"""
updater.py — Self-update from GitHub (Windows / Linux / macOS).

Strategy:
1. Check GitHub API for the latest release tag (or fall back to the latest
   commit on `main`).
2. Compare against local `VERSION` file or the current git SHA, with proper
   pre-release ordering (e.g. 1.0.0-beta1 < 1.0.0).
3. Apply the update via one of two paths:
   a) `git fetch + git reset --hard origin/main` when `.git` exists and
      `git` is on PATH (clean, atomic, easy rollback). Auto-stashes
      uncommitted changes and pops them back on success.
   b) Zip download → staged extract → atomic move when (a) is unavailable.
      The zip path also REMOVES files that were deleted upstream so the
      working tree matches the new release. Backup goes to
      `.update_backup/<timestamp>` so the previous tree can be restored on
      failure.
4. Auto re-install Python deps when `requirements.txt` changed (hash diff).
5. Run idempotent migrations (currently `db.init_db()`).
6. Prune old backups (keep the latest N).
7. Tell the caller to restart — `restart_app()` is provided as a helper.

Concurrency: a module-level lock + a file lock (`.update.lock`) prevent two
update runs from racing each other (e.g. double-click on the GUI button).

User data NEVER touched (whitelist `_USER_DATA`):
    .env, notion_config.json, sync_history.db, icon.png, sync.log,
    streamlit_server.log, .venv/

Public API:
    get_current_version()        → str
    get_remote_version()         → dict | None
    check_for_update()           → dict
    apply_update(strategy?)      → tuple[bool, str]
    rollback(backup_path)        → tuple[bool, str]
    list_backups()               → list[Path]
    prune_backups(keep?)         → int
    restart_app()                → NoReturn
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import NoReturn
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

ROOT          = Path(__file__).parent
VERSION_FILE  = ROOT / "VERSION"
BACKUP_DIR    = ROOT / ".update_backup"
STAGING_DIR   = ROOT / ".update_staging"
LOCK_FILE     = ROOT / ".update.lock"
MANIFEST_FILE = ROOT / ".update_manifest.json"
REPO          = "MoacyrBarata/anke-notion"
BRANCH        = "main"
USER_AGENT    = f"anke-notion-updater/{REPO}"
HTTP_TIMEOUT  = 10  # seconds
ZIP_TIMEOUT   = 60  # seconds
BACKUP_KEEP   = 5   # how many timestamped backups to retain
LOCK_STALE_S  = 30 * 60  # treat lockfiles older than 30min as stale

# Files / dirs the updater MUST NOT overwrite or delete.
_USER_DATA: tuple[str, ...] = (
    ".env",
    "notion_config.json",
    "sync_history.db",
    "sync_history.db-journal",
    "sync_history.db-wal",
    "sync_history.db-shm",
    "icon.png",
    "sync.log",
    "streamlit_server.log",
    ".venv",
    ".update_backup",
    ".update_staging",
    ".update.lock",
    ".update_manifest.json",
    ".git",
    "__pycache__",
)

_lock = threading.Lock()


# ── Version helpers ───────────────────────────────────────────────────────────

def get_current_version() -> str:
    """Returns the local version string. Falls back to git SHA, then 'unknown'."""
    if VERSION_FILE.exists():
        v = VERSION_FILE.read_text(encoding="utf-8").strip()
        if v:
            return v
    sha = _git("rev-parse", "--short", "HEAD")
    return sha or "unknown"


def _parse_semver(s: str) -> tuple[int, int, int, tuple] | None:
    """Returns (major, minor, patch, prerelease_tuple) or None.

    Pre-release ordering follows SemVer 2.0: a version with a pre-release
    sorts BEFORE the same version without one (1.0.0-beta1 < 1.0.0).
    The prerelease_tuple is empty `()` for releases and a tuple of identifiers
    for pre-releases. Empty tuple > non-empty tuple under our compare logic
    (handled in `_is_newer`).
    """
    s = s.lstrip("vV").strip()
    if not s:
        return None
    # Strip build metadata (`+...`) — irrelevant for ordering.
    s = s.split("+", 1)[0]
    core, _, pre = s.partition("-")
    parts = core.split(".")
    if len(parts) < 3:
        return None
    try:
        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None
    pre_tuple: tuple = ()
    if pre:
        ids = []
        for ident in pre.split("."):
            if ident.isdigit():
                ids.append((0, int(ident)))  # numeric ids sort < alpha
            else:
                ids.append((1, ident))
            pre_tuple = tuple(ids)
    return (major, minor, patch, pre_tuple)


def _cmp_semver(a: tuple, b: tuple) -> int:
    """Compare two parsed semvers. Returns -1, 0, 1."""
    am, an, ap, apre = a
    bm, bn, bp, bpre = b
    if (am, an, ap) != (bm, bn, bp):
        return -1 if (am, an, ap) < (bm, bn, bp) else 1
    # Same core. Release > prerelease.
    if not apre and not bpre:
        return 0
    if not apre:
        return 1
    if not bpre:
        return -1
    if apre == bpre:
        return 0
    return -1 if apre < bpre else 1


def _is_newer(remote: str, local: str) -> bool:
    """True iff `remote` semver is strictly greater than `local`. When either
    side is not parseable, falls back to plain string inequality so that
    non-tagged installs (git SHA) still see ANY remote version as newer."""
    rs = _parse_semver(remote)
    ls = _parse_semver(local)
    if rs is not None and ls is not None:
        return _cmp_semver(rs, ls) > 0
    return remote != local and bool(remote)


# ── HTTP / GitHub API ─────────────────────────────────────────────────────────

def _http_json(url: str) -> dict:
    """Fetch JSON. Returns `{"ok": True, "data": ...}` on success or
    `{"ok": False, "kind": <network|ratelimit|http|json>, "msg": "..."}` on
    failure. Caller can distinguish error classes."""
    req = Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept":     "application/vnd.github+json",
    })
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT) as r:
            raw = r.read().decode("utf-8")
    except HTTPError as e:
        kind = "ratelimit" if e.code in (403, 429) else "http"
        return {"ok": False, "kind": kind, "msg": f"HTTP {e.code}"}
    except (URLError, TimeoutError) as e:
        return {"ok": False, "kind": "network", "msg": str(e)}
    try:
        return {"ok": True, "data": json.loads(raw)}
    except json.JSONDecodeError as e:
        return {"ok": False, "kind": "json", "msg": str(e)}


def get_remote_version() -> dict | None:
    """Tries `releases/latest` first, falls back to `commits/main`.
    Returns dict {version, sha, url, notes, source} or None on network error."""
    rel = _http_json(f"https://api.github.com/repos/{REPO}/releases/latest")
    if rel.get("ok"):
        data = rel["data"]
        if isinstance(data, dict) and data.get("tag_name"):
            return {
                "version": data["tag_name"],
                "sha":     None,
                "url":     data.get("html_url"),
                "notes":   (data.get("body") or "").strip(),
                "source":  "release",
            }
    commit = _http_json(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}")
    if commit.get("ok"):
        data = commit["data"]
        if isinstance(data, dict) and data.get("sha"):
            sha = data["sha"]
            msg = (data.get("commit", {}).get("message") or "").strip().splitlines()[:1]
            return {
                "version": sha[:7],
                "sha":     sha,
                "url":     data.get("html_url"),
                "notes":   msg[0] if msg else "",
                "source":  "commit",
            }
    return None


def check_for_update() -> dict:
    """Combines current + remote info. `has_update` is False on network
    error — caller should inspect `error` and `error_kind`."""
    current = get_current_version()
    rel = _http_json(f"https://api.github.com/repos/{REPO}/releases/latest")
    commit_resp = None
    remote: dict | None = None
    if rel.get("ok") and isinstance(rel["data"], dict) and rel["data"].get("tag_name"):
        d = rel["data"]
        remote = {
            "version": d["tag_name"], "sha": None,
            "url": d.get("html_url"),
            "notes": (d.get("body") or "").strip(),
            "source": "release",
        }
    else:
        commit_resp = _http_json(
            f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
        )
        if (commit_resp.get("ok")
                and isinstance(commit_resp["data"], dict)
                and commit_resp["data"].get("sha")):
            d = commit_resp["data"]
            sha = d["sha"]
            msg = (d.get("commit", {}).get("message") or "").strip().splitlines()[:1]
            remote = {
                "version": sha[:7], "sha": sha,
                "url": d.get("html_url"),
                "notes": msg[0] if msg else "",
                "source": "commit",
            }
    if remote is None:
        kind = (commit_resp or rel).get("kind", "network")
        msg_map = {
            "ratelimit": "Limite da API do GitHub atingido. Tente em alguns minutos ou defina GITHUB_TOKEN.",
            "network":   "Sem conexão com github.com.",
            "http":      "GitHub respondeu com erro HTTP.",
            "json":      "Resposta inválida do GitHub.",
        }
        return {
            "current":    current,
            "latest":     None,
            "has_update": False,
            "error":      msg_map.get(kind, "Falha desconhecida ao consultar GitHub."),
            "error_kind": kind,
        }
    return {
        "current":    current,
        "latest":     remote["version"],
        "remote":     remote,
        "has_update": _is_newer(remote["version"], current),
        "error":      None,
        "error_kind": None,
    }


# ── Git availability ──────────────────────────────────────────────────────────

def _git(*args: str, cwd: Path | None = None) -> str:
    """Runs `git <args>` and returns stdout (stripped). '' on failure."""
    if shutil.which("git") is None:
        return ""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(cwd or ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if out.returncode != 0:
        return ""
    return out.stdout.strip()


def _git_available() -> bool:
    return shutil.which("git") is not None and (ROOT / ".git").exists()


def _git_is_clean() -> bool:
    """Returns True iff there are no uncommitted changes."""
    if not _git_available():
        return True
    return _git("status", "--porcelain") == ""


# ── Lock ──────────────────────────────────────────────────────────────────────

def _acquire_lock() -> tuple[bool, str]:
    """File-based lock so concurrent processes (or rogue double-click in GUI)
    can't both write to the same tree. Stale locks are auto-released."""
    if LOCK_FILE.exists():
        try:
            age = time.time() - LOCK_FILE.stat().st_mtime
        except OSError:
            age = 0
        if age < LOCK_STALE_S:
            return False, "Outra atualização em andamento. Aguarde."
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass
    try:
        LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except OSError as e:
        return False, f"Falha ao criar lock: {e}"
    return True, ""


def _release_lock() -> None:
    try:
        LOCK_FILE.unlink()
    except OSError:
        pass


# ── Update — git path ─────────────────────────────────────────────────────────

def _apply_update_git() -> tuple[bool, str]:
    if not _git_available():
        return False, "Git não disponível ou repo não-git."

    pre_sha = _git("rev-parse", "HEAD")
    if not pre_sha:
        return False, "Falha ao ler SHA atual."

    stashed = False
    if not _git_is_clean():
        # Auto-stash so the user doesn't get blocked.
        out = subprocess.run(
            ["git", "stash", "push", "-u", "-m", "anke-notion auto-update"],
            cwd=str(ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60,
        )
        if out.returncode != 0:
            return False, ("Há alterações locais não commitadas e o stash "
                           f"automático falhou: {out.stderr.strip()[:200]}")
        stashed = True

    if not _git("fetch", "origin", BRANCH):
        if stashed:
            _git("stash", "pop")
        return False, "Falha em `git fetch origin`."

    new_sha = _git("rev-parse", f"origin/{BRANCH}")
    if not new_sha:
        if stashed:
            _git("stash", "pop")
        return False, f"Falha ao resolver origin/{BRANCH}."

    if new_sha == pre_sha:
        if stashed:
            _git("stash", "pop")
        return True, "Já está na última versão."

    out = subprocess.run(
        ["git", "reset", "--hard", new_sha],
        cwd=str(ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60,
    )
    if out.returncode != 0:
        if stashed:
            _git("stash", "pop")
        return False, f"git reset falhou: {out.stderr.strip()[:200]}"

    suffix = " (stash restaurado)" if stashed and _git("stash", "pop") else ""
    return True, f"git: {pre_sha[:7]} → {new_sha[:7]}{suffix}"


# ── Update — zip path ─────────────────────────────────────────────────────────

def _zip_url(ref: str = BRANCH) -> str:
    return f"https://codeload.github.com/{REPO}/zip/refs/heads/{ref}"


def _download_zip(url: str) -> bytes | None:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=ZIP_TIMEOUT) as r:
            return r.read()
    except (URLError, HTTPError, TimeoutError):
        return None


def _is_user_data(rel_path: str) -> bool:
    head = rel_path.replace("\\", "/").split("/", 1)[0]
    return head in _USER_DATA


def _backup_dir() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return BACKUP_DIR / ts


def _backup_tree(dest: Path) -> None:
    """Copies the current project tree to `dest`, skipping user data and
    other transient dirs."""
    dest.mkdir(parents=True, exist_ok=True)
    for entry in ROOT.iterdir():
        if entry.name in _USER_DATA:
            continue
        try:
            target = dest / entry.name
            if entry.is_dir():
                shutil.copytree(entry, target,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                shutil.copy2(entry, target)
        except Exception:
            continue


def _restore_tree(src: Path) -> None:
    """Mirror of _backup_tree — copy back saved files into ROOT, skipping
    user data so we never overwrite user state during rollback."""
    for entry in src.iterdir():
        if entry.name in _USER_DATA:
            continue
        target = ROOT / entry.name
        try:
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            if entry.is_dir():
                shutil.copytree(entry, target)
            else:
                shutil.copy2(entry, target)
        except Exception:
            continue


def _hash_file(p: Path) -> str:
    if not p.exists():
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _validate_zip(raw: bytes) -> tuple[zipfile.ZipFile | None, str, str]:
    """Validate the downloaded zip and return (zipfile, top_dir, error).
    `top_dir` is the leading directory all entries share (e.g. anke-notion-main).
    Errors are caught early so we don't trash the tree on a half-baked zip."""
    if not raw:
        return None, "", "zip vazio."
    try:
        zf = zipfile.ZipFile(BytesIO(raw))
    except zipfile.BadZipFile as e:
        return None, "", f"zip inválido: {e}"
    names = zf.namelist()
    if not names:
        return None, "", "zip sem entradas."
    top = names[0].split("/", 1)[0]
    expected = f"{REPO.split('/', 1)[1]}-"
    if not top.startswith(expected):
        return None, "", f"prefixo do zip inesperado: {top!r}"
    for n in names:
        # Prevent path traversal — every entry must live under `top/`.
        if n != top and not n.startswith(top + "/"):
            return None, "", f"entrada fora do diretório raiz: {n!r}"
        if "..\\" in n or "../" in n or n.startswith("/"):
            return None, "", f"path traversal detectado: {n!r}"
    return zf, top, ""


def _extract_to_staging(zf: zipfile.ZipFile, top: str) -> tuple[Path | None, str]:
    """Extract repo contents into a clean staging dir. Returns (path, error)."""
    if STAGING_DIR.exists():
        try:
            shutil.rmtree(STAGING_DIR)
        except Exception as e:
            return None, f"falha ao limpar staging: {e}"
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    try:
        for member in zf.namelist():
            if member == top + "/" or member == top:
                continue
            rel = (member[len(top) + 1:]
                   if member.startswith(top + "/") else member)
            if not rel:
                continue
            target = STAGING_DIR / rel
            if member.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
    except Exception as e:
        try:
            shutil.rmtree(STAGING_DIR, ignore_errors=True)
        except Exception:
            pass
        return None, f"falha ao extrair zip: {e}"
    return STAGING_DIR, ""


def _swap_from_staging(staging: Path) -> tuple[bool, str, set[str]]:
    """Move staged files into ROOT, then delete files that exist locally
    but are absent from the new release (excluding user data).

    Returns (ok, error, new_paths) where new_paths is the set of repo-relative
    paths shipped by the release (used for the delete-pass)."""
    new_paths: set[str] = set()
    try:
        for entry in staging.rglob("*"):
            rel = entry.relative_to(staging).as_posix()
            new_paths.add(rel)
            if entry.is_dir():
                continue
            target = ROOT / rel
            if _is_user_data(rel):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            # Atomic replace — works on Windows with file open in another
            # process only if no handle is held.
            os.replace(entry, target)
    except Exception as e:
        return False, f"falha ao trocar arquivos: {e}", new_paths
    return True, "", new_paths


def _read_manifest() -> set[str]:
    """Read the set of repo-relative paths shipped by the LAST successful
    update. Empty set if no manifest exists yet (first run on this tree)."""
    if not MANIFEST_FILE.exists():
        return set()
    try:
        data = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, list):
        return set()
    return {str(p) for p in files}


def _write_manifest(new_paths: set[str]) -> None:
    """Persist the list of files we just shipped so the NEXT update can
    delete only files we previously installed (not user-added ones)."""
    files = sorted(p for p in new_paths if p)
    payload = {
        "version":  get_current_version(),
        "shipped_at": datetime.now(timezone.utc).isoformat(),
        "files":    files,
    }
    try:
        MANIFEST_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _delete_orphans(new_paths: set[str]) -> int:
    """Delete files that we shipped previously but are absent from the new
    release. Files NOT listed in the prior manifest are user-added and
    preserved. First-run (no manifest) is a no-op."""
    prior = _read_manifest()
    if not prior:
        return 0
    new_files = {p for p in new_paths if p}
    deleted = 0
    for rel in prior - new_files:
        if _is_user_data(rel):
            continue
        target = ROOT / rel
        if not target.exists() or not target.is_file():
            continue
        try:
            target.unlink()
            deleted += 1
        except OSError:
            continue
    return deleted


def _apply_update_zip() -> tuple[bool, str]:
    raw = _download_zip(_zip_url())
    if raw is None:
        return False, "Falha ao baixar zip do GitHub."

    zf, top, err = _validate_zip(raw)
    if zf is None:
        return False, f"Validação falhou: {err}"

    backup = _backup_dir()
    _backup_tree(backup)

    staging, err = _extract_to_staging(zf, top)
    zf.close()
    if staging is None:
        _restore_tree(backup)
        return False, f"{err}. Backup restaurado."

    ok, err, new_paths = _swap_from_staging(staging)
    if not ok:
        _restore_tree(backup)
        try:
            shutil.rmtree(staging, ignore_errors=True)
        except Exception:
            pass
        return False, f"{err}. Backup restaurado."

    deleted = _delete_orphans(new_paths)
    _write_manifest(new_paths)
    try:
        shutil.rmtree(staging, ignore_errors=True)
    except Exception:
        pass

    suffix = f" · removidos {deleted}" if deleted else ""
    return True, f"zip: backup em {backup.relative_to(ROOT)}{suffix}"


# ── Public apply / rollback ───────────────────────────────────────────────────

def apply_update(strategy: str = "auto") -> tuple[bool, str]:
    """Runs the update pipeline. Returns (ok, message).

    `strategy`:  'auto' | 'git' | 'zip'

    Locks across threads AND processes so a double-click can't race.
    """
    if not _lock.acquire(blocking=False):
        return False, "Atualização já em andamento."
    try:
        ok, msg = _acquire_lock()
        if not ok:
            return False, msg
        try:
            return _apply_update_inner(strategy)
        finally:
            _release_lock()
    finally:
        _lock.release()


def _apply_update_inner(strategy: str) -> tuple[bool, str]:
    pre_req_hash = _hash_file(ROOT / "requirements.txt")

    if strategy == "git":
        ok, msg = _apply_update_git()
    elif strategy == "zip":
        ok, msg = _apply_update_zip()
    else:  # auto
        if _git_available():
            ok, msg = _apply_update_git()
            if not ok and "Já está" not in msg:
                ok2, msg2 = _apply_update_zip()
                if ok2:
                    ok, msg = True, f"git falhou ({msg}); zip ok ({msg2})"
        else:
            ok, msg = _apply_update_zip()

    if ok:
        post_msg = _post_update_migrations(pre_req_hash)
        prune_backups(BACKUP_KEEP)
        if post_msg:
            return True, f"{msg} | {post_msg}"
    return ok, msg


def _post_update_migrations(pre_req_hash: str) -> str:
    """Runs idempotent migrations after a successful file update.

    - Reinstalls Python deps if requirements.txt changed.
    - Ensures sqlite schema is up-to-date.
    """
    bits: list[str] = []

    post_req_hash = _hash_file(ROOT / "requirements.txt")
    if pre_req_hash and post_req_hash and pre_req_hash != post_req_hash:
        ok, msg = _reinstall_deps()
        bits.append(f"deps {'OK' if ok else 'falhou'}: {msg}" if msg else
                    ("deps OK" if ok else "deps falhou"))

    try:
        import db as sync_db
        sync_db.init_db()
        bits.append("DB OK")
    except Exception as exc:
        bits.append(f"DB falhou: {exc}")
    return " · ".join(bits)


def _venv_python() -> Path | None:
    """Return path to venv python if a `.venv` exists next to ROOT."""
    venv = ROOT / ".venv"
    if not venv.exists():
        return None
    if sys.platform == "win32":
        candidate = venv / "Scripts" / "python.exe"
    else:
        candidate = venv / "bin" / "python"
    return candidate if candidate.exists() else None


def _reinstall_deps() -> tuple[bool, str]:
    """Run `pip install -r requirements.txt` against the venv (or current
    interpreter as a fallback)."""
    py = _venv_python() or Path(sys.executable)
    req = ROOT / "requirements.txt"
    if not req.exists():
        return True, "sem requirements.txt"
    try:
        out = subprocess.run(
            [str(py), "-m", "pip", "install", "-r", str(req),
             "--disable-pip-version-check"],
            cwd=str(ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"pip subprocess falhou: {e}"
    if out.returncode != 0:
        tail = (out.stderr or out.stdout or "").strip().splitlines()[-1:]
        return False, tail[0] if tail else "pip retornou erro"
    return True, "pip ok"


def rollback(backup_path: Path) -> tuple[bool, str]:
    """Restores files from a previous backup directory."""
    backup_path = Path(backup_path)
    if not backup_path.exists():
        return False, f"Backup não encontrado: {backup_path}"
    try:
        _restore_tree(backup_path)
    except Exception as exc:
        return False, f"Rollback falhou: {exc}"
    return True, f"Rollback aplicado de {backup_path}"


def list_backups() -> list[Path]:
    if not BACKUP_DIR.exists():
        return []
    return sorted([p for p in BACKUP_DIR.iterdir() if p.is_dir()],
                  reverse=True)


def prune_backups(keep: int = BACKUP_KEEP) -> int:
    """Delete oldest backups, keeping the newest `keep`. Returns count removed."""
    backups = list_backups()
    removed = 0
    for old in backups[keep:]:
        try:
            shutil.rmtree(old)
            removed += 1
        except OSError:
            continue
    return removed


# ── Restart ───────────────────────────────────────────────────────────────────

def restart_app() -> NoReturn:
    """Re-exec the launcher so the running process picks up the new code.

    Caller should ensure the UI has flushed any pending state before calling.
    On Windows we spawn a fresh process and exit so file handles to .py files
    held by the old import system don't bleed into the child.
    """
    launcher = ROOT / "launcher.py"
    py = _venv_python() or Path(sys.executable)
    args = [str(py), str(launcher)] if launcher.exists() else [str(py), *sys.argv]
    if sys.platform == "win32":
        subprocess.Popen(args, cwd=str(ROOT), close_fds=True)
        sys.exit(0)
    else:
        os.execv(str(py), args)
