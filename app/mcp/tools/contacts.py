from __future__ import annotations

import logging
from typing import Annotated, Any

from flask import Flask
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from app.mcp.auth import McpAuthError
from app.mcp.errors import resilient_tool
from app.mcp.helpers import err, ok, ok_paginated, resolve_read, resolve_write
from app.mcp.schemas import BulkContactIdItem, ensure_typed
from app.mcp.tools.mail import _ServiceConnectionError

_AccId = Annotated[int | None, Field(description="Account ID (uses default account if omitted)")]

_contacts_logger = logging.getLogger(__name__)


def _row_to_dict(row) -> dict[str, Any]:
    assert row is not None
    return {k: row[k] for k in row.keys()}


def _get_cache_conn(account_id, dek, flask_app):
    from app.shared.models.core import CustomerAccount
    from app.shared.db import db
    from app.modules.contacts.services.cache import get_cache_path
    from app.modules.contacts.services.cache_db import open_cache
    account = db.session.get(CustomerAccount, account_id)
    if not account:
        raise McpAuthError("NOT_FOUND", f"Account {account_id} not found")
    path = get_cache_path(account)
    return open_cache(path, dek)


def _contact_to_dict(row) -> dict[str, Any]:
    d: dict[str, Any] = _row_to_dict(row) if not isinstance(row, dict) else row
    from app.modules.contacts.services.vcard import parse_vcard
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


def _get_carddav_session(account, dek, flask_app):
    from app.shared.models.core import Domain
    from app.shared.db import db
    from app.modules.mail.services.secrets import decrypt_with_key
    domain = db.session.get(Domain, account.domain_id)
    if not domain or not domain.carddav_host:
        raise McpAuthError("NOT_CONFIGURED", "CardDAV is not configured for this domain")
    scheme = "https" if domain.carddav_use_tls else "http"
    base_url = f"{scheme}://{domain.carddav_host}:{domain.carddav_port or 5232}"
    try:
        password = decrypt_with_key(account.encrypted_secret, dek) if account.encrypted_secret else ""
    except Exception as exc:
        raise McpAuthError(
            "DEK_MISMATCH",
            "Your encryption key does not match the stored credentials. "
            "Reset your API access: go to Settings \u2192 API \u2192 Disable, then re-enable and create a new token.",
        ) from exc
    try:
        from app.modules.contacts.services import carddav
        s, abook_url, _ = carddav.discover_address_book(base_url, account.username, password)
        if not abook_url:
            abook_url = carddav.create_address_book(s, base_url, account.username)
        return s, abook_url, password
    except McpAuthError:
        raise
    except Exception as exc:
        raise _ServiceConnectionError("CardDAV", base_url, exc) from exc


def _build_vcard_data(data):
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


def _merge_vcard_data(existing, updates):
    merged = {}
    for key in ("fn", "email_work", "email_home", "tel_work", "tel_cell", "tel_home", "org", "title", "note"):
        if key in updates and updates[key] is not None:
            merged[key] = updates[key]
        else:
            merged[key] = existing.get(key, "")
    return merged


def register(mcp: FastMCP, flask_app: Flask) -> None:
    @mcp.tool(
        name="contacts_list",
        title="List Contacts",
        description="List contacts in the user's address book. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def contacts_list(
        account_id: _AccId = None,
        q: Annotated[str | None, Field(description="Search query to filter contacts by name or email")] = None,
        sort: Annotated[str | None, Field(description="Sort order: 'name' or 'email'")] = None,
        max_results: Annotated[int | None, Field(description="Maximum number of contacts to return (1–200, default 50)", ge=1, le=200)] = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "contacts", account_id)
        limit = max_results or 50
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.contacts.services.cache_db import (
                    list_contacts as db_list, search_contacts as db_search, count_contacts,
                )
                rows = db_search(conn, q, per_page=limit) if q else db_list(conn, per_page=limit)
                total = count_contacts(conn)
                items = [_contact_to_dict(r) for r in rows]
            finally:
                conn.close()
        has_more = len(items) >= limit and len(items) < total
        return ok_paginated(items, has_more=has_more)

    @mcp.tool(
        name="contacts_get",
        title="Get Contact",
        description="Get full details of a specific contact including raw vCard data. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def contacts_get(
        contact_id: Annotated[int, Field(description="ID of the contact to retrieve")],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "contacts", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.contacts.services.cache_db import get_contact as db_get
                row = db_get(conn, contact_id)
                if not row:
                    return err("NOT_FOUND", "Contact not found")
                d = _row_to_dict(row)
                result = _contact_to_dict(d)
                result["vcard_raw"] = d.get("raw_vcard") or d.get("vcard_text", "")
            finally:
                conn.close()
        return ok(result)

    @mcp.tool(
        name="contacts_search",
        title="Search Contacts",
        description="Search contacts by name or email. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def contacts_search(
        q: Annotated[str, Field(description="Search query string (matched against name and email)")],
        max_results: Annotated[int | None, Field(description="Maximum number of results to return (1–200, default 50)", ge=1, le=200)] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "contacts", account_id)
        limit = max_results or 50
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.contacts.services.cache_db import search_contacts_api
                rows = search_contacts_api(conn, q, limit=limit)
                rows = [_row_to_dict(r) for r in rows]
                items = [{"name": r.get("fn", ""), "email": r["emails"][0]["email"] if r.get("emails") else ""} for r in rows]
            finally:
                conn.close()
        return ok(items)

    @mcp.tool(
        name="contacts_create",
        title="Create Contact",
        description="Create a new contact via CardDAV.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def contacts_create(
        fn: Annotated[str, Field(description="Full name of the contact")],
        email_work: Annotated[str | None, Field(description="Work email address")] = None,
        email_home: Annotated[str | None, Field(description="Home email address")] = None,
        phone_work: Annotated[str | None, Field(description="Work phone number")] = None,
        phone_cell: Annotated[str | None, Field(description="Cell/mobile phone number")] = None,
        phone_home: Annotated[str | None, Field(description="Home phone number")] = None,
        organization: Annotated[str | None, Field(description="Company or organization name")] = None,
        title: Annotated[str | None, Field(description="Job title")] = None,
        note: Annotated[str | None, Field(description="Free-form notes about the contact")] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "contacts", account_id)
        if not fn and not email_work:
            return err("VALIDATION_ERROR", "'fn' or 'email_work' is required")
        data = {"fn": fn, "email_work": email_work, "email_home": email_home, "phone_work": phone_work, "phone_cell": phone_cell, "phone_home": phone_home, "organization": organization, "title": title, "note": note}
        vcard_data = _build_vcard_data(data)
        for key in vcard_data:
            if vcard_data[key] is None:
                vcard_data[key] = ""
        from app.modules.contacts.services.vcard import generate_vcard, extract_uid
        vcard_text = generate_vcard(vcard_data)
        uid = extract_uid(vcard_text)
        with flask_app.app_context():
            from app.shared.models.core import CustomerAccount
            from app.shared.db import db
            account = db.session.get(CustomerAccount, aid)
            if not account:
                return err("NOT_FOUND", "Account not found")
            try:
                s, abook_url, _ = _get_carddav_session(account, dek, flask_app)
                from app.modules.contacts.services import carddav
                href, etag = carddav.create_contact(s, abook_url, vcard_text)
            except McpAuthError:
                raise
            except Exception as exc:
                return err("CARDDAV_ERROR", f"CardDAV operation failed: {exc}")
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.contacts.services.cache_db import upsert_contact, get_contact_by_uid as db_get_by_uid
                upsert_contact(conn, uid, href, etag, vcard_text)
                row = db_get_by_uid(conn, uid)
                result = _contact_to_dict(row) if row else {"uid": uid, "fn": vcard_data["fn"]}
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event
        push_ui_event(ctx["customer_id"], "contacts", "contact_created", {"account_id": aid, "uid": uid})
        return ok(result)

    @mcp.tool(
        name="contacts_update",
        title="Update Contact",
        description="Update an existing contact via CardDAV.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def contacts_update(
        contact_id: Annotated[int, Field(description="ID of the contact to update")],
        fn: Annotated[str | None, Field(description="Full name of the contact")] = None,
        email_work: Annotated[str | None, Field(description="Work email address")] = None,
        email_home: Annotated[str | None, Field(description="Home email address")] = None,
        phone_work: Annotated[str | None, Field(description="Work phone number")] = None,
        phone_cell: Annotated[str | None, Field(description="Cell/mobile phone number")] = None,
        phone_home: Annotated[str | None, Field(description="Home phone number")] = None,
        organization: Annotated[str | None, Field(description="Company or organization name")] = None,
        title: Annotated[str | None, Field(description="Job title")] = None,
        note: Annotated[str | None, Field(description="Free-form notes about the contact")] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "contacts", account_id)
        data = {"fn": fn, "email_work": email_work, "email_home": email_home, "phone_work": phone_work, "phone_cell": phone_cell, "phone_home": phone_home, "organization": organization, "title": title, "note": note}
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.contacts.services.cache_db import get_contact as db_get
                row = db_get(conn, contact_id)
                if not row:
                    return err("NOT_FOUND", "Contact not found")
                d = _row_to_dict(row)
            finally:
                conn.close()
            uid = d.get("uid")
            updates = _build_vcard_data(data)
            from app.shared.models.core import CustomerAccount
            from app.shared.db import db
            account = db.session.get(CustomerAccount, aid)
            if not account:
                return err("NOT_FOUND", "Account not found")
            try:
                s, abook_url, _ = _get_carddav_session(account, dek, flask_app)
                from app.modules.contacts.services import carddav
                from app.modules.contacts.services.vcard import parse_vcard, generate_vcard
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
            except McpAuthError:
                raise
            except Exception as exc:
                return err("CARDDAV_ERROR", f"CardDAV operation failed: {exc}")
            vcard_text = generate_vcard(merged, uid=uid)
            try:
                if href:
                    etag = carddav.update_contact(s, href, vcard_text, d.get("etag"))
                else:
                    href, etag = carddav.create_contact(s, abook_url, vcard_text, uid=uid)
            except McpAuthError:
                raise
            except Exception as exc:
                return err("CARDDAV_ERROR", f"CardDAV operation failed: {exc}")
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.contacts.services.cache_db import upsert_contact, get_contact_by_uid as db_get_by_uid
                upsert_contact(conn, uid, href, etag, vcard_text)
                row = db_get_by_uid(conn, uid)
                result = _contact_to_dict(row) if row else {"uid": uid, "fn": merged.get("fn", "")}
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event
        push_ui_event(ctx["customer_id"], "contacts", "contact_updated", {"account_id": aid, "uid": uid})
        return ok(result)

    @mcp.tool(
        name="contacts_delete",
        title="Delete Contact",
        description="Delete a contact by contact ID.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=True, idempotentHint=True),
    )
    @resilient_tool
    async def contacts_delete(
        contact_id: Annotated[int, Field(description="ID of the contact to delete")],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "contacts", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.contacts.services.cache_db import get_contact as db_get, delete_contact_by_uid
                row = db_get(conn, contact_id)
                if not row:
                    return err("NOT_FOUND", "Contact not found")
                d = _row_to_dict(row)
                delete_contact_by_uid(conn, d["uid"])
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event
        push_ui_event(ctx["customer_id"], "contacts", "contact_deleted", {"account_id": aid, "contact_id": contact_id})
        return ok()

    @mcp.tool(
        name="contacts_bulk_delete",
        title="Bulk Delete Contacts",
        description="Delete multiple contacts at once.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=True, idempotentHint=True),
    )
    @resilient_tool
    async def contacts_bulk_delete(
        items: Annotated[list[BulkContactIdItem], Field(description="Array of items to delete, each with a contact_id")],
        account_id: _AccId = None,
    ) -> str:
        if not items or len(items) > 100:
            return err("VALIDATION_ERROR", "Items must contain 1-100 entries")
        items = ensure_typed(items, BulkContactIdItem)
        ctx, aid, dek = resolve_write(flask_app, "contacts", account_id)
        succeeded = []
        failed: list[dict[str, Any]] = []
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.contacts.services.cache_db import get_contact as db_get, delete_contact_by_uid
                for i, v in enumerate(items):
                    row = db_get(conn, v.contact_id)
                    if not row:
                        failed.append({"index": i, "error": {"code": "NOT_FOUND"}})
                        continue
                    d = _row_to_dict(row)
                    delete_contact_by_uid(conn, d["uid"])
                    succeeded.append({"contact_id": v.contact_id})
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event
        push_ui_event(ctx["customer_id"], "contacts", "contacts_deleted", {"account_id": aid})
        return ok({"succeeded": succeeded, "failed": failed})
