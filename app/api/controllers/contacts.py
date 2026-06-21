from flask import g

from app.api.openapi import create_api_blueprint
from app.api.controllers.helpers import (
    api_response, api_paginated, api_error, require_api_token, require_scope,
    get_api_account_id, ApiError,
)
from app.api.schemas.common import ErrorResponse, BulkResponse, EmptyResponse
from app.api.schemas.contacts import (
    ContactListResponse, ContactDetailResponse, ContactSearchResponse,
    ContactPath, ListContactsQuery, SearchContactsQuery,
    CreateContactBody, UpdateContactBody, BulkDeleteContactsBody,
)
from app.shared.models.core import CustomerAccount
from app.modules.contacts.services.cache import get_cache_path
from app.modules.contacts.services.cache_db import (
    open_cache, list_contacts as db_list_contacts,
    get_contact as db_get_contact,
    get_contact_by_uid as db_get_contact_by_uid,
    search_contacts as db_search_contacts,
    delete_contact_by_uid,
    count_contacts,
)
from app.modules.contacts.services.vcard import parse_vcard
from app.shared.ui_events import push_ui_event

bp = create_api_blueprint("contacts", "Contacts management")


def _row_to_dict(row):
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _get_cache_conn(account_id, dek):
    account = CustomerAccount.query.filter_by(id=account_id).first()
    if not account:
        raise ApiError("NOT_FOUND", "Account not found", 404)
    path = get_cache_path(account)
    return open_cache(path, dek)


def _contact_to_dict(row):
    d = _row_to_dict(row)
    vcard_raw = d.get("raw_vcard") or d.get("vcard_text")
    parsed = parse_vcard(vcard_raw) if vcard_raw else {}
    return {
        "id": d["id"],
        "uid": d.get("uid"),
        "fn": parsed.get("fn", "") or d.get("fn", ""),
        "email_work": parsed.get("email_work", "") or d.get("email_work", ""),
        "email_home": parsed.get("email_home", "") or d.get("email_home", ""),
        "phone_work": parsed.get("tel_work", "") or d.get("tel_work", ""),
        "phone_cell": parsed.get("tel_cell", "") or d.get("tel_cell", ""),
        "phone_home": parsed.get("tel_home", "") or d.get("tel_home", ""),
        "organization": parsed.get("organization", "") or d.get("org", ""),
        "title": parsed.get("title", "") or d.get("title", ""),
        "note": parsed.get("note", "") or d.get("note", ""),
    }


@bp.get("/contacts", summary="List contacts", description="Returns paginated contacts for the authenticated account. Supports optional search filtering and page-based pagination. Requires `contacts:read` scope.", responses={"200": ContactListResponse, "401": ErrorResponse})
@require_api_token(scopes=["contacts:read"])
@require_scope("contacts", "read")
def api_list_contacts(query: ListContactsQuery):
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    limit = min(query.max_results, 200)
    page = query.page
    q = query.q
    conn = _get_cache_conn(account_id, dek)
    try:
        if q:
            rows = db_search_contacts(conn, q, page=page, per_page=limit)
        else:
            rows = db_list_contacts(conn, page=page, per_page=limit)
        total = count_contacts(conn)
        items = [_contact_to_dict(r) for r in rows]
        has_more = (page * limit) < total
        next_cursor = str(page + 1) if has_more else None
        return api_paginated(items, next_cursor=next_cursor, has_more=has_more)
    finally:
        conn.close()


@bp.get("/contacts/<int:contact_id>", summary="Get contact detail", description="Returns a single contact by ID, including the raw vCard source. Requires `contacts:read` scope.", responses={"200": ContactDetailResponse, "404": ErrorResponse})
@require_api_token(scopes=["contacts:read"])
@require_scope("contacts", "read")
def api_get_contact(path: ContactPath):
    contact_id = path.contact_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    conn = _get_cache_conn(account_id, dek)
    try:
        row = db_get_contact(conn, contact_id)
        if not row:
            return api_error("NOT_FOUND", "Contact not found", 404)
        d = _row_to_dict(row)
        result = _contact_to_dict(d)
        result["vcard_raw"] = d.get("raw_vcard") or d.get("vcard_text", "")
        return api_response(result)
    finally:
        conn.close()


@bp.get("/contacts/search", summary="Search contacts", description="Searches contacts by name, email, or phone number. Returns simplified results with name and primary email. Requires `contacts:read` scope.", responses={"200": ContactSearchResponse, "400": ErrorResponse})
@require_api_token(scopes=["contacts:read"])
@require_scope("contacts", "read")
def api_search_contacts(query: SearchContactsQuery):
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    q = query.q
    if not q:
        return api_error("VALIDATION_ERROR", "Query parameter 'q' is required", 400)
    limit = min(query.max_results, 200)
    conn = _get_cache_conn(account_id, dek)
    try:
        from app.modules.contacts.services.cache_db import search_contacts_api
        rows = search_contacts_api(conn, q, limit=limit)
        rows = [_row_to_dict(r) for r in rows]
        items = [{"name": r.get("fn", ""), "email": r["emails"][0]["email"] if r.get("emails") else ""} for r in rows]
        return api_response(items)
    finally:
        conn.close()


@bp.delete("/contacts/<int:contact_id>", summary="Delete contact", description="Permanently deletes a contact by ID from both the CardDAV server and the local cache. Requires `contacts:write` scope.", responses={"204": EmptyResponse, "404": ErrorResponse})
@require_api_token(scopes=["contacts:write"])
@require_scope("contacts", "write")
def api_delete_contact(path: ContactPath):
    contact_id = path.contact_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    conn = _get_cache_conn(account_id, dek)
    try:
        row = db_get_contact(conn, contact_id)
        if not row:
            return api_error("NOT_FOUND", "Contact not found", 404)
        d = _row_to_dict(row)
        delete_contact_by_uid(conn, d["uid"])
        push_ui_event(g.api_context["customer_id"], "contacts", "contact_deleted", {"account_id": account_id, "contact_id": contact_id})
        return api_response(None, 204)
    finally:
        conn.close()


@bp.post("/contacts/bulk/delete", summary="Bulk delete contacts", description="Permanently deletes up to 100 contacts by ID from both the CardDAV server and the local cache. Requires `contacts:write` scope.", responses={"200": BulkResponse, "400": ErrorResponse})
@require_api_token(scopes=["contacts:write"])
@require_scope("contacts", "write")
def api_bulk_delete_contacts(body: BulkDeleteContactsBody):
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    items = body.items[:100]
    if not items:
        return api_error("VALIDATION_ERROR", "No items provided", 400)
    conn = _get_cache_conn(account_id, dek)
    succeeded = []
    failed = []
    try:
        for i, item in enumerate(items):
            cid = item.get("contact_id")
            if not cid:
                failed.append({"index": i, "error": {"code": "MISSING_ID"}})
                continue
            row = db_get_contact(conn, cid)
            if not row:
                failed.append({"index": i, "error": {"code": "NOT_FOUND"}})
                continue
            d = _row_to_dict(row)
            delete_contact_by_uid(conn, d["uid"])
            succeeded.append({"contact_id": cid})
        push_ui_event(g.api_context["customer_id"], "contacts", "contacts_deleted", {"account_id": account_id})
        return api_response({"succeeded": succeeded, "failed": failed})
    finally:
        conn.close()


def _get_carddav_session(account):
    from app.shared.models.core import Domain
    domain = Domain.query.filter_by(id=account.domain_id).first()
    config = {}
    if domain.carddav_host:
        config = {"host": domain.carddav_host, "port": domain.carddav_port or 5232, "use_tls": domain.carddav_use_tls}
    if not config:
        raise ApiError("NOT_CONFIGURED", "CardDAV is not configured for this domain", 400)
    base_url = f"{'https' if config['use_tls'] else 'http'}://{config['host']}:{config['port']}"
    from app.modules.mail.services.secrets import decrypt_with_key
    from app.api.controllers.helpers import g as flask_g
    dek = flask_g.api_context["dek"]
    password = decrypt_with_key(account.encrypted_secret, dek)
    from app.modules.contacts.services import carddav
    s, abook_url, _ = carddav.discover_address_book(base_url, account.username, password)
    if not abook_url:
        abook_url = carddav.create_address_book(s, base_url, account.username)
    return s, abook_url, password


def _build_vcard_data_from_api(data):
    return {
        "fn": data.get("fn"),
        "email_work": data.get("email_work"),
        "email_home": data.get("email_home"),
        "tel_work": data.get("phone_work"),
        "tel_cell": data.get("phone_cell"),
        "tel_home": data.get("phone_home"),
        "org": data.get("organization"),
        "title": data.get("title"),
        "note": data.get("note"),
    }


def _merge_vcard_data(existing_parsed, updates):
    merged = {}
    for key in ("fn", "email_work", "email_home", "tel_work", "tel_cell", "tel_home", "org", "title", "note"):
        if key in updates and updates[key] is not None:
            merged[key] = updates[key]
        else:
            merged[key] = existing_parsed.get(key, "")
    return merged


@bp.post("/contacts", summary="Create contact", description="Creates a new contact via CardDAV and caches it locally. At least a formatted name (fn) or work email is required. Requires `contacts:write` scope.", responses={"201": ContactDetailResponse, "400": ErrorResponse})
@require_api_token(scopes=["contacts:write"])
@require_scope("contacts", "write")
def api_create_contact(body: CreateContactBody):
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    account = CustomerAccount.query.filter_by(id=account_id, is_active=True).first()
    if not account:
        return api_error("NOT_FOUND", "Account not found", 404)
    data = body.model_dump()
    if not data.get("fn") and not data.get("email_work"):
        return api_error("VALIDATION_ERROR", "'fn' or 'email_work' is required", 400)
    vcard_data = _build_vcard_data_from_api(data)
    for key in vcard_data:
        if vcard_data[key] is None:
            vcard_data[key] = ""
    from app.modules.contacts.services.vcard import generate_vcard, extract_uid
    vcard_text = generate_vcard(vcard_data)
    uid = extract_uid(vcard_text)
    try:
        s, abook_url, _ = _get_carddav_session(account)
        from app.modules.contacts.services import carddav
        href, etag = carddav.create_contact(s, abook_url, vcard_text)
    except ApiError:
        raise
    except Exception as e:
        return api_error("CARDDAV_ERROR", str(e), 502)
    conn = _get_cache_conn(account_id, dek)
    try:
        from app.modules.contacts.services.cache_db import upsert_contact
        upsert_contact(conn, uid, href, etag, vcard_text)
        row = db_get_contact_by_uid(conn, uid)
        result = _contact_to_dict(row) if row else {"uid": uid, "fn": vcard_data["fn"]}
    finally:
        conn.close()
    push_ui_event(g.api_context["customer_id"], "contacts", "contact_created", {"account_id": account_id, "uid": uid})
    return api_response(result, 201)


@bp.put("/contacts/<int:contact_id>", summary="Update contact", description="Updates an existing contact by merging provided fields with the existing vCard data, then syncs via CardDAV. Only non-null fields are updated. Requires `contacts:write` scope.", responses={"200": ContactDetailResponse, "404": ErrorResponse})
@require_api_token(scopes=["contacts:write"])
@require_scope("contacts", "write")
def api_update_contact(path: ContactPath, body: UpdateContactBody):
    contact_id = path.contact_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    account = CustomerAccount.query.filter_by(id=account_id, is_active=True).first()
    if not account:
        return api_error("NOT_FOUND", "Account not found", 404)
    conn = _get_cache_conn(account_id, dek)
    try:
        row = db_get_contact(conn, contact_id)
        if not row:
            return api_error("NOT_FOUND", "Contact not found", 404)
        d = _row_to_dict(row)
    finally:
        conn.close()
    uid = d.get("uid")
    data = body.model_dump()
    updates = _build_vcard_data_from_api(data)
    try:
        s, abook_url, _ = _get_carddav_session(account)
        from app.modules.contacts.services import carddav
        stored_href = d.get("href")
        href = stored_href
        if href and not href.startswith("http"):
            href = f"{abook_url.rstrip('/')}/{uid}.vcf"
        if href:
            fresh_vcard = carddav.get_contact(s, href)
            existing_parsed = parse_vcard(fresh_vcard)
        else:
            existing_parsed = parse_vcard(d.get("raw_vcard") or d.get("vcard_text") or "")
        merged = _merge_vcard_data(existing_parsed, updates)
    except ApiError:
        raise
    except Exception as e:
        return api_error("CARDDAV_ERROR", str(e), 502)
    from app.modules.contacts.services.vcard import generate_vcard
    vcard_text = generate_vcard(merged, uid=uid)
    try:
        if href:
            etag = carddav.update_contact(s, href, vcard_text, d.get("etag"))
        else:
            href, etag = carddav.create_contact(s, abook_url, vcard_text, uid=uid)
    except ApiError:
        raise
    except Exception as e:
        return api_error("CARDDAV_ERROR", str(e), 502)
    conn = _get_cache_conn(account_id, dek)
    try:
        from app.modules.contacts.services.cache_db import upsert_contact
        upsert_contact(conn, uid, href, etag, vcard_text)
        row = db_get_contact_by_uid(conn, uid)
        result = _contact_to_dict(row) if row else {"uid": uid, "fn": merged.get("fn", "")}
    finally:
        conn.close()
    push_ui_event(g.api_context["customer_id"], "contacts", "contact_updated", {"account_id": account_id, "uid": uid})
    return api_response(result)
