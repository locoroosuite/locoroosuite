from pathlib import Path

from flask import current_app

from app.config import DATA_DIR


def get_docs_dir() -> Path:
    try:
        path = current_app.config.get("DOCS_DIR", str(DATA_DIR / "docs"))
    except RuntimeError:
        path = str(DATA_DIR / "docs")
    docs_dir = Path(path)
    docs_dir.mkdir(parents=True, exist_ok=True)
    return docs_dir


def _doc_dir(user_id, account_id, doc_id):
    doc_path = get_docs_dir() / str(user_id) / str(account_id) / doc_id
    doc_path.mkdir(parents=True, exist_ok=True)
    return doc_path


def _storage_path(user_id, account_id, doc_id):
    return _doc_dir(user_id, account_id, doc_id) / "content"


def write_file(user_id, account_id, doc_id, data):
    path = _storage_path(user_id, account_id, doc_id)
    raw = data if isinstance(data, bytes) else data.read()
    path.write_bytes(raw)
    return len(raw)


def read_file(user_id, account_id, doc_id):
    path = _storage_path(user_id, account_id, doc_id)
    if not path.exists():
        return None
    return path.read_bytes()


def file_exists(user_id, account_id, doc_id):
    return _storage_path(user_id, account_id, doc_id).exists()


def delete_file(user_id, account_id, doc_id):
    path = _storage_path(user_id, account_id, doc_id)
    if path.exists():
        path.unlink()
    sidecar = _sidecar_path(user_id, account_id, doc_id)
    if sidecar.exists():
        sidecar.unlink()
    parent = path.parent
    try:
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


def _sidecar_path(user_id, account_id, doc_id):
    return _doc_dir(user_id, account_id, doc_id) / "meta.json"


def write_sidecar(user_id, account_id, doc_id, metadata):
    import json
    path = _sidecar_path(user_id, account_id, doc_id)
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))


def read_sidecar(user_id, account_id, doc_id):
    import json
    path = _sidecar_path(user_id, account_id, doc_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def sidecar_exists(user_id, account_id, doc_id):
    return _sidecar_path(user_id, account_id, doc_id).exists()
