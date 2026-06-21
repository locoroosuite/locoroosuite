import logging
from math import ceil

from flask import render_template, redirect, url_for, session, request

from app.shared.auth import require_customer
from app.modules.contacts.controllers.helpers import (
    contacts_bp,
    _get_account,
    _get_carddav_config,
    _get_credentials,
    _open_cache_for_account,
    _carddav_base_url,
)
from app.modules.contacts.services import carddav, cache_db

logger = logging.getLogger(__name__)


@contacts_bp.route("/contacts/")
@require_customer
def contact_list():
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return redirect(url_for("mail.mailbox"))

    account = _get_account(account_id, user_id)
    config = _get_carddav_config(account)
    if not config:
        return render_template(
            "list.html",
            contacts=[],
            page=1,
            total_pages=0,
            total=0,
            q="",
            account=account,
            carddav_configured=False,
        )

    conn = _open_cache_for_account(account)
    if not conn:
        return redirect(url_for("mail.login"))

    try:
        sync_state = cache_db.get_sync_state(conn, _carddav_base_url(config))
        if not sync_state:
            _sync_contacts(conn, account, config)

        q = request.args.get("q", "").strip()
        page = request.args.get("page", 1, type=int)
        per_page = 50

        if q:
            contacts = cache_db.search_contacts(conn, q, page, per_page)
            total = len(contacts)
        else:
            contacts = cache_db.list_contacts(conn, page, per_page)
            total = cache_db.count_contacts(conn)

        total_pages = max(1, ceil(total / per_page))
        return render_template(
            "list.html",
            contacts=contacts,
            page=page,
            total_pages=total_pages,
            total=total,
            q=q,
            account=account,
            carddav_configured=True,
        )
    finally:
        conn.close()


@contacts_bp.route("/contacts/<int:account_id>/<uid>")
@require_customer
def contact_detail(account_id, uid):
    user_id = session.get("user_id")
    account = _get_account(account_id, user_id)

    conn = _open_cache_for_account(account)
    if not conn:
        return redirect(url_for("mail.login"))

    try:
        contact = cache_db.get_contact_by_uid(conn, uid)
        if not contact:
            return redirect(url_for("contacts.contact_list"))
        return render_template("detail.html", contact=contact, account=account)
    finally:
        conn.close()


@contacts_bp.route("/contacts/new", methods=["GET", "POST"])
@require_customer
def contact_new():
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return redirect(url_for("mail.mailbox"))

    account = _get_account(account_id, user_id)
    config = _get_carddav_config(account)
    if not config:
        return redirect(url_for("contacts.contact_list"))

    if request.method == "GET":
        return render_template("form.html", contact=None, account=account, errors={})

    errors = _validate_contact_form(request.form)
    if errors:
        return render_template(
            "form.html",
            contact=request.form.to_dict(),
            account=account,
            errors=errors,
        )

    email_work = request.form.get("email_work", "").strip()
    email_home = request.form.get("email_home", "").strip()

    conn = _open_cache_for_account(account)
    if not conn:
        return redirect(url_for("mail.login"))

    try:
        dup_errors = _check_email_duplicates(conn, email_work, email_home)
        if dup_errors:
            return render_template(
                "form.html",
                contact=request.form.to_dict(),
                account=account,
                errors=dup_errors,
            )
    finally:
        conn.close()

    from app.modules.contacts.services.vcard import generate_vcard
    data = _form_to_data(request.form)
    vcard_text = generate_vcard(data)

    password = _get_credentials(account)
    if not password:
        return redirect(url_for("mail.login"))

    try:
        s, abook_url, _ = carddav.discover_address_book(
            _carddav_base_url(config), account.username, password
        )
        if not abook_url:
            abook_url = carddav.create_address_book(s, _carddav_base_url(config), account.username)
        href, etag = carddav.create_contact(s, abook_url, vcard_text)
        uid = None
        from app.modules.contacts.services.vcard import extract_uid
        uid = extract_uid(vcard_text)
        conn = _open_cache_for_account(account)
        try:
            cache_db.upsert_contact(conn, uid, href, etag, vcard_text)
        finally:
            conn.close()
    except Exception:
        logger.exception("failed to create contact on CardDAV")
        return render_template(
            "form.html",
            contact=request.form.to_dict(),
            account=account,
            errors={"_server": "Failed to save contact. Please check your connection and retry."},
        )

    return redirect(url_for("contacts.contact_list"))


@contacts_bp.route("/contacts/<int:account_id>/<uid>/edit", methods=["GET", "POST"])
@require_customer
def contact_edit(account_id, uid):
    user_id = session.get("user_id")
    account = _get_account(account_id, user_id)

    conn = _open_cache_for_account(account)
    if not conn:
        return redirect(url_for("mail.login"))

    try:
        contact = cache_db.get_contact_by_uid(conn, uid)
        if not contact:
            return redirect(url_for("contacts.contact_list"))

        if request.method == "GET":
            return render_template("form.html", contact=contact, account=account, errors={})

        errors = _validate_contact_form(request.form)
        if errors:
            contact.update(request.form.to_dict())
            return render_template("form.html", contact=contact, account=account, errors=errors)

        email_work = request.form.get("email_work", "").strip()
        email_home = request.form.get("email_home", "").strip()
        dup_errors = _check_email_duplicates(conn, email_work, email_home, exclude_uid=uid)
        if dup_errors:
            contact.update(request.form.to_dict())
            return render_template("form.html", contact=contact, account=account, errors=dup_errors)

        from app.modules.contacts.services.vcard import generate_vcard
        data = _form_to_data(request.form)
        vcard_text = generate_vcard(data, uid=uid)

        config = _get_carddav_config(account)
        password = _get_credentials(account)
        if not config or not password:
            return redirect(url_for("mail.login"))

        try:
            s, abook_url, _ = carddav.discover_address_book(
                _carddav_base_url(config), account.username, password
            )
            stored_href = contact.get("href")
            href = stored_href
            if href and not href.startswith("http") and abook_url:
                href = f"{abook_url.rstrip('/')}/{uid}.vcf"
            if href:
                etag = carddav.update_contact(s, href, vcard_text, contact.get("etag"))
            else:
                href, etag = carddav.create_contact(s, abook_url, vcard_text, uid=uid)
            cache_db.upsert_contact(conn, uid, href, etag, vcard_text)
        except Exception:
            logger.exception("failed to update contact on CardDAV")
            contact.update(request.form.to_dict())
            return render_template(
                "form.html",
                contact=contact,
                account=account,
                errors={"_server": "Failed to save contact. Please check your connection and retry."},
            )

        return redirect(url_for("contacts.contact_detail", account_id=account_id, uid=uid))
    finally:
        conn.close()


@contacts_bp.route("/contacts/<int:account_id>/<uid>/delete", methods=["POST"])
@require_customer
def contact_delete(account_id, uid):
    user_id = session.get("user_id")
    account = _get_account(account_id, user_id)

    conn = _open_cache_for_account(account)
    if not conn:
        return redirect(url_for("mail.login"))

    try:
        contact = cache_db.get_contact_by_uid(conn, uid)
        if not contact:
            return redirect(url_for("contacts.contact_list"))

        config = _get_carddav_config(account)
        password = _get_credentials(account)
        if config and password and contact.get("href"):
            try:
                base_url = _carddav_base_url(config)
                s, abook_url, _ = carddav.discover_address_book(
                    base_url, account.username, password
                )
                contact_href = contact["href"]
                if not contact_href.startswith("http"):
                    if abook_url:
                        contact_href = f"{abook_url.rstrip('/')}/{uid}.vcf"
                carddav.delete_contact(s, contact_href, contact.get("etag"))
            except Exception:
                logger.exception("failed to delete contact from CardDAV")

        cache_db.delete_contact_by_uid(conn, uid)
    finally:
        conn.close()

    return redirect(url_for("contacts.contact_list"))


@contacts_bp.route("/contacts/sync", methods=["POST"])
@require_customer
def contact_sync():
    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return redirect(url_for("mail.mailbox"))

    account = _get_account(account_id, user_id)
    config = _get_carddav_config(account)
    if not config:
        return redirect(url_for("contacts.contact_list"))

    conn = _open_cache_for_account(account)
    if not conn:
        return redirect(url_for("mail.login"))

    try:
        _sync_contacts(conn, account, config)
    except Exception:
        logger.exception("contact sync failed")
    finally:
        conn.close()

    return redirect(url_for("contacts.contact_list"))


def _sync_contacts(conn, account, config):
    password = _get_credentials(account)
    if not password:
        return

    s, abook_url, _ = carddav.discover_address_book(
        _carddav_base_url(config), account.username, password
    )
    if not abook_url:
        abook_url = carddav.create_address_book(s, _carddav_base_url(config), account.username)

    remote_contacts = carddav.list_contacts(s, abook_url)
    from app.modules.contacts.services.vcard import extract_uid

    remote_uids = set()
    for href, etag, vcard_text in remote_contacts:
        uid = extract_uid(vcard_text)
        if uid:
            remote_uids.add(uid)
            cache_db.upsert_contact(conn, uid, href, etag, vcard_text)

    local_rows = conn.execute("SELECT uid FROM contacts").fetchall()
    for (local_uid,) in local_rows:
        if local_uid not in remote_uids:
            cache_db.delete_contact_by_uid(conn, local_uid)

    cache_db.set_sync_state(conn, _carddav_base_url(config))


def _validate_contact_form(form):
    errors = {}
    fn = form.get("fn", "").strip()
    first_name = form.get("first_name", "").strip()
    last_name = form.get("last_name", "").strip()
    if not fn and not first_name and not last_name:
        errors["fn"] = "Name is required."
    return errors


def _form_to_data(form):
    return {
        "fn": form.get("fn", "").strip(),
        "first_name": form.get("first_name", "").strip(),
        "last_name": form.get("last_name", "").strip(),
        "email_work": form.get("email_work", "").strip() or None,
        "email_home": form.get("email_home", "").strip() or None,
        "tel_work": form.get("tel_work", "").strip() or None,
        "tel_home": form.get("tel_home", "").strip() or None,
        "tel_cell": form.get("tel_cell", "").strip() or None,
        "org": form.get("org", "").strip() or None,
        "title": form.get("title", "").strip() or None,
        "note": form.get("note", "").strip() or None,
    }


def _check_email_duplicates(conn, email_work, email_home, exclude_uid=None):
    errors = {}
    for label, email in [("email_work", email_work), ("email_home", email_home)]:
        if not email:
            continue
        existing = cache_db.find_by_email(conn, email)
        if existing and existing["uid"] != exclude_uid:
            errors[label] = f"Email {email} is already used by contact '{existing['fn'] or existing['first_name'] + ' ' + existing['last_name']}'."
    return errors
