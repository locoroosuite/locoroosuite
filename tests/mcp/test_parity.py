"""MCP parity guard (AGENTS.md: "Parity is test-enforced, not aspirational").

Enumerates the REST API routes under /api/v1/, the Python MCP tool registry,
and the TypeScript MCP client tool registrations, then asserts that every
endpoint in the PARITY table exists in all layers it declares. Adding a REST
endpoint without extending this table (and shipping the MCP + TS tools) fails
here — this is the enforcement mechanism for the three-layer parity rule.
"""

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
TS_TOOLS_DIR = REPO / "packages" / "locoroosuite-mcp" / "src" / "tools"

# (method, path-template, tool name — None marks an api-only endpoint)
# path-template uses the literal route (with <type:name> placeholders kept).
# Extend this table when adding REST endpoints; the test fails until the
# endpoint has MCP + TS tools (or is deliberately marked api-only).
PARITY: list[tuple[str, str, str | None]] = [
    # accounts / tokens — auth lifecycle, not agent tools
    ("GET", "/api/v1/accounts", None),
    ("GET", "/api/v1/tokens", None),
    ("DELETE", "/api/v1/tokens/<int:token_id>", None),
    # events — cross-process bridge endpoint (U27.4)
    ("POST", "/api/v1/events/publish", None),
    # mail
    ("GET", "/api/v1/mail/folders", "mail_list_folders"),
    ("POST", "/api/v1/mail/folders", "mail_create_folder"),
    ("POST", "/api/v1/mail/folders/<path:folder>/rename", "mail_rename_folder"),
    ("DELETE", "/api/v1/mail/folders/<path:folder>", "mail_delete_folder"),
    ("GET", "/api/v1/mail/folders/<path:folder>/messages", "mail_list_messages"),
    ("GET", "/api/v1/mail/messages/<int:message_id>", "mail_get_message"),
    ("PATCH", "/api/v1/mail/messages/<int:message_id>", "mail_update_flags"),
    ("DELETE", "/api/v1/mail/messages/<int:message_id>", "mail_delete_message"),
    ("POST", "/api/v1/mail/messages/<int:message_id>/move", "mail_move_message"),
    ("POST", "/api/v1/mail/messages/<int:message_id>/spam", "mail_report_spam"),
    ("POST", "/api/v1/mail/messages/<int:message_id>/not-spam", "mail_not_spam"),
    ("GET", "/api/v1/mail/messages/<int:message_id>/raw", "mail_get_raw_message"),
    (
        "GET",
        "/api/v1/mail/messages/<int:message_id>/attachments/<int:attachment_index>",
        "mail_get_attachment",
    ),
    (
        "GET",
        "/api/v1/mail/messages/<int:message_id>/attachments/<int:attachment_index>/view",
        "mail_view_attachment",
    ),
    ("GET", "/api/v1/mail/threads/<thread_id>", "mail_get_thread"),
    ("GET", "/api/v1/mail/search", "mail_search"),
    ("POST", "/api/v1/mail/messages", "mail_send"),
    ("POST", "/api/v1/mail/drafts", "mail_save_draft"),
    ("DELETE", "/api/v1/mail/drafts/<uid>", "mail_delete_draft"),
    ("POST", "/api/v1/mail/bulk/move", "mail_bulk_move"),
    ("POST", "/api/v1/mail/bulk/delete", "mail_bulk_delete"),
    ("POST", "/api/v1/mail/bulk/flag", "mail_bulk_flag"),
    # calendar
    ("GET", "/api/v1/calendar/calendars", "calendar_list_calendars"),
    ("POST", "/api/v1/calendar/calendars", "calendar_create_calendar"),
    ("PUT", "/api/v1/calendar/calendars/<int:calendar_id>", "calendar_update_calendar"),
    ("DELETE", "/api/v1/calendar/calendars/<int:calendar_id>", "calendar_delete_calendar"),
    ("GET", "/api/v1/calendar/calendars/<int:calendar_id>/events", "calendar_list_events"),
    ("GET", "/api/v1/calendar/events/<int:event_id>", "calendar_get_event"),
    ("POST", "/api/v1/calendar/events", "calendar_create_event"),
    ("PUT", "/api/v1/calendar/events/<int:event_id>", "calendar_update_event"),
    ("DELETE", "/api/v1/calendar/events/<int:event_id>", "calendar_delete_event"),
    ("GET", "/api/v1/calendar/search", "calendar_search_events"),
    ("POST", "/api/v1/calendar/free-busy", "calendar_check_free_busy"),
    # contacts
    ("GET", "/api/v1/contacts", "contacts_list"),
    ("POST", "/api/v1/contacts", "contacts_create"),
    ("GET", "/api/v1/contacts/<int:contact_id>", "contacts_get"),
    ("PUT", "/api/v1/contacts/<int:contact_id>", "contacts_update"),
    ("DELETE", "/api/v1/contacts/<int:contact_id>", "contacts_delete"),
    ("GET", "/api/v1/contacts/search", "contacts_search"),
    ("POST", "/api/v1/contacts/bulk/delete", "contacts_bulk_delete"),
    # chat
    ("GET", "/api/v1/chat/rooms", "chat_list_rooms"),
    ("POST", "/api/v1/chat/rooms", "chat_create_room"),
    ("GET", "/api/v1/chat/rooms/<string:room_id>/messages", "chat_list_messages"),
    ("POST", "/api/v1/chat/rooms/<string:room_id>/messages", "chat_send_message"),
    ("POST", "/api/v1/chat/rooms/<string:room_id>/join", "chat_join_room"),
    ("POST", "/api/v1/chat/rooms/<string:room_id>/leave", "chat_leave_room"),
    ("POST", "/api/v1/chat/rooms/<string:room_id>/invite", "chat_invite_user"),
    ("POST", "/api/v1/chat/rooms/<string:room_id>/read", "chat_mark_room_read"),
    ("POST", "/api/v1/chat/messages/<string:event_id>/edit", "chat_edit_message"),
    ("POST", "/api/v1/chat/messages/<string:event_id>/react", "chat_react"),
    ("POST", "/api/v1/chat/messages/<string:event_id>/redact", "chat_delete_message"),
    # docs
    ("GET", "/api/v1/docs/documents", "docs_list_documents"),
    ("POST", "/api/v1/docs/documents", "docs_create_document"),
    ("POST", "/api/v1/docs/documents/upload", "docs_upload_document"),
    ("GET", "/api/v1/docs/documents/<doc_id>", "docs_get_document"),
    ("PUT", "/api/v1/docs/documents/<doc_id>", "docs_rename_document"),
    ("DELETE", "/api/v1/docs/documents/<doc_id>", "docs_delete_document"),
    ("GET", "/api/v1/docs/documents/<doc_id>/content", "docs_read_content"),
    ("PUT", "/api/v1/docs/documents/<doc_id>/content", "docs_update_content"),
    ("GET", "/api/v1/docs/documents/<doc_id>/download", "docs_download_document"),
    ("GET", "/api/v1/docs/documents/<doc_id>/download/pdf", "docs_export_pdf"),
    ("POST", "/api/v1/docs/documents/<doc_id>/convert", "docs_convert_document"),
    ("POST", "/api/v1/docs/documents/<doc_id>/move", "docs_move_document"),
    ("GET", "/api/v1/docs/documents/<doc_id>/tags", "docs_get_tags"),
    ("PUT", "/api/v1/docs/documents/<doc_id>/tags", "docs_update_tags"),
    ("GET", "/api/v1/docs/tags", "docs_list_tags"),
    ("GET", "/api/v1/docs/documents/<doc_id>/drafts", "docs_list_drafts"),
    ("POST", "/api/v1/docs/documents/<doc_id>/drafts", "docs_create_draft"),
    ("POST", "/api/v1/docs/documents/<doc_id>/drafts/<draft_id>/apply", "docs_apply_draft"),
    ("DELETE", "/api/v1/docs/documents/<doc_id>/drafts/<draft_id>", "docs_discard_draft"),
    ("GET", "/api/v1/docs/folders", "docs_list_folders"),
    ("POST", "/api/v1/docs/folders", "docs_create_folder"),
    ("POST", "/api/v1/docs/folders/rename", "docs_rename_folder"),
    ("POST", "/api/v1/docs/folders/delete", "docs_delete_folder"),
]


def _normalize(path: str) -> str:
    return re.sub(r"<[^>]+>", "{}", path)


def _ts_tools():
    names = set()
    for path in sorted(TS_TOOLS_DIR.glob("*.ts")):
        names |= set(re.findall(r'registerTool\("([a-z_]+)"', path.read_text()))
    return names


@pytest.fixture()
def mcp_registry(app):
    """Register all MCP tools against a real app and return the tool names."""
    from mcp.server.mcpserver import MCPServer

    from app.mcp.tools.calendar import register as register_calendar
    from app.mcp.tools.chat import register as register_chat
    from app.mcp.tools.contacts import register as register_contacts
    from app.mcp.tools.docs import register as register_docs
    from app.mcp.tools.mail import register as register_mail

    server = MCPServer("parity")
    register_mail(server, app)
    register_calendar(server, app)
    register_contacts(server, app)
    register_docs(server, app)
    register_chat(server, app)
    tools = server._tool_manager._tools
    return set(tools.keys())


def _api_routes(app):
    """Enumerate /api/v1 routes from the API app (mounted via WSGI middleware,
    not present on the main app's url_map)."""
    from app.api.openapi import api_app

    routes = set()
    for rule in api_app.url_map.iter_rules():
        if not rule.rule.startswith("/v1/"):
            continue
        methods = rule.methods or set()
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            routes.add((method, _normalize("/api" + rule.rule)))
    return routes


def test_every_api_route_is_in_the_parity_table(app):
    routes = _api_routes(app)
    table = {(method, _normalize(path)) for method, path, _tool in PARITY}
    missing = routes - table
    extra = table - routes
    assert not missing, (
        f"REST routes missing from the parity table (add them + their MCP/TS tools "
        f"or mark them api-only): {sorted(missing)}"
    )
    assert not extra, f"Stale parity entries (route no longer exists): {sorted(extra)}"


def test_every_mapped_tool_exists_in_mcp_and_ts(app, mcp_registry):
    ts_tools = _ts_tools()
    missing_mcp = []
    missing_ts = []
    for _method, _path, tool in PARITY:
        if tool is None:
            continue
        if tool not in mcp_registry:
            missing_mcp.append(tool)
        if tool not in ts_tools:
            missing_ts.append(tool)
    assert not missing_mcp, f"MCP Python tools missing: {sorted(set(missing_mcp))}"
    assert not missing_ts, f"TypeScript MCP tools missing: {sorted(set(missing_ts))}"


def test_mcp_and_ts_tool_names_stay_in_sync(app, mcp_registry):
    ts_tools = _ts_tools()
    only_mcp = mcp_registry - ts_tools
    only_ts = ts_tools - mcp_registry
    assert not only_mcp, f"Tools exposed only by the Python MCP server: {sorted(only_mcp)}"
    assert not only_ts, f"Tools exposed only by the TypeScript client: {sorted(only_ts)}"


def test_ai_activity_tool_rules_cover_write_endpoints():
    """U27.2: REST endpoints that mutate user data should be recognisable by
    the ai_activity hook (a known tool name, not a generic fallback)."""
    from app.api.openapi import _api_tool_name

    checks = [
        ("POST", "/v1/mail/messages", "mail_send"),
        ("POST", "/v1/mail/drafts", "mail_save_draft"),
        ("DELETE", "/v1/mail/drafts/42", "mail_delete_draft"),
        ("POST", "/v1/calendar/events", "calendar_create_event"),
        ("PUT", "/v1/calendar/events/3", "calendar_update_event"),
        ("POST", "/v1/contacts", "contacts_create"),
    ]
    for method, path, expected in checks:
        assert _api_tool_name(method, path) == expected, (method, path)
