import base64
import json
from email import encoders, message_from_bytes
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from flask import session

from app.modules.mail.controllers.helpers import (
    _build_reply_forward_prefill,
    _pending_sends,
    _pending_sends_lock,
)
from app.modules.mail.services import attachments as staging
from app.modules.mail.services.compose_attachments import (
    StagedFile,
    apply_inline_content_ids,
    build_message_root,
    rewrite_cid_srcs,
    stage_mime_attachments,
    strip_cid_imgs,
)

DATE_HEADER = "Mon, 05 Jan 2026 10:00:00 +0000"
BERLIN_DATE_DISPLAY = "Mon, 05 Jan 2026 at 11:00"


def _cache_row():
    row = {
        "uid": "77",
        "folder": "INBOX",
        "subject": "Quarterly report",
        "sender": "Alice <alice@example.com>",
        "recipients": "Bob <bob@example.com>",
        "date": DATE_HEADER,
        "snippet": "snippet text",
    }
    mock = MagicMock()
    mock.__getitem__.side_effect = row.__getitem__
    mock.keys.return_value = row.keys()
    return mock


def _make_raw_msg(text_plain=None, text_html=None, attachments=(), inline_parts=()):
    root = MIMEMultipart("mixed")
    root["From"] = "Alice <alice@example.com>"
    root["To"] = "Bob <bob@example.com>"
    root["Cc"] = "Carol <carol@example.com>"
    root["Subject"] = "Quarterly report"
    root["Date"] = DATE_HEADER
    alt = MIMEMultipart("alternative")
    if text_plain is not None:
        alt.attach(MIMEText(text_plain, "plain"))
    if text_html is not None:
        alt.attach(MIMEText(text_html, "html"))
    root.attach(alt)
    for filename, payload in attachments:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(payload)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        root.attach(part)
    for content_id, payload, mime in inline_parts:
        maintype, _, subtype = mime.partition("/")
        part = MIMEBase(maintype, subtype)
        part.set_payload(payload)
        encoders.encode_base64(part)
        part.add_header("Content-ID", f"<{content_id}>")
        part.add_header("Content-Disposition", "inline", filename=f"{content_id}.{subtype}")
        root.attach(part)
    return root


def _mock_account():
    account = MagicMock()
    account.id = 1
    account.email_address = "bob@example.com"
    account.cache_db_path = "/tmp/none.db"
    account.encrypted_secret = None
    return account


def _call_prefill(app, raw_msg, *, forward=False, reply_all=False, timezone_name=None):
    """Call _build_reply_forward_prefill inside a request context with IMAP/cache mocked."""
    with app.test_request_context("/app/mail/compose"):
        session["user_id"] = 1
        with (
            patch("app.modules.mail.controllers.helpers.get_user_key", return_value=b"k"),
            patch("app.modules.mail.controllers.helpers.open_cache"),
            patch("app.modules.mail.controllers.helpers.get_message", return_value=_cache_row()),
            patch("app.modules.mail.controllers.helpers.decrypt_with_key"),
            patch("app.modules.mail.controllers.helpers._imap_for_account") as mock_imap,
            patch("app.modules.mail.controllers.helpers.select_folder"),
            patch("app.modules.mail.controllers.helpers.fetch_message", return_value=raw_msg),
            patch("app.modules.mail.controllers.helpers.safe_logout"),
        ):
            mock_imap.return_value = (MagicMock(), MagicMock())
            return _build_reply_forward_prefill(
                _mock_account(),
                55,
                reply_all=reply_all,
                forward=forward,
                timezone_name=timezone_name,
            )


@pytest.fixture()
def staging_dir(app, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "MAIL_ATTACHMENTS_DIR", str(tmp_path))
    yield tmp_path
    monkeypatch.delitem(app.config, "MAIL_ATTACHMENTS_DIR", raising=False)


class TestForwardQuote:
    def test_forward_prefers_html_over_plain(self, app, staging_dir):
        raw = _make_raw_msg(
            text_plain="PLAINMARK plain body",
            text_html="<p>HTMLMARK <strong>bold</strong> body</p>",
        )
        prefill = _call_prefill(app, raw, forward=True)
        assert prefill is not None
        assert "HTMLMARK" in prefill["body_html"]
        assert "<strong>bold</strong>" in prefill["body_html"]
        assert "PLAINMARK" not in prefill["body_html"]
        assert prefill["subject"] == "Fwd: Quarterly report"
        assert prefill["to_addrs"] == ""

    def test_forward_plain_only_falls_back_to_escaped_text(self, app, staging_dir):
        raw = _make_raw_msg(text_plain="line1\nline2 <tag>")
        prefill = _call_prefill(app, raw, forward=True)
        assert "line1<br>line2 &lt;tag&gt;" in prefill["body_html"]

    def test_forward_strips_scripts(self, app, staging_dir):
        raw = _make_raw_msg(text_html="<p>hi</p><script>alert(1)</script>")
        prefill = _call_prefill(app, raw, forward=True)
        assert "alert" not in prefill["body_html"]
        assert "<p>hi</p>" in prefill["body_html"]

    def test_forward_gmail_header_block_and_user_timezone(self, app, staging_dir):
        raw = _make_raw_msg(text_html="<p>body</p>")
        prefill = _call_prefill(app, raw, forward=True, timezone_name="Europe/Berlin")
        assert "Forwarded message" in prefill["body_html"]
        assert "From:" in prefill["body_html"]
        assert "Subject:" in prefill["body_html"]
        assert BERLIN_DATE_DISPLAY in prefill["body_html"]
        assert DATE_HEADER not in prefill["body_html"]

    def test_forward_subject_no_double_prefix(self, app, staging_dir):
        row = _cache_row()
        row.__getitem__.side_effect = lambda k: {
            "uid": "77",
            "folder": "INBOX",
            "subject": "Fwd: already forwarded",
            "sender": "Alice <alice@example.com>",
            "recipients": "Bob <bob@example.com>",
            "date": DATE_HEADER,
            "snippet": "",
        }[k]
        raw = _make_raw_msg(text_html="<p>body</p>")
        with (
            app.test_request_context("/app/mail/compose"),
            patch("app.modules.mail.controllers.helpers.get_user_key", return_value=b"k"),
            patch("app.modules.mail.controllers.helpers.open_cache"),
            patch("app.modules.mail.controllers.helpers.get_message", return_value=row),
            patch("app.modules.mail.controllers.helpers.decrypt_with_key"),
            patch("app.modules.mail.controllers.helpers._imap_for_account") as mock_imap,
            patch("app.modules.mail.controllers.helpers.select_folder"),
            patch("app.modules.mail.controllers.helpers.fetch_message", return_value=raw),
            patch("app.modules.mail.controllers.helpers.safe_logout"),
        ):
            mock_imap.return_value = (MagicMock(), MagicMock())
            prefill = _build_reply_forward_prefill(_mock_account(), 55, forward=True)
        assert prefill["subject"] == "Fwd: already forwarded"

    def test_reply_quote_keeps_summary_and_prefers_html(self, app, staging_dir):
        raw = _make_raw_msg(
            text_plain="plain body",
            text_html="<p>html <em>reply</em> body</p>",
        )
        prefill = _call_prefill(app, raw)
        assert "wrote:" in prefill["body_html"]
        assert "html <em>reply</em> body" in prefill["body_html"]
        assert "plain body" not in prefill["body_html"]
        assert prefill["subject"] == "Re: Quarterly report"
        assert prefill["to_addrs"] == "Alice <alice@example.com>"
        assert "attachments" not in prefill

    def test_reply_strips_inline_cid_images(self, app, staging_dir):
        raw = _make_raw_msg(
            text_html='<p>body</p><img src="cid:img1" alt="inline">',
            inline_parts=[("img1", b"\x89PNG-fake", "image/png")],
        )
        prefill = _call_prefill(app, raw)
        assert 'src="cid:img1"' not in prefill["body_html"]
        assert "attachments" not in prefill


class TestForwardAttachmentStaging:
    def test_forward_stages_all_attachments(self, app, staging_dir):
        raw = _make_raw_msg(
            text_html="<p>body</p>",
            attachments=[("report.pdf", b"pdf-data"), ("data.csv", b"csv-data")],
        )
        prefill = _call_prefill(app, raw, forward=True)
        assert prefill["compose_session_id"]
        names = {a["name"] for a in prefill["attachments"]}
        assert names == {"report.pdf", "data.csv"}
        sid = prefill["compose_session_id"]
        with app.app_context():
            staged = staging.list_staged(1, sid)
        assert {s["name"] for s in staged} == names

    def test_forward_inline_images_keep_content_id_and_render_as_data_url(self, app, staging_dir):
        raw = _make_raw_msg(
            text_html='<p>body</p><img src="cid:img1">',
            inline_parts=[("img1", b"\x89PNG-fake", "image/png")],
        )
        prefill = _call_prefill(app, raw, forward=True)
        expected_b64 = base64.b64encode(b"\x89PNG-fake").decode("ascii")
        assert f'src="data:image/png;base64,{expected_b64}"' in prefill["body_html"]
        sid = prefill["compose_session_id"]
        assert sid
        with app.app_context():
            items = staging.list_staged(1, sid)
        assert len(items) == 1
        # list_staged shape: id/name/size/mime (content_id lives in meta.json)
        meta_files = list(Path(staging_dir, "1", sid).glob("*/meta.json"))
        metas = [json.loads(p.read_text()) for p in meta_files]
        assert any(m.get("content_id") == "img1" for m in metas)

    def test_forward_over_limit_attachment_skipped_with_notice(self, app, staging_dir, monkeypatch):
        monkeypatch.setitem(app.config, "MAIL_ATTACHMENT_MAX_FILE_BYTES", 4)
        raw = _make_raw_msg(
            text_html="<p>body</p>",
            attachments=[("big.bin", b"toolarge"), ("ok.txt", b"ok")],
        )
        prefill = _call_prefill(app, raw, forward=True)
        assert "attachment_notice" in prefill
        assert "big.bin" in prefill["attachment_notice"]
        names = {a["name"] for a in prefill["attachments"]}
        assert names == {"ok.txt"}

    def test_forward_imap_failure_still_builds_quote_from_snippet(self, app, staging_dir):
        with (
            app.test_request_context("/app/mail/compose"),
            patch("app.modules.mail.controllers.helpers.get_user_key", return_value=b"k"),
            patch("app.modules.mail.controllers.helpers.open_cache"),
            patch("app.modules.mail.controllers.helpers.get_message", return_value=_cache_row()),
            patch("app.modules.mail.controllers.helpers.decrypt_with_key"),
            patch(
                "app.modules.mail.controllers.helpers._imap_for_account",
                side_effect=RuntimeError("imap down"),
            ),
        ):
            prefill = _build_reply_forward_prefill(_mock_account(), 55, forward=True)
        assert prefill is not None
        assert prefill["subject"] == "Fwd: Quarterly report"
        assert "snippet text" in prefill["body_html"]
        assert "attachments" not in prefill


class TestComposeAttachmentHelpers:
    def test_rewrite_cid_srcs(self):
        out = rewrite_cid_srcs('<p><img src="cid:img1"></p>', {"img1": "data:image/png;base64,AAA"})
        assert out == '<p><img src="data:image/png;base64,AAA"></p>'

    def test_rewrite_cid_srcs_single_quotes_and_unknown_cid(self):
        html = "<p><img src='cid:img1'><img src=\"cid:unknown\"></p>"
        out = rewrite_cid_srcs(html, {"img1": "data:image/gif;base64,BBB"})
        assert '<img src="data:image/gif;base64,BBB">' in out
        assert 'src="cid:unknown"' in out

    def test_strip_cid_imgs(self):
        html = '<p>keep</p><img src="cid:x" alt="me"><img src="https://evil.example/a.png">'
        out = strip_cid_imgs(html)
        assert "keep" in out
        assert "cid:" not in out
        assert "https://evil.example/a.png" in out

    def test_apply_inline_content_ids(self):
        staged: list[StagedFile] = [
            {
                "name": "img1.png",
                "mime": "image/png",
                "data": b"\x89PNG-fake",
                "content_id": "img1",
            },
            {"name": "doc.pdf", "mime": "application/pdf", "data": b"pdf"},
        ]
        b64 = base64.b64encode(b"\x89PNG-fake").decode("ascii")
        html = f'<p><img src="data:image/png;base64,{b64}"></p>'
        out = apply_inline_content_ids(html, staged)
        assert out == '<p><img src="cid:img1"></p>'

    def test_stage_mime_attachments_no_parts(self):
        raw = _make_raw_msg(text_html="<p>only body</p>")
        result = stage_mime_attachments(1, raw)
        assert result["compose_session_id"] is None
        assert result["attachments"] == []
        assert result["notices"] == []

    def test_build_message_root_related_structure(self):
        staged: list[StagedFile] = [
            {
                "name": "img1.png",
                "mime": "image/png",
                "data": b"png",
                "content_id": "img1",
            },
            {"name": "doc.pdf", "mime": "application/pdf", "data": b"pdf"},
        ]
        root = build_message_root(
            [("From", "a@x.com"), ("To", "b@y.com"), ("Subject", "S")],
            "plain",
            '<p><img src="cid:img1"></p>',
            staged,
        )
        assert root.get_content_type() == "multipart/mixed"
        related = None
        regular = []
        for part in root.walk():
            if part.get_content_type() == "multipart/related":
                related = part
            if part.get_content_disposition() == "attachment":
                regular.append(part)
        assert related is not None
        inline = [p for p in related.walk() if p.get_content_disposition() == "inline"]
        assert len(inline) == 1
        assert inline[0].get("Content-ID") == "<img1>"
        assert len(regular) == 1
        assert regular[0].get_filename() == "doc.pdf"

    def test_build_message_root_plain_when_no_inline(self):
        staged: list[StagedFile] = [{"name": "doc.pdf", "mime": "application/pdf", "data": b"pdf"}]
        root = build_message_root([("Subject", "S")], "plain", "<p>html</p>", staged)
        types = [p.get_content_type() for p in root.walk()]
        assert "multipart/related" not in types
        assert "multipart/alternative" in types


class TestSendWithInlineImages:
    def test_send_builds_related_and_cid_refs(self, app, authed_client, staging_dir):
        client, user_id, account_id = authed_client
        sid = "sess" + "0" * 28
        fid = "file" + "0" * 27
        with app.app_context():
            staging.stage_file(
                user_id, sid, fid, b"\x89PNG-fake", "img1.png", "image/png", content_id="img1"
            )
        b64 = base64.b64encode(b"\x89PNG-fake").decode("ascii")
        try:
            with (
                patch("app.modules.mail.controllers.compose.decrypt_with_key"),
                patch("app.modules.mail.controllers.compose._start_send_worker"),
                patch("app.modules.mail.controllers.compose._cleanup_pending_sends"),
            ):
                resp = client.post(
                    "/app/mail/send",
                    data={
                        "account_id": account_id,
                        "to": "dest@example.com",
                        "subject": "Fwd: with inline",
                        "body_html": f'<p>see</p><img src="data:image/png;base64,{b64}">',
                        "compose_session_id": sid,
                        "attachment_ids": fid,
                    },
                )
            assert resp.status_code == 302
            with _pending_sends_lock:
                tokens = [t for t in _pending_sends if _pending_sends[t].get("user_id") == user_id]
                assert tokens
                msg_bytes = _pending_sends[tokens[0]]["msg"]
        finally:
            with _pending_sends_lock:
                for t in list(_pending_sends):
                    if _pending_sends[t].get("user_id") == user_id:
                        _pending_sends.pop(t, None)
        parsed = message_from_bytes(msg_bytes)
        assert parsed.get_content_type() == "multipart/mixed"
        related = None
        for part in parsed.walk():
            if part.get_content_type() == "multipart/related":
                related = part
        assert related is not None
        html_payloads = [
            bytes(p.get_payload(decode=True) or b"")
            for p in related.walk()
            if p.get_content_type() == "text/html"
        ]
        assert any(b'src="cid:img1"' in payload for payload in html_payloads)
        inline = [p for p in related.walk() if p.get_content_disposition() == "inline"]
        assert len(inline) == 1
        assert inline[0].get("Content-ID") == "<img1>"
