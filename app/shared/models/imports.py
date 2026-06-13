from datetime import datetime, timezone

from app.shared.db import db


def _utcnow():
    return datetime.now(timezone.utc)


class ImportRequest(db.Model):
    __tablename__ = "import_requests"

    id = db.Column(db.Integer, primary_key=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    customer_email = db.Column(db.String(255), nullable=False)
    source_type = db.Column(db.String(32), nullable=False, default="google")
    destination_email = db.Column(db.String(255), nullable=False)
    destination_imap_host = db.Column(db.String(255), nullable=False)
    destination_imap_port = db.Column(db.Integer, nullable=False, default=993)
    destination_imap_tls = db.Column(db.Boolean, nullable=False, default=True)
    destination_username = db.Column(db.String(255), nullable=False)
    encrypted_destination_secret = db.Column(db.LargeBinary, nullable=False)
    encrypted_source_refresh_token = db.Column(db.LargeBinary, nullable=True)
    source_account_email = db.Column(db.String(255), nullable=True)
    staged_upload_path = db.Column(db.String(512), nullable=True)
    upload_filename = db.Column(db.String(255), nullable=True)
    upload_size_bytes = db.Column(db.Integer, nullable=False, default=0)
    uploaded_bytes = db.Column(db.Integer, nullable=False, default=0)
    upload_status = db.Column(db.String(32), nullable=False, default="none")
    upload_completed_at = db.Column(db.DateTime, nullable=True)
    link_key = db.Column(db.String(128), nullable=False, unique=True)
    is_enabled = db.Column(db.Boolean, nullable=False, default=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(32), nullable=False, default="pending_auth")
    last_error = db.Column(db.Text, nullable=True)
    last_run_started_at = db.Column(db.DateTime, nullable=True)
    last_run_finished_at = db.Column(db.DateTime, nullable=True)
    total_seen_count = db.Column(db.Integer, nullable=False, default=0)
    total_imported_count = db.Column(db.Integer, nullable=False, default=0)
    total_skipped_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )


class ImportRun(db.Model):
    __tablename__ = "import_runs"

    id = db.Column(db.Integer, primary_key=True)
    import_request_id = db.Column(
        db.Integer,
        db.ForeignKey("import_requests.id"),
        nullable=False,
        index=True,
    )
    status = db.Column(db.String(32), nullable=False, default="queued")
    last_error = db.Column(db.Text, nullable=True)
    current_phase = db.Column(db.String(64), nullable=True)
    total_seen_count = db.Column(db.Integer, nullable=False, default=0)
    imported_count = db.Column(db.Integer, nullable=False, default=0)
    skipped_count = db.Column(db.Integer, nullable=False, default=0)
    folder_counts_json = db.Column(db.Text, nullable=True)
    started_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    finished_at = db.Column(db.DateTime, nullable=True)


class ImportedMessage(db.Model):
    __tablename__ = "imported_messages"
    __table_args__ = (
        db.UniqueConstraint("import_request_id", "source_message_id", name="uq_import_message_source"),
    )

    id = db.Column(db.Integer, primary_key=True)
    import_request_id = db.Column(
        db.Integer,
        db.ForeignKey("import_requests.id"),
        nullable=False,
        index=True,
    )
    import_run_id = db.Column(
        db.Integer,
        db.ForeignKey("import_runs.id"),
        nullable=True,
        index=True,
    )
    source_message_id = db.Column(db.String(128), nullable=False)
    destination_folder = db.Column(db.String(255), nullable=False)
    imported_at = db.Column(db.DateTime, nullable=False, default=_utcnow)


class ImportCheckpoint(db.Model):
    __tablename__ = "import_checkpoints"

    import_request_id = db.Column(
        db.Integer,
        db.ForeignKey("import_requests.id"),
        primary_key=True,
    )
    import_run_id = db.Column(
        db.Integer,
        db.ForeignKey("import_runs.id"),
        nullable=True,
        index=True,
    )
    page_token = db.Column(db.String(512), nullable=True)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )
