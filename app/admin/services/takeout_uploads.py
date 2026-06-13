import os
import re
from datetime import datetime, timezone
from pathlib import Path

from app.config import DATA_DIR


UPLOAD_ROOT = DATA_DIR / "import_uploads"
UPLOAD_ROOT.mkdir(exist_ok=True)

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_upload_name(filename):
    base = os.path.basename((filename or "").strip()) or "mail.mbox"
    cleaned = _SAFE_NAME_RE.sub("_", base)
    if not cleaned.lower().endswith(".mbox"):
        cleaned = cleaned + ".mbox"
    return cleaned[:180]


def staged_upload_part_path(import_request_id, filename):
    safe_name = sanitize_upload_name(filename)
    return UPLOAD_ROOT / f"import-{import_request_id}-{safe_name}.part"


def finalized_upload_path(import_request_id, filename):
    safe_name = sanitize_upload_name(filename)
    return UPLOAD_ROOT / f"import-{import_request_id}-{safe_name}"


def ensure_upload_metadata(import_request, filename, total_size):
    part_path = staged_upload_part_path(import_request.id, filename)
    same_upload = (
        import_request.upload_filename == sanitize_upload_name(filename)
        and int(import_request.upload_size_bytes or 0) == int(total_size or 0)
        and import_request.staged_upload_path
        and Path(import_request.staged_upload_path) == part_path
        and part_path.exists()
    )
    if same_upload:
        import_request.uploaded_bytes = part_path.stat().st_size
        import_request.upload_status = "uploading" if import_request.uploaded_bytes < import_request.upload_size_bytes else "uploaded"
        return part_path, import_request.uploaded_bytes

    cleanup_upload_path(import_request.staged_upload_path)
    import_request.staged_upload_path = str(part_path)
    import_request.upload_filename = sanitize_upload_name(filename)
    import_request.upload_size_bytes = int(total_size or 0)
    import_request.uploaded_bytes = 0
    import_request.upload_status = "uploading"
    import_request.upload_completed_at = None
    part_path.parent.mkdir(parents=True, exist_ok=True)
    with open(part_path, "wb"):
        pass
    return part_path, 0


def append_upload_chunk(import_request, offset, chunk_bytes):
    if not import_request.staged_upload_path:
        raise ValueError("Upload session is not initialized.")
    upload_path = Path(import_request.staged_upload_path)
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    current_size = upload_path.stat().st_size if upload_path.exists() else 0
    if int(offset) != current_size:
        raise ValueError(f"Unexpected upload offset. Expected {current_size}.")
    with open(upload_path, "ab") as handle:
        handle.write(chunk_bytes)
    import_request.uploaded_bytes = upload_path.stat().st_size
    import_request.upload_status = "uploading"
    return upload_path, import_request.uploaded_bytes


def finalize_upload(import_request):
    if not import_request.staged_upload_path:
        raise ValueError("Upload session is not initialized.")
    part_path = Path(import_request.staged_upload_path)
    if not part_path.exists():
        raise ValueError("Uploaded file is missing from staging.")
    if int(import_request.uploaded_bytes or 0) != int(import_request.upload_size_bytes or 0):
        raise ValueError("Uploaded file size does not match the expected size.")
    final_path = finalized_upload_path(import_request.id, import_request.upload_filename or "mail.mbox")
    if final_path.exists():
        final_path.unlink()
    part_path.rename(final_path)
    import_request.staged_upload_path = str(final_path)
    import_request.upload_status = "uploaded"
    import_request.upload_completed_at = datetime.now(timezone.utc)
    return final_path


def cleanup_upload_path(path_value):
    if not path_value:
        return
    try:
        path = Path(path_value)
        if path.exists():
            path.unlink()
    except OSError:
        pass
