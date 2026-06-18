import json
import logging
import re
import shutil
import time
from pathlib import Path

from flask import current_app

from app.config import DATA_DIR


logger = logging.getLogger(__name__)

# Safe identifier for compose_session_id and file_id. Rejects path separators,
# dots, and anything that could escape the staging tree.
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")

_DEFAULT_DIR = str(DATA_DIR / "mail_attachments")


def _attachments_dir() -> Path:
    try:
        path = current_app.config.get("MAIL_ATTACHMENTS_DIR", _DEFAULT_DIR)
    except RuntimeError:
        path = _DEFAULT_DIR
    base = Path(path)
    base.mkdir(parents=True, exist_ok=True)
    return base


def is_valid_id(value):
    return bool(value) and SAFE_ID_RE.match(value) is not None


def _session_dir(user_id, compose_session_id):
    return _attachments_dir() / str(user_id) / compose_session_id


def _file_dir(user_id, compose_session_id, file_id):
    path = _session_dir(user_id, compose_session_id) / file_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _content_path(user_id, compose_session_id, file_id):
    return _file_dir(user_id, compose_session_id, file_id) / "content"


def _meta_path(user_id, compose_session_id, file_id):
    return _file_dir(user_id, compose_session_id, file_id) / "meta.json"


def stage_file(user_id, compose_session_id, file_id, data, name, mime):
    raw = data if isinstance(data, bytes) else data.read()
    size = len(raw)
    _content_path(user_id, compose_session_id, file_id).write_bytes(raw)
    meta = {"name": name, "mime": mime or "application/octet-stream", "size": size}
    _meta_path(user_id, compose_session_id, file_id).write_text(
        json.dumps(meta, ensure_ascii=False)
    )
    return size


def read_meta(user_id, compose_session_id, file_id):
    path = _meta_path(user_id, compose_session_id, file_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def read_bytes(user_id, compose_session_id, file_id):
    path = _content_path(user_id, compose_session_id, file_id)
    if not path.exists():
        return None
    return path.read_bytes()


def list_staged(user_id, compose_session_id):
    session_path = _session_dir(user_id, compose_session_id)
    if not session_path.exists():
        return []
    items = []
    for child in session_path.iterdir():
        if not child.is_dir():
            continue
        file_id = child.name
        meta = read_meta(user_id, compose_session_id, file_id)
        if not meta:
            continue
        items.append({
            "id": file_id,
            "name": meta.get("name", file_id),
            "mime": meta.get("mime", "application/octet-stream"),
            "size": meta.get("size", 0),
        })
    return items


def session_size(user_id, compose_session_id):
    return sum(item["size"] for item in list_staged(user_id, compose_session_id))


def delete_staged(user_id, compose_session_id, file_id):
    path = _file_dir(user_id, compose_session_id, file_id)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    _remove_session_if_empty(user_id, compose_session_id)


def delete_session(user_id, compose_session_id):
    path = _session_dir(user_id, compose_session_id)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _remove_session_if_empty(user_id, compose_session_id):
    session_path = _session_dir(user_id, compose_session_id)
    try:
        if session_path.exists() and not any(session_path.iterdir()):
            session_path.rmdir()
    except OSError:
        pass


def cleanup_stale(ttl_hours):
    """Remove session directories older than ttl_hours across all users."""
    try:
        ttl_seconds = int(ttl_hours) * 3600
        now = time.time()
        base = _attachments_dir()
        if not base.exists():
            return 0
        removed = 0
        for user_dir in base.iterdir():
            if not user_dir.is_dir():
                continue
            for session_dir in list(user_dir.iterdir()):
                if not session_dir.is_dir():
                    continue
                try:
                    mtime = session_dir.stat().st_mtime
                except OSError:
                    continue
                if now - mtime > ttl_seconds:
                    shutil.rmtree(session_dir, ignore_errors=True)
                    removed += 1
            try:
                if user_dir.exists() and not any(user_dir.iterdir()):
                    user_dir.rmdir()
            except OSError:
                pass
        return removed
    except Exception:
        logger.warning("attachment staging cleanup failed", exc_info=True)
        return 0
