"""
notion_helpers.py — Notion API helpers, field-suggestion and sample-page logic.
"""
import requests

try:
    from notion_client import Client as NotionClient
    NOTION_CLIENT_AVAILABLE = True
except ImportError:
    NotionClient = None
    NOTION_CLIENT_AVAILABLE = False

_NOTION_VERSION = "2025-09-03"


def _notion_get(token: str, path: str) -> dict:
    r = requests.get(
        f"https://api.notion.com/v1{path}",
        headers={"Authorization": f"Bearer {token}",
                 "Notion-Version": _NOTION_VERSION},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def list_notion_databases(token: str, on_progress=None):
    """Returns (list_of_dbs, error_msg). error_msg is None on success."""
    def prog(msg):
        if on_progress:
            on_progress(msg)

    if not NOTION_CLIENT_AVAILABLE:
        return [], "notion-client não instalado"
    if not token:
        return [], "Token não informado"
    try:
        client = NotionClient(auth=token)

        # Primary: search API (databases explicitly connected)
        prog("🔍 Buscando tabelas conectadas à integração...")
        db_map, cursor = {}, None
        while True:
            kwargs = {"filter": {"property": "object", "value": "data_source"},
                      "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = client.search(**kwargs)
            for db in resp.get("results", []):
                title = (db.get("title") or [{}])[0].get("plain_text", db["id"][:8])
                prog(f"📋 Tabela encontrada: {title}")
                db_map[db["id"]] = db
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")

        # Fallback: discover parent databases via accessible pages
        # Notion bug: connecting child pages doesn't always index the parent DB
        # in /search, but direct retrieval still works.
        prog("📄 Verificando páginas para descobrir tabelas adicionais...")
        page_cursor, page_count = None, 0
        while True:
            kwargs = {"filter": {"property": "object", "value": "page"},
                      "page_size": 100}
            if page_cursor:
                kwargs["start_cursor"] = page_cursor
            resp = client.search(**kwargs)
            results = resp.get("results", [])
            page_count += len(results)
            if results:
                prog(f"📄 {page_count} página(s) analisada(s)...")
            for page in results:
                p_title = ""
                try:
                    p_title = (page.get("properties", {}).get(
                        next(iter(page.get("properties", {})), ""), {}
                    ).get("title") or [{}])[0].get("plain_text", "")
                except Exception:
                    pass
                parent = page.get("parent", {})
                db_id  = parent.get("database_id") or parent.get("data_source_id")
                ptype  = parent.get("type")
                if ptype in ("database_id", "data_source_id") and db_id:
                    if db_id not in db_map:
                        if p_title:
                            prog(f"🔎 Encontrado em \"{p_title}\", buscando tabela...")
                        try:
                            if ptype == "data_source_id":
                                db = _notion_get(token, f"/data_sources/{db_id}")
                                db_title = (db.get("title") or [{}])[0].get("plain_text", db_id[:8])
                                prog(f"✅ Tabela descoberta: {db_title}")
                                db_map[db_id] = db
                            else:
                                # parent é database_id legacy → resolver data_sources
                                db = _notion_get(token, f"/databases/{db_id}")
                                for src in (db.get("data_sources") or []):
                                    if src["id"] in db_map:
                                        continue
                                    src_full = _notion_get(token, f"/data_sources/{src['id']}")
                                    src_title = (src_full.get("title") or [{}])[0].get("plain_text") or src.get("name") or src["id"][:8]
                                    prog(f"✅ Tabela descoberta: {src_title}")
                                    db_map[src["id"]] = src_full
                        except Exception:
                            pass
            if not resp.get("has_more"):
                break
            page_cursor = resp.get("next_cursor")

        prog(f"✔ Busca concluída — {len(db_map)} tabela(s) encontrada(s)")
        return list(db_map.values()), None
    except Exception as exc:
        msg = str(exc)
        if "401" in msg or "unauthorized" in msg.lower():
            return [], "Token inválido ou expirado (401)"
        if "403" in msg or "forbidden" in msg.lower():
            return [], "Sem permissão (403) — verifique o token"
        return [], f"Erro: {msg[:120]}"


def get_database_properties(token: str, db_id: str) -> dict:
    """db_id agora é tratado como data_source_id (Notion API 2025-09-03)."""
    if not token or not db_id:
        return {}
    try:
        return _notion_get(token, f"/data_sources/{db_id}").get("properties", {})
    except Exception:
        # Fallback: pode ser um database_id legacy.
        try:
            db = _notion_get(token, f"/databases/{db_id}")
            sources = db.get("data_sources") or []
            if sources:
                return _notion_get(token, f"/data_sources/{sources[0]['id']}").get("properties", {})
        except Exception:
            pass
        return {}


def get_db_title(db: dict) -> str:
    try:
        return db["title"][0]["plain_text"]
    except (KeyError, IndexError):
        return db.get("id", "—")[:8]


def props_by_type(props: dict, *types: str) -> list:
    return [k for k, v in props.items() if v["type"] in types]


def suggest_fields(props: dict) -> dict:
    """Keyword + type heuristics to pre-fill field dropdowns."""
    _TITLE_KW   = {"título", "title", "nome", "name", "ideia", "aula", "item", "tópico"}
    _CONTENT_KW = {"conteúdo", "content", "resumo", "summary", "notas", "notes",
                   "anotações", "texto", "descrição", "description", "corpo", "body"}
    _DATE_KW    = {"data", "date", "dia", "quando", "when", "criado", "created"}
    _SYNC_KW    = {"sync", "sincronizado", "anki", "sincronizar", "exportado"}
    _STATUS_KW  = {"status", "estado", "pronto", "completo", "done", "situação"}

    def score(name, keywords):
        n = name.lower()
        return any(k in n for k in keywords)

    hints = {}

    for name, meta in props.items():
        if meta.get("type") == "title":
            hints["title"] = name
            break

    for name, meta in props.items():
        if meta.get("type") == "rich_text" and score(name, _CONTENT_KW):
            hints["content"] = name
            break
    if "content" not in hints:
        for name, meta in props.items():
            if meta.get("type") == "rich_text":
                hints["content"] = name
                break

    for name, meta in props.items():
        if meta.get("type") in ("date", "created_time", "last_edited_time") and score(name, _DATE_KW):
            hints["date"] = name
            break
    if "date" not in hints:
        for name, meta in props.items():
            if meta.get("type") in ("date", "created_time", "last_edited_time"):
                hints["date"] = name
                break

    for name, meta in props.items():
        if meta.get("type") in ("select", "status") and score(name, _SYNC_KW):
            hints["sync"] = name
            break

    for name, meta in props.items():
        if meta.get("type") in ("select", "status") and score(name, _STATUS_KW):
            if name != hints.get("sync"):
                hints["status"] = name
                break

    return hints


def get_sample_page(token: str, db_id: str) -> dict | None:
    """db_id é tratado como data_source_id."""
    if not token or not db_id:
        return None
    try:
        r = requests.post(
            f"https://api.notion.com/v1/data_sources/{db_id}/query",
            headers={"Authorization": f"Bearer {token}",
                     "Notion-Version": _NOTION_VERSION},
            json={"page_size": 1},
            timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        return results[0] if results else None
    except Exception:
        return None


def extract_prop_text(page: dict, prop_name: str) -> str:
    if not page or not prop_name:
        return ""
    prop  = page.get("properties", {}).get(prop_name, {})
    ptype = prop.get("type", "")
    try:
        if ptype in ("title", "rich_text"):
            return "".join(r["plain_text"] for r in prop.get(ptype, []))
        if ptype == "select":
            return prop["select"]["name"] if prop.get("select") else ""
        if ptype == "status":
            return prop["status"]["name"] if prop.get("status") else ""
        if ptype == "multi_select":
            return ", ".join(o["name"] for o in prop.get("multi_select", []))
        if ptype == "date":
            return prop["date"]["start"] if prop.get("date") else ""
        if ptype == "created_time":
            return prop.get("created_time", "")[:10]
        if ptype == "last_edited_time":
            return prop.get("last_edited_time", "")[:10]
        if ptype == "number":
            return str(prop.get("number", ""))
    except Exception:
        pass
    return ""
