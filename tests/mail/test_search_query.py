"""Unit tests for the Gmail-style search query parser (HLD U7.4)."""

from app.modules.mail.services.search_query import parse_search_query


def test_plain_text_query():
    f = parse_search_query("quarterly report")
    assert f.text_terms == ["quarterly", "report"]
    assert f.chips() == []
    assert f.to_query() == "quarterly report"


def test_all_operators_parsed():
    f = parse_search_query(
        'from:alice to:"Bob Smith" subject:offer folder:INBOX is:unread '
        "has:attachment filename:pdf before:2025-01-31 after:2024-12-01 hello"
    )
    assert f.from_terms == ["alice"]
    assert f.to_terms == ["Bob Smith"]
    assert f.subject_terms == ["offer"]
    assert f.folders == ["INBOX"]
    assert f.is_flags == ["unread"]
    assert f.has_attachment is True
    assert f.filenames == ["pdf"]
    assert f.before_raw == "2025-01-31"
    assert f.after_raw == "2024-12-01"
    assert f.text_terms == ["hello"]
    assert not f.is_empty


def test_in_is_alias_for_folder():
    assert parse_search_query("in:sent").folders == ["sent"]


def test_star_is_alias_for_starred():
    assert parse_search_query("is:star").is_flags == ["starred"]


def test_is_read_and_draft():
    assert parse_search_query("is:read").is_flags == ["read"]
    assert parse_search_query("is:draft").is_flags == ["draft"]


def test_unknown_prefix_kept_as_literal_text():
    f = parse_search_query("unknown:xyz http://example.com foo")
    assert f.text_terms == ["unknown:xyz", "http://example.com", "foo"]


def test_invalid_date_kept_as_literal_text():
    f = parse_search_query("before:not-a-date")
    assert f.text_terms == ["before:not-a-date"]
    assert f.before_ts is None


def test_unknown_is_value_kept_as_literal():
    f = parse_search_query("is:purple")
    assert f.text_terms == ["is:purple"]
    assert f.is_flags == []


def test_empty_and_none_queries():
    assert parse_search_query("").is_empty
    assert parse_search_query(None).is_empty
    assert parse_search_query("   ").is_empty


def test_date_semantics_inclusive_day_boundaries():
    before = parse_search_query("before:2025-01-31")
    after = parse_search_query("after:2025-01-31")
    # before is end-of-day, after is start-of-day: a message sent at any
    # moment on 2025-01-31 matches both.
    assert before.before_ts is not None
    assert after.after_ts is not None
    assert before.before_ts == after.after_ts + 86399


def test_date_accepts_slash_format():
    assert parse_search_query("before:2025/01/31").before_raw == "2025/01/31"


def test_to_query_roundtrip_is_stable():
    raw = 'from:alice to:"Bob Smith" is:unread has:attachment report'
    canonical = parse_search_query(raw).to_query()
    assert parse_search_query(canonical).to_query() == canonical


def test_chips_list_all_operators():
    chips = parse_search_query("from:alice is:unread before:2025-01-31").chips()
    assert {"key": "from", "value": "alice"} in chips
    assert {"key": "is", "value": "unread"} in chips
    assert {"key": "before", "value": "2025-01-31"} in chips


def test_without_removes_single_filter():
    f = parse_search_query("from:alice from:zoe is:unread report")
    g = f.without("from", "alice")
    assert g.from_terms == ["zoe"]
    assert g.is_flags == ["unread"]
    assert g.text_terms == ["report"]


def test_without_handles_date_filters():
    f = parse_search_query("after:2025-01-01 report")
    g = f.without("after", "2025-01-01")
    assert g.after_ts is None
    assert g.text_terms == ["report"]


def test_with_api_filters_merges():
    f = parse_search_query("report").with_api_filters(
        folder="INBOX", unread=True, since="2025-01-01", until="2025-06-01T12:00:00Z"
    )
    assert f.folders == ["INBOX"]
    assert "unread" in f.is_flags
    assert f.after_ts is not None
    assert f.before_ts is not None
    assert f.text_terms == ["report"]


def test_with_api_filters_invalid_since_is_ignored():
    f = parse_search_query("report").with_api_filters(since="not-a-date")
    assert f.after_ts is None
    assert f.text_terms == ["report"]


def test_with_api_filters_unread_false_means_read():
    f = parse_search_query("").with_api_filters(unread=False)
    assert "read" in f.is_flags


class TestToFormFields:
    def test_single_operators_map_to_fields(self):
        f = parse_search_query(
            "from:alice to:bob subject:report folder:INBOX filename:pdf "
            "before:2025-01-31 after:2024-12-01 has:attachment is:unread is:starred hello"
        )
        fields = f.to_form_fields()
        assert fields["f_from"] == "alice"
        assert fields["f_to"] == "bob"
        assert fields["f_subject"] == "report"
        assert fields["f_folder"] == "INBOX"
        assert fields["f_filename"] == "pdf"
        assert fields["f_before"] == "2025-01-31"
        assert fields["f_after"] == "2024-12-01"
        assert fields["f_attachment"] is True
        assert fields["f_unread"] is True
        assert fields["f_starred"] is True
        assert fields["q"] == "hello"

    def test_extra_values_and_flags_preserved_in_q(self):
        f = parse_search_query("from:alice from:zoe is:read is:draft report")
        fields = f.to_form_fields()
        assert fields["f_from"] == "alice"
        assert "from:zoe" in fields["q"]
        assert "is:read" in fields["q"]
        assert "is:draft" in fields["q"]
        assert "report" in fields["q"]

    def test_quoted_values_map_to_single_field(self):
        f = parse_search_query('from:"John Doe" folder:"Sent Items"')
        fields = f.to_form_fields()
        assert fields["f_from"] == "John Doe"
        assert fields["f_folder"] == "Sent Items"

    def test_empty_query_yields_blank_fields(self):
        fields = parse_search_query("").to_form_fields()
        assert fields["f_from"] == ""
        assert fields["f_attachment"] is False
        assert fields["q"] == ""

    def test_panel_roundtrip_never_loses_criteria(self):
        """U7.5: parse → form fields → f_* tokens + leftover q must re-parse
        to equivalent filters (fields + checkboxes + words)."""
        from app.modules.mail.controllers.search import _advanced_field_tokens

        raw = "from:alice from:zoe is:draft is:starred has:attachment report"
        fields = parse_search_query(raw).to_form_fields()
        tokens = _advanced_field_tokens(
            {
                "f_from": fields["f_from"],
                "f_to": fields["f_to"],
                "f_subject": fields["f_subject"],
                "f_folder": fields["f_folder"],
                "f_filename": fields["f_filename"],
                "f_after": fields["f_after"],
                "f_before": fields["f_before"],
                "f_attachment": fields["f_attachment"],
                "f_unread": fields["f_unread"],
                "f_starred": fields["f_starred"],
            }
        )
        merged = parse_search_query(" ".join([fields["q"], *tokens]))
        assert sorted(merged.from_terms) == ["alice", "zoe"]
        assert sorted(merged.is_flags) == ["draft", "starred"]
        assert merged.has_attachment is True
        assert merged.text_terms == ["report"]
