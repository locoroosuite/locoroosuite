import logging
import os
from pathlib import Path

from app.modules.docs.services import cache_db, doc_meta, storage

logger = logging.getLogger(__name__)


def build_doc_metadata(doc_id, name, doc_type, account_id, original_format=None, deleted_at=None, created_at=None, updated_at=None):
    return {
        "id": doc_id,
        "name": name,
        "doc_type": doc_type,
        "original_format": original_format,
        "account_id": account_id,
        "deleted_at": deleted_at,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def inject_metadata_to_file(user_id, account_id, doc_id, metadata):
    file_bytes = storage.read_file(user_id, account_id, doc_id)
    if file_bytes is None:
        logger.warning("inject_metadata: file not found for doc_id=%s", doc_id)
        return
    try:
        patched = doc_meta.inject_metadata(file_bytes, metadata)
        storage.write_file(user_id, account_id, doc_id, patched)
    except Exception:
        logger.exception("inject_metadata: failed for doc_id=%s", doc_id)


def inject_metadata_from_doc_row(user_id, account_id, doc):
    metadata = build_doc_metadata(
        doc_id=doc["id"],
        name=doc["name"],
        doc_type=doc["doc_type"],
        account_id=doc["account_id"],
        original_format=doc.get("original_format"),
        deleted_at=doc.get("deleted_at"),
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at"),
    )
    if doc.get("original_format"):
        storage.write_sidecar(user_id, account_id, doc["id"], metadata)
    else:
        inject_metadata_to_file(user_id, account_id, doc["id"], metadata)


def resync_docs(conn, user_id, account_id):
    docs_dir = storage.get_docs_dir() / str(user_id) / str(account_id)
    if not docs_dir.exists():
        return 0

    count = 0
    for entry in sorted(docs_dir.iterdir()):
        if not entry.is_dir():
            continue
        doc_id = entry.name
        content_path = entry / "content"
        if not content_path.exists():
            continue

        existing = cache_db.get_document(conn, doc_id)
        if existing:
            continue

        file_bytes = content_path.read_bytes()
        file_size = len(file_bytes)

        sidecar_meta = storage.read_sidecar(user_id, account_id, doc_id)
        if sidecar_meta:
            name = sidecar_meta.get("name", doc_id)
            doc_type = sidecar_meta.get("doc_type", "odt")
            original_format = sidecar_meta.get("original_format")
            deleted_at = sidecar_meta.get("deleted_at")
            created_at = sidecar_meta.get("created_at")
            updated_at = sidecar_meta.get("updated_at")
            meta_account_id = sidecar_meta.get("account_id", account_id)
        else:
            metadata = doc_meta.extract_metadata(file_bytes)
            if metadata:
                name = metadata.get("name", doc_id)
                doc_type = metadata.get("doc_type", "odt")
                original_format = metadata.get("original_format")
                deleted_at = metadata.get("deleted_at")
                created_at = metadata.get("created_at")
                updated_at = metadata.get("updated_at")
                meta_account_id = metadata.get("account_id", account_id)
            else:
                name = _guess_name(doc_id, file_bytes)
                doc_type, original_format = _guess_type_and_format(file_bytes)
                deleted_at = None
                created_at = None
                updated_at = None
                meta_account_id = account_id

        cache_db.create_document(
            conn, doc_id, name, doc_type, meta_account_id,
            file_size=file_size, original_format=original_format,
        )
        if deleted_at:
            cache_db.soft_delete_document(conn, doc_id)
        if created_at:
            conn.execute(
                "UPDATE documents SET created_at = ? WHERE id = ?",
                (created_at, doc_id),
            )
        if updated_at:
            conn.execute(
                "UPDATE documents SET updated_at = ? WHERE id = ?",
                (updated_at, doc_id),
            )
        conn.commit()
        count += 1
        logger.info("resync_docs: recovered doc_id=%s name=%s", doc_id, name)

    return count


def _guess_name(doc_id, file_bytes):
    return doc_id[:8]


def _guess_type_and_format(file_bytes):
    if file_bytes[:4] == b"%PDF":
        return "odt", "pdf"
    if file_bytes[:2] == b"PK":
        format_guess = _guess_office_format_from_zip(file_bytes)
        if format_guess:
            return format_guess
    if file_bytes[:4] != b"PK\x03\x04":
        return "odt", None
    try:
        import zipfile
        import io
        with zipfile.ZipFile(io.BytesIO(file_bytes), "r") as zf:
            if "meta.xml" in zf.namelist():
                meta = zf.read("meta.xml")
                if b"spreadsheet" in meta:
                    return "ods", None
                if b"presentation" in meta:
                    return "odp", None
            if "content.xml" in zf.namelist():
                content = zf.read("content.xml")
                if b"spreadsheet" in content:
                    return "ods", None
                if b"presentation" in content:
                    return "odp", None
    except Exception:
        pass
    return "odt", None


def _guess_office_format_from_zip(file_bytes):
    try:
        import zipfile
        import io
        with zipfile.ZipFile(io.BytesIO(file_bytes), "r") as zf:
            names = zf.namelist()
            if "[Content_Types].xml" not in names:
                return None
            ct = zf.read("[Content_Types].xml").decode("utf-8", errors="ignore")
            if "spreadsheetml" in ct:
                return "ods", "xlsx"
            if "presentationml" in ct:
                return "odp", "pptx"
            if "wordprocessingml" in ct:
                return "odt", "docx"
    except Exception:
        pass
    return None
