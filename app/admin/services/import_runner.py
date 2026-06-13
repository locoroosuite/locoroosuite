import email
import hashlib
import json
import logging
import mailbox
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from app.shared.db import db
from app.shared.models.imports import ImportCheckpoint, ImportedMessage, ImportRequest, ImportRun
from app.admin.services.google_import import gmail_get_raw_message, gmail_list_messages, refresh_google_access_token
from app.modules.mail.services.imap_client import append_message, connect_imap, create_folder, list_folders, login_imap, safe_logout
from app.admin.services.import_security import decrypt_import_secret, is_request_expired
from app.admin.services.takeout_uploads import cleanup_upload_path

logger = logging.getLogger(__name__)

GMAIL_TO_IMAP_FOLDER = {
    "SENT": "Sent",
    "DRAFT": "Drafts",
    "ARCHIVE": "Archive",
    "INBOX": "INBOX",
}

def _utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _folder_for_labels(label_ids):
    labels = set(label_ids or [])
    if "SPAM" in labels or "TRASH" in labels:
        return None
    if "SENT" in labels:
        return GMAIL_TO_IMAP_FOLDER["SENT"]
    if "DRAFT" in labels:
        return GMAIL_TO_IMAP_FOLDER["DRAFT"]
    if "INBOX" in labels:
        return GMAIL_TO_IMAP_FOLDER["INBOX"]
    return GMAIL_TO_IMAP_FOLDER["ARCHIVE"]


def _flags_for_labels(label_ids):
    labels = set(label_ids or [])
    flags = []
    if "UNREAD" not in labels:
        flags.append("\\Seen")
    if "STARRED" in labels:
        flags.append("\\Flagged")
    return flags


def _internal_date_for_message(payload):
    raw_value = payload.get("internalDate")
    if not raw_value:
        return None
    try:
        timestamp = int(raw_value) / 1000.0
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _ensure_folder(client, folder_name, known_folders):
    if folder_name in known_folders:
        return
    create_folder(client, folder_name)
    known_folders.add(folder_name)


def _load_existing_ids(import_request_id, message_ids):
    if not message_ids:
        return set()
    rows = (
        db.session.query(ImportedMessage.source_message_id)
        .filter(
            ImportedMessage.import_request_id == import_request_id,
            ImportedMessage.source_message_id.in_(message_ids),
        )
        .all()
    )
    return {row[0] for row in rows}


def _message_imported(import_request_id, source_message_id):
    return (
        db.session.query(ImportedMessage.id)
        .filter(
            ImportedMessage.import_request_id == import_request_id,
            ImportedMessage.source_message_id == source_message_id,
        )
        .first()
        is not None
    )


def _prepare_destination(import_request):
    destination_password = decrypt_import_secret(import_request.id, "destination_secret", import_request.encrypted_destination_secret)
    client = connect_imap(
        import_request.destination_imap_host,
        import_request.destination_imap_port,
        import_request.destination_imap_tls,
    )
    login_imap(client, import_request.destination_username, password=destination_password)
    known_folders = set(list_folders(client))
    for folder_name in {"Archive", "Sent", "Drafts"}:
        _ensure_folder(client, folder_name, known_folders)
    return client, known_folders


def _load_checkpoint(import_request, run):
    checkpoint = db.session.get(ImportCheckpoint, import_request.id)
    cursor = checkpoint.page_token if checkpoint and checkpoint.import_run_id == run.id else None
    if checkpoint is None:
        checkpoint = ImportCheckpoint(import_request_id=import_request.id, import_run_id=run.id)
        db.session.add(checkpoint)
        db.session.commit()
    else:
        checkpoint.import_run_id = run.id
        db.session.commit()
    return checkpoint, cursor


def _totals_for_run(run):
    return {
        "seen": int(run.total_seen_count or 0),
        "imported": int(run.imported_count or 0),
        "skipped": int(run.skipped_count or 0),
        "folders": json.loads(run.folder_counts_json or "{}"),
    }


def _persist_totals(import_request, run, checkpoint, totals, cursor):
    checkpoint.page_token = cursor
    checkpoint.import_run_id = run.id
    run.total_seen_count = totals["seen"]
    run.imported_count = totals["imported"]
    run.skipped_count = totals["skipped"]
    run.folder_counts_json = json.dumps(totals["folders"], sort_keys=True)
    import_request.total_seen_count = totals["seen"]
    import_request.total_imported_count = totals["imported"]
    import_request.total_skipped_count = totals["skipped"]
    db.session.commit()


def _takeout_labels(message_obj):
    raw_value = message_obj.get("X-Gmail-Labels", "")
    labels = set()
    for part in raw_value.replace(";", ",").split(","):
        label = part.strip().lower()
        if label:
            labels.add(label)
    return labels


def _folder_for_takeout_message(message_obj):
    labels = _takeout_labels(message_obj)
    if any(label in labels for label in ("spam", "junk", "trash")):
        return None
    if any(label in labels for label in ("sent", "\\sent")):
        return "Sent"
    if any(label in labels for label in ("draft", "drafts", "\\draft")):
        return "Drafts"
    if "inbox" in labels:
        return "INBOX"
    return "Archive"


def _flags_for_takeout_message(message_obj):
    labels = _takeout_labels(message_obj)
    flags = []
    if "unread" not in labels:
        flags.append("\\Seen")
    if "starred" in labels:
        flags.append("\\Flagged")
    return flags


def _takeout_message_date(message_obj):
    raw_value = message_obj.get("Date")
    if not raw_value:
        return None
    try:
        parsed = parsedate_to_datetime(raw_value)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _import_google_messages(import_request, run):
    refresh_token = decrypt_import_secret(import_request.id, "source_refresh", import_request.encrypted_source_refresh_token)
    token_data = refresh_google_access_token(
        import_request_app_config("GOOGLE_IMPORT_CLIENT_ID"),
        import_request_app_config("GOOGLE_IMPORT_CLIENT_SECRET"),
        refresh_token,
    )
    access_token = token_data["access_token"]

    client, known_folders = _prepare_destination(import_request)
    checkpoint, page_token = _load_checkpoint(import_request, run)
    totals = _totals_for_run(run)
    try:
        while True:
            run.current_phase = "listing_google_messages"
            db.session.commit()
            page = gmail_list_messages(access_token, page_token=page_token, max_results=100)
            message_refs = page.get("messages") or []
            message_ids = [item["id"] for item in message_refs if item.get("id")]
            existing_ids = _load_existing_ids(import_request.id, message_ids)

            for message_id in message_ids:
                totals["seen"] += 1
                if message_id in existing_ids:
                    totals["skipped"] += 1
                    continue

                run.current_phase = "fetching_google_message"
                db.session.commit()
                payload = gmail_get_raw_message(access_token, message_id)
                folder_name = _folder_for_labels(payload.get("labelIds"))
                if not folder_name:
                    totals["skipped"] += 1
                    continue

                _ensure_folder(client, folder_name, known_folders)
                flags = _flags_for_labels(payload.get("labelIds"))
                append_message(
                    client,
                    folder_name,
                    payload["raw_bytes"],
                    flags=flags,
                    date_time=_internal_date_for_message(payload),
                )

                record = ImportedMessage(
                    import_request_id=import_request.id,
                    import_run_id=run.id,
                    source_message_id=message_id,
                    destination_folder=folder_name,
                )
                db.session.add(record)
                try:
                    db.session.commit()
                    totals["imported"] += 1
                    totals["folders"][folder_name] = totals["folders"].get(folder_name, 0) + 1
                except IntegrityError:
                    db.session.rollback()
                    totals["skipped"] += 1

                _persist_totals(import_request, run, checkpoint, totals, page_token)

            page_token = page.get("nextPageToken")
            _persist_totals(import_request, run, checkpoint, totals, page_token)
            if not page_token:
                break
        checkpoint.page_token = None
        db.session.commit()
    finally:
        safe_logout(client)


def _import_takeout_messages(import_request, run):
    if not import_request.staged_upload_path:
        import_request.status = "pending_upload"
        import_request.last_error = "Upload a Google Takeout MBOX file before starting the import."
        db.session.commit()
        return False
    staged_path = Path(import_request.staged_upload_path)
    if not staged_path.exists():
        import_request.status = "pending_upload"
        import_request.last_error = "The staged Takeout file is no longer available. Upload it again."
        import_request.upload_status = "pending_upload"
        import_request.staged_upload_path = None
        import_request.uploaded_bytes = 0
        db.session.commit()
        return False

    client, known_folders = _prepare_destination(import_request)
    checkpoint, cursor = _load_checkpoint(import_request, run)
    start_index = int(cursor or "0")
    totals = _totals_for_run(run)
    mbox = mailbox.mbox(str(staged_path), factory=None, create=False)
    try:
        for index, key in enumerate(mbox.iterkeys()):
            if index < start_index:
                continue
            run.current_phase = "reading_takeout_mbox"
            db.session.commit()
            handle = mbox.get_file(key)
            try:
                raw_bytes = handle.read()
            finally:
                handle.close()
            message_obj = email.message_from_bytes(raw_bytes)
            folder_name = _folder_for_takeout_message(message_obj)
            totals["seen"] += 1
            dedupe_key = "takeout:" + hashlib.sha256(raw_bytes).hexdigest()
            if not folder_name or _message_imported(import_request.id, dedupe_key):
                totals["skipped"] += 1
                _persist_totals(import_request, run, checkpoint, totals, str(index + 1))
                continue

            _ensure_folder(client, folder_name, known_folders)
            append_message(
                client,
                folder_name,
                raw_bytes,
                flags=_flags_for_takeout_message(message_obj),
                date_time=_takeout_message_date(message_obj),
            )
            record = ImportedMessage(
                import_request_id=import_request.id,
                import_run_id=run.id,
                source_message_id=dedupe_key,
                destination_folder=folder_name,
            )
            db.session.add(record)
            try:
                db.session.commit()
                totals["imported"] += 1
                totals["folders"][folder_name] = totals["folders"].get(folder_name, 0) + 1
            except IntegrityError:
                db.session.rollback()
                totals["skipped"] += 1
            _persist_totals(import_request, run, checkpoint, totals, str(index + 1))
        checkpoint.page_token = None
        db.session.commit()
        cleanup_upload_path(import_request.staged_upload_path)
        import_request.staged_upload_path = None
        import_request.upload_status = "consumed"
        import_request.uploaded_bytes = 0
        db.session.commit()
        return True
    finally:
        safe_logout(client)


def run_import(import_request_id, run_id=None):
    import_request = db.session.get(ImportRequest, import_request_id)
    if not import_request:
        logger.warning("import request missing import_request_id=%s", import_request_id)
        return False
    if not import_request.is_enabled or is_request_expired(import_request):
        logger.info("import request inactive import_request_id=%s", import_request.id)
        import_request.status = "disabled" if not import_request.is_enabled else "expired"
        db.session.commit()
        return False
    if import_request.source_type == "google" and not import_request.encrypted_source_refresh_token:
        import_request.status = "pending_auth"
        import_request.last_error = "Google authorization is required before import can run."
        db.session.commit()
        return False
    if import_request.source_type == "google_takeout" and not import_request.staged_upload_path:
        import_request.status = "pending_upload"
        import_request.last_error = "Upload a Google Takeout MBOX file before starting the import."
        db.session.commit()
        return False

    run = db.session.get(ImportRun, run_id) if run_id else None
    if run is None:
        run = ImportRun(import_request_id=import_request.id, status="running", current_phase="starting")
        db.session.add(run)
        db.session.commit()
        run_id = run.id

    import_request.status = "running"
    import_request.last_error = None
    import_request.last_run_started_at = _utcnow_naive()
    import_request.last_run_finished_at = None
    run.started_at = import_request.last_run_started_at
    run.status = "running"
    run.last_error = None
    run.current_phase = "refreshing_google_token"
    db.session.commit()

    try:
        if import_request.source_type == "google":
            _import_google_messages(import_request, run)
        elif import_request.source_type == "google_takeout":
            if _import_takeout_messages(import_request, run) is False:
                return False
        else:
            raise RuntimeError(f"Unsupported import source: {import_request.source_type}")

        run.status = "completed"
        run.current_phase = "completed"
        run.finished_at = _utcnow_naive()
        import_request.status = "completed"
        import_request.last_error = None
        import_request.last_run_finished_at = run.finished_at
        db.session.commit()
        return True
    except Exception as exc:
        logger.exception("import run failed import_request_id=%s run_id=%s", import_request.id, run.id)
        error_message = str(exc)[:500] or "import failed"
        run.status = "failed"
        run.last_error = error_message
        run.finished_at = _utcnow_naive()
        import_request.status = "failed"
        import_request.last_error = error_message
        import_request.last_run_finished_at = run.finished_at
        db.session.commit()
        return False


def import_request_app_config(key):
    from flask import current_app

    value = current_app.config.get(key)
    if not value:
        raise RuntimeError(f"Missing required config: {key}")
    return value
