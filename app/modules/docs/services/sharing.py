import logging
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import current_app

from app.shared.db import db
from app.shared.models.core import DocShare, Domain

logger = logging.getLogger(__name__)


def is_internal_email(email):
    domain_part = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    if not domain_part:
        return False
    return Domain.query.filter_by(name=domain_part, is_active=True).first() is not None


def create_share(doc_id, owner_user_id, owner_account_id, recipient_email,
                 permission, doc_name, doc_type, doc_size=0, doc_updated_at=None):
    share_type = "internal" if is_internal_email(recipient_email) else "link"
    share_token = uuid.uuid4().hex

    share = DocShare(
        doc_id=doc_id,
        owner_user_id=owner_user_id,
        owner_account_id=owner_account_id,
        share_token=share_token,
        permission=permission,
        share_type=share_type,
        recipient_email=recipient_email.lower().strip(),
        doc_name=doc_name,
        doc_type=doc_type,
        doc_size=doc_size,
        doc_updated_at=doc_updated_at,
    )
    db.session.add(share)
    db.session.commit()
    return share


def create_shares_batch(doc_id, owner_user_id, owner_account_id, recipients,
                        permission, doc_name, doc_type, doc_size=0, doc_updated_at=None):
    shares = []
    for email in recipients:
        email = email.strip().lower()
        if not email or "@" not in email:
            continue
        existing = DocShare.query.filter_by(
            doc_id=doc_id,
            recipient_email=email,
            revoked_at=None,
        ).first()
        if existing:
            continue
        share = create_share(
            doc_id, owner_user_id, owner_account_id, email,
            permission, doc_name, doc_type, doc_size, doc_updated_at,
        )
        shares.append(share)
    return shares


def revoke_share(share_id, owner_user_id):
    share = db.session.get(DocShare, share_id)
    if not share or share.owner_user_id != owner_user_id:
        return False
    share.revoked_at = datetime.now(timezone.utc)
    db.session.commit()
    return True


def revoke_shares_for_doc(doc_id):
    now = datetime.now(timezone.utc)
    DocShare.query.filter_by(doc_id=doc_id, revoked_at=None).update({"revoked_at": now})
    db.session.commit()


def update_shares_on_rename(doc_id, new_name):
    DocShare.query.filter_by(doc_id=doc_id, revoked_at=None).update({"doc_name": new_name})
    db.session.commit()


def update_shares_on_save(doc_id, file_size):
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    DocShare.query.filter_by(doc_id=doc_id, revoked_at=None).update({
        "doc_size": file_size,
        "doc_updated_at": now_str,
    })
    db.session.commit()


def get_active_shares_for_doc(doc_id):
    return DocShare.query.filter_by(doc_id=doc_id, revoked_at=None).order_by(DocShare.created_at.desc()).all()


def get_share_by_token(share_token):
    return DocShare.query.filter_by(share_token=share_token, revoked_at=None).first()


def record_share_access(share):
    share.view_count = (share.view_count or 0) + 1
    share.last_accessed_at = datetime.now(timezone.utc)
    db.session.commit()


def get_shared_with_user(user_emails):
    if not user_emails:
        return []
    return DocShare.query.filter(
        DocShare.recipient_email.in_(user_emails),
        DocShare.revoked_at.is_(None),
    ).order_by(DocShare.doc_updated_at.desc()).all()


def send_share_invite(share, owner_email):
    try:
        from app.shared.models.core import CustomerAccount
        account = db.session.get(CustomerAccount, share.owner_account_id)
        if not account:
            logger.warning("share invite skipped: account %s not found", share.owner_account_id)
            return False

        domain = db.session.get(Domain, account.domain_id)
        if not domain:
            logger.warning("share invite skipped: domain not found for account %s", share.owner_account_id)
            return False

        base_url = current_app.config.get("WOPI_HOST_URL", "")
        if not base_url:
            base_url = current_app.config.get("APP_URL", "http://localhost:5001")

        if share.share_type == "link":
            doc_url = f"{base_url}/app/docs/s/{share.share_token}"
        else:
            doc_url = f"{base_url}/app/docs/"

        perm_label = "view" if share.permission == "view" else "edit"
        html_body = _build_invite_html(owner_email, share.doc_name, perm_label, doc_url)
        plain_body = _build_invite_plain(owner_email, share.doc_name, perm_label, doc_url)

        msg = MIMEMultipart("alternative")
        msg["From"] = owner_email
        msg["To"] = share.recipient_email
        msg["Subject"] = f"{owner_email} shared a document with you: {share.doc_name}"
        msg.attach(MIMEText(plain_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        _smtp_send(domain, account, owner_email, [share.recipient_email], msg.as_bytes())
        logger.info("share invite sent to=%s doc=%s", share.recipient_email, share.doc_id)
        return True
    except Exception:
        logger.exception("share invite failed to=%s doc=%s", share.recipient_email, share.doc_id)
        return False


def _smtp_send(domain, account, from_addr, recipients, msg_bytes):
    import smtplib
    from app.shared.keys import get_user_key

    key = get_user_key(account.customer_id)
    if not key:
        raise RuntimeError("Session key unavailable for SMTP")

    from app.modules.mail.services.secrets import decrypt_with_key
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    if not secret:
        raise RuntimeError("Credentials unavailable for SMTP")

    server = None
    try:
        if domain.smtp_tls_mode == "smtps":
            server = smtplib.SMTP_SSL(domain.smtp_host, domain.smtp_port)
        else:
            server = smtplib.SMTP(domain.smtp_host, domain.smtp_port)
            server.ehlo()
            server.starttls()
        server.ehlo()
        server.login(account.username, secret)
        server.sendmail(from_addr, recipients, msg_bytes)
    finally:
        if server:
            try:
                server.quit()
            except Exception:
                pass


def _build_invite_html(owner_email, doc_name, perm_label, doc_url):
    return f"""<!DOCTYPE html>
<html><body style="font-family:system-ui,sans-serif;color:#1e293b;margin:0;padding:0;background:#f8fafc">
<div style="max-width:480px;margin:40px auto;background:#fff;border-radius:16px;border:1px solid #e2e8f0;overflow:hidden">
  <div style="padding:32px 28px">
    <p style="margin:0 0 8px;font-size:15px;color:#475569">Document shared with you</p>
    <h2 style="margin:0 0 16px;font-size:20px;font-weight:600;color:#0f172a">{_escape(doc_name)}</h2>
    <p style="margin:0 0 6px;font-size:14px;color:#64748b">
      <strong>{_escape(owner_email)}</strong> shared this document with <strong>{perm_label}</strong> access.
    </p>
    <a href="{doc_url}" style="display:inline-block;margin-top:20px;padding:10px 24px;background:#0f172a;color:#fff;text-decoration:none;border-radius:10px;font-size:14px;font-weight:500">
      Open Document
    </a>
  </div>
  <div style="padding:16px 28px;background:#f8fafc;border-top:1px solid #f1f5f9;font-size:12px;color:#94a3b8">
    LocoRoo Docs
  </div>
</div>
</body></html>"""


def _build_invite_plain(owner_email, doc_name, perm_label, doc_url):
    return (
        f"{owner_email} shared a document with you.\n\n"
        f"Document: {doc_name}\n"
        f"Permission: {perm_label}\n\n"
        f"Open the document:\n{doc_url}\n"
    )


def _escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
