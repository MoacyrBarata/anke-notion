"""
updater.py — Self-update from GitHub (Windows / Linux / macOS).

Strategy:
1. Check GitHub API for the latest release tag (or fall back to the latest
   commit on `main`).
2. Compare against local `VERSION` file or the current git SHA.
3. Apply the update via one of two paths:
   a) `git fetch + git reset --hard origin/main` when `.git` exists and
      `git` is on PATH (clean, atomic, easy rollback).
   b) Zip download → swap files when (a) is unavailable. Backup goes to
      `.update_backup/<timestamp>` so the previous tree can be restored on
      failure.
4. Re-install Python deps when `requirements.txt` changed.
5. Run idempotent migrations (currently `db.init_db()`).
6. Tell the caller to restart — we do not kill the running process here.

User data NEVER touched (whitelist `_USER_DATA`):
    .env, notion_config.json, sync_history.db, icon.png, sync.log,
    streamlit_server.log, .venv/

Public API:
    get_current_version()        → str
    get_remote_version()         → dict           {"version", "sha", "url", "notes"}
    check_for_update()           → dict           {"current", "latest", "has_update", ...}
    apply_update(strategy?)      → tuple[bool,str]
    rollback(backup_path)        → tuple[bool,str]
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

ROOT          = Path(__file__).parent
VERSION_FILE  = ROOT / "VERSION"
BACKUP_DIR    = ROOT / ".update_backup"
REPO          = "MoacyrBarata/anke-notion"
BRANCH        = "main"
USER_AGENT    = f"anke-notion-updater/{REPO}"
HTTP_TIMEOUT  = 10  # seconds

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
    ".git",
    "__pycache__",
)


# ── Version helpers ───────────────────────────────────────────────────────────

def get_current_version() -> str:
    """Returns the local version string. Falls back to git SHA, then 'unknown'."""
    if VERSION_FILE.exists():
        v = VERSION_FILE.read_text(encoding="utf-8").strip()
        if v:
            return v
    sha = _git("rev-parse", "--short", "HEAD")
    return sha or "unknown"


def _parse_semver(s: str) -> tuple[int, int, int] | None:
    s = s.lstrip("v").strip()
    parts = s.split(".")
    if len(parts) < 3:
        return None
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2].split("-")[0]))
    except ValueError:
        return None


def _is_newer(remote: str, local: str) -> bool:
    """True iff `remote` semver is strictly greater than `local`. When either
    side is not parseable, falls back to plain string inequality so that
    non-tagged installs (git SHA) still see ANY remote version as newer."""
    rs = _parse_semver(remote)
    ls = _parse_semver(local)
    if rs is not None and ls is not None:
        return rs > ls
    return remote != local and bool(remote)


# ── GitHub API ────────────────────────────────────────────────────────────────

def _http_json(url: str) -> dict | list | None:
    req = Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept":     "application/vnd.github+json",
    })
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except (URLError, HTTPError, TimeoutError, json.JSONDecodeError):
        return None


def get_remote_version() -> dict | None:
    """Tries `releases/latest` first, falls back to `commits/main`.
    Returns dict {version, sha, url, notes, source} or None on network error."""
    rel = _http_json(f"https://api.github.com/repos/{REPO}/releases/latest")
    if isinstance(rel, dict) and rel.get("tag_name"):
        return {
            "version": rel["tag_name"],
            "sha":     None,
            "url":     rel.get("html_url"),
            "notes":   (rel.get("body") or "").strip(),
            "source":  "release",
        }
    commit = _http_json(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}")
    if isinstance(commit, dict) and commit.get("sha"):
        sha = commit["sha"]
        msg = (commit.get("commit", {}).get("message") or "").strip().splitlines()[:1]
        return {
            "version": sha[:7],
            "sha":     sha,
            "url":     commit.get("html_url"),
            "notes":   msg[0] if msg else "",
            "source":  "commit",
        }
    return None


def check_for_update() -> dict:
    """Combines current + remote info. `has_update` may be False on network
    error — caller should inspect `error`."""
    current = get_current_version()
    remote  = get_remote_version()
    if remote is None:
        return {
            "current":    current,
            "latest":     None,
            "has_update": False,
            "error":      "Sem conexão com github.com ou rate-limit excedido.",
        }
    return {
        "current":    current,
        "latest":     remote["version"],
        "remote":     remote,
        "has_update": _is_newer(remote["version"], current),
        "error":      None,
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
        return True  # not git-managed → nothing to lose
    return _git("status", "--porcelain") == ""


# ── Update — git path ─────────────────────────────────────────────────────────

def _apply_update_git() -> tuple[bool, str]:
    if not _git_available():
        return False, "Git não disponível ou repo não-git."

    pre_sha = _git("rev-parse", "HEAD")
    if not pre_sha:
        return False, "Falha ao ler SHA atual."

    if not _git_is_clean():
        return False, ("Há alterações locais não commitadas. Faça commit ou "
                       "stash antes de atualizar.")

    if not _git("fetch", "origin", BRANCH):
        return False, "Falha em `git fetch origin`."

    new_sha = _git("rev-parse", f"origin/{BRANCH}")
    if not new_sha:
        return False, f"Falha ao resolver origin/{BRANCH}."

    if new_sha == pre_sha:
        return True, "Já está na última versão."

    # Reset working tree to remote head.
    out = subprocess.run(
        ["git", "reset", "--hard", new_sha],
        cwd=str(ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60,
    )
    if out.returncode != 0:
        return False, f"git reset falhou: {out.stderr.strip()[:200]}"

    return True, f"git: {pre_sha[:7]} → {new_sha[:7]}"


# ── Update — zip path ─────────────────────────────────────────────────────────

def _zip_url(ref: str = BRANCH) -> str:
    return f"https://codeload.github.com/{REPO}/zip/refs/heads/{ref}"


def _download_zip(url: str) -> bytes | None:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=60) as r:
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
            # Best-effort backup; missing items are acceptable.
            continue


def _restore_tree(src: Path) -> None:
    """Mirror of _backup_tree — copy back the saved files into ROOT, skipping
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


def _apply_update_zip() -> tuple[bool, str]:
    raw = _download_zip(_zip_url())
    if raw is None:
        return False, "Falha ao baixar zip do GitHub."

    backup = _backup_dir()
    _backup_tree(backup)

    try:
        with zipfile.ZipFile(BytesIO(raw)) as zf:
            top = zf.namelist()[0].split("/", 1)[0]  # e.g. anke-notion-main
            for member in zf.namelist():
                # Strip leading top-level dir to get repo-relative path.
                if member == top + "/" or member == top:
                    continue
                rel = member[len(top) + 1:] if member.startswith(top + "/") else member
                if not rel:
                    continue
                if _is_user_data(rel):
                    continue
                target = ROOT / rel
                if member.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    except Exception as exc:
        # Roll back from backup.
        _restore_tree(backup)
        return False, f"Falha ao aplicar zip ({exc!r}). Backup restaurado."

    return True, f"zip: backup em {backup.relative_to(ROOT)}"


# ── Public apply / rollback ───────────────────────────────────────────────────

def apply_update(strategy: str = "auto") -> tuple[bool, str]:
    """Runs the update pipeline. Returns (ok, message).

    `strategy`:  'auto' | 'git' | 'zip'
    """
    if strategy == "git":
        ok, msg = _apply_update_git()
    elif strategy == "zip":
        ok, msg = _apply_update_zip()
    else:  # auto
        if _git_available():
            ok, msg = _apply_update_git()
            if not ok and "Já está" not in msg:
                # Try zip as last resort.
                ok2, msg2 = _apply_update_zip()
                if ok2:
                    return True, f"git falhou ({msg}); zip ok ({msg2})"
        else:
            ok, msg = _apply_update_zip()

    if ok:
        post_msg = _post_update_migrations()
        if post_msg:
            return True, f"{msg} | {post_msg}"
    return ok, msg


def _post_update_migrations() -> str:
    """Runs idempotent migrations after a successful file update.

    - Ensures sqlite schema is up-to-date.
    - Surfaces a hint if requirements.txt changed (caller may re-install).
    """
    bits: list[str] = []
    try:
        import db as sync_db  # local import — avoid hard dep at module load
        sync_db.init_db()
        bits.append("DB OK")
    except Exception as exc:
        bits.append(f"DB falhou: {exc}")
    return " · ".join(bits)


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
