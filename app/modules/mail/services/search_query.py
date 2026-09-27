"""Gmail-style search query parsing (HLD U7.4).

Parses a user query such as ``from:alice has:attachment before:2025-01-31 report``
into a structured :class:`SearchFilters` object. Unknown ``prefix:`` tokens are
kept as literal text so nothing the user types is silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

_TOKEN_RE = re.compile(
    r'(?:(?P<prefix>[A-Za-z]+):(?:"(?P<quoted>[^"]*)"|(?P<bare>\S+)))'
    r'|(?:"(?P<text_quoted>[^"]*)")'
    r"|(?P<text>\S+)"
)

_FLAG_VALUES = {"unread", "read", "starred", "star", "draft"}
_ATTACHMENT_VALUES = {"attachment", "attachments"}
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d")
_DAY_SECONDS = 86399


def _parse_date_token(value: str) -> int | None:
    """Parse a ``YYYY-MM-DD`` / ``YYYY/MM/DD`` value to a UTC unix timestamp."""
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        return int(parsed.replace(tzinfo=UTC).timestamp())
    return None


def _parse_iso_token(value: str) -> int | None:
    """Parse an ISO 8601 datetime or date to a UTC unix timestamp."""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())


def quote_token(value: str) -> str:
    """Quote a token for display in a query string when it contains whitespace."""
    if re.search(r"\s", value):
        return f'"{value}"'
    return value


@dataclass
class SearchFilters:
    """Structured representation of a mail search query (HLD U7.4)."""

    text_terms: list[str] = field(default_factory=list)
    from_terms: list[str] = field(default_factory=list)
    to_terms: list[str] = field(default_factory=list)
    subject_terms: list[str] = field(default_factory=list)
    folders: list[str] = field(default_factory=list)
    is_flags: list[str] = field(default_factory=list)
    has_attachment: bool | None = None
    filenames: list[str] = field(default_factory=list)
    after_ts: int | None = None
    before_ts: int | None = None
    after_raw: str | None = None
    before_raw: str | None = None

    @property
    def is_empty(self) -> bool:
        return not (
            self.text_terms
            or self.from_terms
            or self.to_terms
            or self.subject_terms
            or self.folders
            or self.is_flags
            or self.has_attachment
            or self.filenames
            or self.after_ts is not None
            or self.before_ts is not None
        )

    def to_query(self) -> str:
        """Rebuild a canonical query string from these filters."""
        tokens: list[str] = []
        for value in self.folders:
            tokens.append(f"folder:{quote_token(value)}")
        for value in self.from_terms:
            tokens.append(f"from:{quote_token(value)}")
        for value in self.to_terms:
            tokens.append(f"to:{quote_token(value)}")
        for value in self.subject_terms:
            tokens.append(f"subject:{quote_token(value)}")
        for value in self.is_flags:
            tokens.append(f"is:{value}")
        if self.has_attachment:
            tokens.append("has:attachment")
        for value in self.filenames:
            tokens.append(f"filename:{quote_token(value)}")
        if self.after_raw:
            tokens.append(f"after:{self.after_raw}")
        if self.before_raw:
            tokens.append(f"before:{self.before_raw}")
        for value in self.text_terms:
            tokens.append(quote_token(value))
        return " ".join(tokens)

    def chips(self) -> list[dict[str, str]]:
        """Active operator filters, for rendering as removable search tags."""
        result: list[dict[str, str]] = []
        for value in self.folders:
            result.append({"key": "folder", "value": value})
        for value in self.from_terms:
            result.append({"key": "from", "value": value})
        for value in self.to_terms:
            result.append({"key": "to", "value": value})
        for value in self.subject_terms:
            result.append({"key": "subject", "value": value})
        for value in self.is_flags:
            result.append({"key": "is", "value": value})
        if self.has_attachment:
            result.append({"key": "has", "value": "attachment"})
        for value in self.filenames:
            result.append({"key": "filename", "value": value})
        if self.after_raw:
            result.append({"key": "after", "value": self.after_raw})
        if self.before_raw:
            result.append({"key": "before", "value": self.before_raw})
        return result

    def without(self, key: str, value: str) -> SearchFilters:
        """Return a copy of these filters with one operator filter removed."""
        clone = SearchFilters(
            text_terms=list(self.text_terms),
            from_terms=list(self.from_terms),
            to_terms=list(self.to_terms),
            subject_terms=list(self.subject_terms),
            folders=list(self.folders),
            is_flags=[f for f in self.is_flags if not (key == "is" and f == value)],
            has_attachment=None if key == "has" else self.has_attachment,
            filenames=list(self.filenames),
            after_ts=self.after_ts,
            before_ts=self.before_ts,
            after_raw=None if key == "after" else self.after_raw,
            before_raw=None if key == "before" else self.before_raw,
        )
        if key == "folder" and value in clone.folders:
            clone.folders.remove(value)
        elif key == "from" and value in clone.from_terms:
            clone.from_terms.remove(value)
        elif key == "to" and value in clone.to_terms:
            clone.to_terms.remove(value)
        elif key == "subject" and value in clone.subject_terms:
            clone.subject_terms.remove(value)
        elif key == "filename" and value in clone.filenames:
            clone.filenames.remove(value)
        if key in ("after", "before"):
            clone.after_ts = None if key == "after" else clone.after_ts
            clone.before_ts = None if key == "before" else clone.before_ts
        return clone

    def with_api_filters(
        self,
        folder: str | None = None,
        unread: bool | None = None,
        flagged: bool | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> SearchFilters:
        """Merge REST-API filter parameters (HLD U15.34) into this query."""
        clone = SearchFilters(
            text_terms=list(self.text_terms),
            from_terms=list(self.from_terms),
            to_terms=list(self.to_terms),
            subject_terms=list(self.subject_terms),
            folders=list(self.folders),
            is_flags=list(self.is_flags),
            has_attachment=self.has_attachment,
            filenames=list(self.filenames),
            after_ts=self.after_ts,
            before_ts=self.before_ts,
            after_raw=self.after_raw,
            before_raw=self.before_raw,
        )
        if folder:
            clone.folders.append(folder)
        if unread is True:
            clone.is_flags.append("unread")
        elif unread is False:
            clone.is_flags.append("read")
        if flagged is True:
            clone.is_flags.append("starred")
        if since:
            ts = _parse_iso_token(since)
            if ts is not None:
                clone.after_ts = ts if clone.after_ts is None else max(clone.after_ts, ts)
                clone.after_raw = since
        if until:
            ts = _parse_iso_token(until)
            if ts is not None:
                clone.before_ts = ts if clone.before_ts is None else min(clone.before_ts, ts)
                clone.before_raw = until
        return clone


def parse_search_query(query: str | None) -> SearchFilters:
    """Parse a user query string into :class:`SearchFilters`."""
    filters = SearchFilters()
    if not query:
        return filters
    for match in _TOKEN_RE.finditer(query):
        prefix = match.group("prefix")
        if prefix is not None:
            value = (
                match.group("quoted") if match.group("quoted") is not None else match.group("bare")
            )
            lowered = prefix.lower()
            if not value:
                filters.text_terms.append(match.group(0))
            elif lowered in ("from", "to", "subject"):
                target = {
                    "from": filters.from_terms,
                    "to": filters.to_terms,
                    "subject": filters.subject_terms,
                }[lowered]
                target.append(value)
            elif lowered in ("folder", "in"):
                filters.folders.append(value)
            elif lowered == "is":
                flag = value.lower()
                if flag in _FLAG_VALUES:
                    filters.is_flags.append("starred" if flag == "star" else flag)
                else:
                    filters.text_terms.append(match.group(0))
            elif lowered == "has":
                if value.lower() in _ATTACHMENT_VALUES:
                    filters.has_attachment = True
                else:
                    filters.text_terms.append(match.group(0))
            elif lowered == "filename":
                filters.filenames.append(value)
            elif lowered == "before":
                ts = _parse_date_token(value)
                if ts is None:
                    filters.text_terms.append(match.group(0))
                else:
                    filters.before_ts = ts + _DAY_SECONDS
                    filters.before_raw = value
            elif lowered == "after":
                ts = _parse_date_token(value)
                if ts is None:
                    filters.text_terms.append(match.group(0))
                else:
                    filters.after_ts = ts
                    filters.after_raw = value
            else:
                filters.text_terms.append(match.group(0))
            continue
        text = (
            match.group("text_quoted")
            if match.group("text_quoted") is not None
            else match.group("text")
        )
        if text:
            filters.text_terms.append(text)
    return filters
