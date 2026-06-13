import itertools
import logging
import queue
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_

from app.shared.db import db
from app.shared.models.core import CustomerAccount, CustomerSettings, Domain
from app.shared.models.imports import ImportRequest
from app.modules.mail.services.cache_db import list_recent_active_folders, open_cache
from app.modules.mail.services.imap_client import connect_imap, login_imap, select_folder, idle_wait, safe_logout
from app.modules.mail.services.imap_sync import sync_account
from app.admin.services.import_runner import run_import
from app.admin.services.import_security import is_request_expired
from app.admin.services.takeout_uploads import cleanup_upload_path
from app.modules.mail.services.secrets import decrypt_with_key
from app.shared.events import push_event
from app.shared.keys import get_user_key

logger = logging.getLogger(__name__)


class _IdleWorker:
    def __init__(self, app, manager, account_id, folder):
        self.app = app
        self.manager = manager
        self.account_id = account_id
        self.folder = folder
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        with self.app.app_context():
            while not self._stop.is_set():
                account = db.session.get(CustomerAccount, self.account_id)
                if not account or not account.is_active:
                    return
                key = get_user_key(account.customer_id)
                if not key:
                    logger.info("idle worker stop missing key account_id=%s", account.id)
                    return
                domain = db.session.get(Domain, account.domain_id)
                if not domain or not domain.is_active:
                    logger.info("idle worker stop missing domain account_id=%s", account.id)
                    return
                try:
                    client = connect_imap(domain.imap_host, domain.imap_port, domain.imap_tls)
                    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
                    login_imap(
                        client,
                        account.username,
                        password=secret,
                    )
                    select_folder(client, self.folder)
                    logger.info(
                        "idle worker connected account_id=%s folder=%s",
                        account.id,
                        self.folder,
                    )
                    while not self._stop.is_set():
                        self.manager.emit_status(
                            account,
                            state="idle",
                            folder=self.folder,
                            message="waiting for changes",
                        )
                        supported, response = idle_wait(client, timeout=60)
                        if not supported:
                            logger.warning("idle not supported account_id=%s", account.id)
                            self.manager.set_idle_supported(account.id, False)
                            self.manager.emit_status(
                                account,
                                state="error",
                                folder=self.folder,
                                message="imap idle unsupported",
                            )
                            break
                        if response:
                            self.manager.enqueue_sync(
                                account.id,
                                folder=self.folder,
                                reason="idle",
                                priority=5,
                            )
                    safe_logout(client)
                    return
                except Exception:
                    logger.exception("idle worker error account_id=%s", account.id)
                    self.manager.emit_status(
                        account,
                        state="error",
                        folder=self.folder,
                        message="imap idle error",
                    )
                    self.manager.set_idle_supported(account.id, False)
                    return


class WorkerManager:
    def __init__(self, app):
        self.app = app
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._stop = threading.Event()
        self._queue = queue.PriorityQueue()
        self._import_queue = queue.PriorityQueue()
        self._counter = itertools.count()
        self._pending = set()
        self._pending_imports = set()
        self._lock = threading.RLock()
        self._active_accounts = {}
        self._active_folders = {}
        self._idle_workers = {}
        self._idle_supported = {}
        self._last_active_poll = {}
        self._last_background_check = {}
        self._fast_poll_until = {}
        self._fast_poll_interval = 5
        self._fast_poll_window = 30
        self._retry_attempts = {}
        self._import_retry_attempts = {}
        self._retry_schedule = (15, 30, 60, 120, 300)
        self._last_upload_cleanup = 0

    def start(self):
        if not self._thread.is_alive():
            logger.info("worker manager starting")
            self._thread.start()

    def stop(self):
        self._stop.set()
        for worker in list(self._idle_workers.values()):
            worker.stop()
        logger.info("worker manager stop requested")

    def set_active_account(self, customer_id, account_id):
        with self._lock:
            previous = self._active_accounts.get(customer_id)
            if previous == account_id:
                return
            if previous:
                self._stop_idle_for_account(previous)
            self._active_accounts[customer_id] = account_id
            self._active_folders[account_id] = "INBOX"
            self._idle_supported[account_id] = True
            self._ensure_idle(account_id, "INBOX")

    def clear_active_customer(self, customer_id):
        with self._lock:
            previous = self._active_accounts.pop(customer_id, None)
            if previous:
                self._stop_idle_for_account(previous)
                self._active_folders.pop(previous, None)
                self._clear_retry_for_account(previous)

    def enqueue_sync(self, account_id, folder=None, reason="manual", priority=10, delay=0):
        key = self._sync_key(account_id, folder)
        with self._lock:
            if key in self._pending:
                return False
            self._pending.add(key)
        run_at = time.time() + max(0, float(delay or 0))
        self._queue.put((run_at, priority, next(self._counter), account_id, folder, reason))
        return True

    def emit_status(self, account, **payload):
        data = {"account_id": account.id}
        data.update(payload)
        push_event(account.customer_id, "sync_status", data)

    def enqueue_import(self, import_request_id, run_id, priority=10, delay=0):
        with self._lock:
            if import_request_id in self._pending_imports:
                return False
            self._pending_imports.add(import_request_id)
        run_at = time.time() + max(0, float(delay or 0))
        self._import_queue.put((run_at, priority, next(self._counter), import_request_id, run_id))
        return True

    def set_idle_supported(self, account_id, supported):
        with self._lock:
            self._idle_supported[account_id] = supported

    def set_active_folder(self, account_id, folder):
        with self._lock:
            previous = self._active_folders.get(account_id)
            self._active_folders[account_id] = folder
            self._fast_poll_until[account_id] = time.time() + self._fast_poll_window
            if previous and previous != "INBOX":
                self._stop_idle(account_id, previous)
            if folder and folder != "INBOX":
                self._ensure_idle(account_id, folder)
            self._ensure_idle(account_id, "INBOX")

    def _ensure_idle(self, account_id, folder):
        with self._lock:
            if not self._idle_supported.get(account_id, True):
                return
            key = (account_id, folder)
            worker = self._idle_workers.get(key)
            if worker and worker._thread.is_alive():
                return
            worker = _IdleWorker(self.app, self, account_id, folder)
            self._idle_workers[key] = worker
            worker.start()

    def _stop_idle(self, account_id, folder):
        with self._lock:
            worker = self._idle_workers.pop((account_id, folder), None)
            if worker:
                worker.stop()

    def _stop_idle_for_account(self, account_id):
        with self._lock:
            keys = [key for key in self._idle_workers if key[0] == account_id]
            for key in keys:
                worker = self._idle_workers.pop(key, None)
                if worker:
                    worker.stop()

    def _poll_interval(self, account):
        settings = CustomerSettings.query.filter_by(customer_id=account.customer_id).first()
        if not settings:
            return 60
        return max(30, int(settings.polling_interval))

    def _active_poll_interval(self, account_id, account):
        interval = self._poll_interval(account)
        fast_until = self._fast_poll_until.get(account_id, 0)
        if time.time() < fast_until:
            return min(interval, self._fast_poll_interval)
        return interval

    def _run(self):
        while not self._stop.is_set():
            try:
                self._process_queue()
            except Exception:
                logger.exception("worker manager _process_queue error")
            try:
                self._process_import_queue()
            except Exception:
                logger.exception("worker manager _process_import_queue error")
            try:
                self._schedule_background_checks()
            except Exception:
                logger.exception("worker manager _schedule_background_checks error")
            self._stop.wait(1)

    def _sync_key(self, account_id, folder):
        return account_id, (folder or "INBOX").lower()

    def _clear_retry_for_account(self, account_id):
        keys = [key for key in self._retry_attempts if key[0] == account_id]
        for key in keys:
            self._retry_attempts.pop(key, None)

    def _register_retry(self, key):
        with self._lock:
            attempt = self._retry_attempts.get(key, 0) + 1
            self._retry_attempts[key] = attempt
        idx = min(attempt - 1, len(self._retry_schedule) - 1)
        return self._retry_schedule[idx], attempt

    def _clear_retry(self, key):
        with self._lock:
            self._retry_attempts.pop(key, None)

    def _register_import_retry(self, import_request_id):
        with self._lock:
            attempt = self._import_retry_attempts.get(import_request_id, 0) + 1
            self._import_retry_attempts[import_request_id] = attempt
        idx = min(attempt - 1, len(self._retry_schedule) - 1)
        return self._retry_schedule[idx], attempt

    def _clear_import_retry(self, import_request_id):
        with self._lock:
            self._import_retry_attempts.pop(import_request_id, None)

    def _process_queue(self):
        while not self._stop.is_set():
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            run_at, priority, _, account_id, folder, reason = item
            if run_at > time.time():
                self._queue.put(item)
                return
            key = self._sync_key(account_id, folder)
            account = None
            retry_delay = None
            try:
                with self.app.app_context():
                    account = db.session.get(CustomerAccount, account_id)
                    if not account or not account.is_active:
                        logger.info("sync skipped missing account account_id=%s", account_id)
                        self._clear_retry(key)
                        continue
                    if not get_user_key(account.customer_id):
                        logger.info("sync skipped missing key account_id=%s", account_id)
                        self._clear_retry(key)
                        continue

                    def status_cb(payload):
                        payload["reason"] = reason
                        self.emit_status(account, **payload)

                    logger.info(
                        "sync task start account_id=%s folder=%s reason=%s priority=%s",
                        account.id,
                        folder,
                        reason,
                        priority,
                    )
                    include_recent_page = reason in ("folder_open", "login", "account_switch", "cache_reset")
                    sync_ok = sync_account(
                        account,
                        folders=folder,
                        status_cb=status_cb,
                        include_recent_page=include_recent_page,
                    )
                    if sync_ok:
                        self._clear_retry(key)
                        self.emit_status(
                            account,
                            state="idle",
                            folder=folder or "INBOX",
                            message="sync complete",
                        )
                    else:
                        retry_delay, attempt = self._register_retry(key)
                        logger.warning(
                            "sync task retry scheduled account_id=%s folder=%s reason=%s attempt=%s retry_in=%ss",
                            account.id,
                            folder or "INBOX",
                            reason,
                            attempt,
                            retry_delay,
                        )
                        self.emit_status(
                            account,
                            state="error",
                            folder=folder or "INBOX",
                            message=f"sync failed; retrying in {retry_delay}s",
                        )
            except Exception:
                logger.exception(
                    "sync task failed account_id=%s folder=%s reason=%s",
                    account_id,
                    folder,
                    reason,
                )
                if account:
                    retry_delay, attempt = self._register_retry(key)
                    logger.warning(
                        "sync task retry scheduled after exception account_id=%s folder=%s reason=%s attempt=%s retry_in=%ss",
                        account.id,
                        folder or "INBOX",
                        reason,
                        attempt,
                        retry_delay,
                    )
                    self.emit_status(
                        account,
                        state="error",
                        folder=folder or "INBOX",
                        message=f"sync task failed; retrying in {retry_delay}s",
                    )
                else:
                    self._clear_retry(key)
            finally:
                with self._lock:
                    self._pending.discard(key)
            if retry_delay and account and account.is_active:
                self.enqueue_sync(
                    account_id,
                    folder=folder,
                    reason="retry_backoff",
                    priority=min(priority + 5, 90),
                    delay=retry_delay,
                )

    def _process_import_queue(self):
        while not self._stop.is_set():
            try:
                item = self._import_queue.get_nowait()
            except queue.Empty:
                return
            run_at, priority, _, import_request_id, run_id = item
            if run_at > time.time():
                self._import_queue.put(item)
                return
            retry_delay = None
            try:
                with self.app.app_context():
                    import_request = db.session.get(ImportRequest, import_request_id)
                    if not import_request:
                        self._clear_import_retry(import_request_id)
                        continue
                    if not import_request.is_enabled or is_request_expired(import_request):
                        self._clear_import_retry(import_request_id)
                        continue
                    if import_request.source_type == "google" and not import_request.encrypted_source_refresh_token:
                        self._clear_import_retry(import_request_id)
                        continue
                    if import_request.source_type == "google_takeout" and not import_request.staged_upload_path:
                        self._clear_import_retry(import_request_id)
                        continue
                    logger.info(
                        "import task start import_request_id=%s run_id=%s priority=%s",
                        import_request_id,
                        run_id,
                        priority,
                    )
                    success = run_import(import_request_id, run_id=run_id)
                    if success:
                        self._clear_import_retry(import_request_id)
                    else:
                        retry_delay, attempt = self._register_import_retry(import_request_id)
                        import_request.status = "queued"
                        db.session.commit()
                        logger.warning(
                            "import task retry scheduled import_request_id=%s run_id=%s attempt=%s retry_in=%ss",
                            import_request_id,
                            run_id,
                            attempt,
                            retry_delay,
                        )
            except Exception:
                logger.exception(
                    "import task failed import_request_id=%s run_id=%s",
                    import_request_id,
                    run_id,
                )
                retry_delay, attempt = self._register_import_retry(import_request_id)
                with self.app.app_context():
                    import_request = db.session.get(ImportRequest, import_request_id)
                    if import_request and import_request.is_enabled:
                        import_request.status = "queued"
                        db.session.commit()
                logger.warning(
                    "import task retry scheduled after exception import_request_id=%s run_id=%s attempt=%s retry_in=%ss",
                    import_request_id,
                    run_id,
                    attempt,
                    retry_delay,
                )
            finally:
                with self._lock:
                    self._pending_imports.discard(import_request_id)
            if retry_delay:
                self.enqueue_import(
                    import_request_id,
                    run_id,
                    priority=min(priority + 5, 90),
                    delay=retry_delay,
                )

    def _schedule_background_checks(self):
        with self.app.app_context():
            self._cleanup_expired_uploads()
            active_accounts = list(self._active_accounts.values())
            for account_id in active_accounts:
                account = db.session.get(CustomerAccount, account_id)
                if not account or not account.is_active:
                    continue
                if not get_user_key(account.customer_id):
                    continue
                selected = self._active_folders.get(account_id, "INBOX")
                self._ensure_idle(account_id, "INBOX")
                if selected and selected != "INBOX":
                    self._ensure_idle(account_id, selected)

                now = time.time()
                if not self._idle_supported.get(account_id, True):
                    interval = self._active_poll_interval(account_id, account)
                    last_poll = self._last_active_poll.get(account_id, 0)
                    if now - last_poll >= interval:
                        self.enqueue_sync(account_id, folder="INBOX", reason="polling", priority=20)
                        if selected and selected != "INBOX":
                            self.enqueue_sync(account_id, folder=selected, reason="polling", priority=20)
                        self._last_active_poll[account_id] = now

                last_bg = self._last_background_check.get(account_id, 0)
                if now - last_bg >= 300:
                    if not account.cache_db_path:
                        continue
                    key = get_user_key(account.customer_id)
                    if not key:
                        continue
                    conn = open_cache(account.cache_db_path, key)
                    since_iso = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
                    recent_folders = list_recent_active_folders(conn, since_iso)
                    for folder in recent_folders:
                        self.enqueue_sync(account_id, folder=folder, reason="background", priority=50)
                    self._last_background_check[account_id] = now

    def _cleanup_expired_uploads(self):
        now = time.time()
        if now - self._last_upload_cleanup < 3600:
            return
        self._last_upload_cleanup = now
        cutoff = datetime.now(timezone.utc) - timedelta(hours=int(self.app.config.get("IMPORT_UPLOAD_RETENTION_HOURS", 48)))
        stale_rows = (
            ImportRequest.query.filter(
                ImportRequest.source_type == "google_takeout",
                ImportRequest.staged_upload_path.isnot(None),
                or_(
                    ImportRequest.upload_completed_at < cutoff,
                    ImportRequest.updated_at < cutoff,
                ),
                ImportRequest.status.in_(("failed", "disabled", "expired", "pending_upload", "uploading")),
            )
            .all()
        )
        for row in stale_rows:
            cleanup_upload_path(row.staged_upload_path)
            row.staged_upload_path = None
            row.uploaded_bytes = 0
            row.upload_status = "expired"
        if stale_rows:
            db.session.commit()
