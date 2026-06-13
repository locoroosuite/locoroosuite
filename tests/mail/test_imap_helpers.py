from unittest.mock import MagicMock

from app.modules.mail.services.imap_client import parse_append_uid, delete_message_by_uid


def test_parse_append_uid_extracts_uid():
    data = [b"[APPENDUID 1682834567 42] APPEND completed."]
    assert parse_append_uid(data) == "42"


def test_parse_append_uid_returns_none_on_empty():
    assert parse_append_uid(None) is None
    assert parse_append_uid([]) is None


def test_parse_append_uid_returns_none_without_appenduid():
    data = [b"OK APPEND completed."]
    assert parse_append_uid(data) is None


def test_delete_message_by_uid():
    client = MagicMock()
    delete_message_by_uid(client, "42")
    client.uid.assert_called_once_with("STORE", "42", "+FLAGS", "(\\Deleted)")
    client.expunge.assert_called_once()


def test_has_text_content_empty_string():
    from app.modules.mail.controllers.compose import _has_text_content
    assert _has_text_content("") is False
    assert _has_text_content(None) is False


def test_has_text_content_only_tags():
    from app.modules.mail.controllers.compose import _has_text_content
    assert _has_text_content("<p></p>") is False
    assert _has_text_content("<p><br></p>") is False
    assert _has_text_content("<div>&nbsp;</div>") is False


def test_has_text_content_with_text():
    from app.modules.mail.controllers.compose import _has_text_content
    assert _has_text_content("<p>Hello</p>") is True
    assert _has_text_content("<p>Hello world</p>") is True
