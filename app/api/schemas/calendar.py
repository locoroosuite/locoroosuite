from __future__ import annotations

from pydantic import BaseModel, Field


class CalendarItem(BaseModel):
    id: int = Field(..., description="Unique calendar ID")
    uid: str | None = Field(None, description="CalDAV calendar UID")
    name: str = Field("", description="Calendar display name")
    color: str = Field("#4285f4", description="Hex color code")
    is_default: bool = Field(False, description="Whether this is the default calendar")


class CalendarListResponse(BaseModel):
    data: list[CalendarItem] = Field(..., description="List of calendars")


class CalendarPath(BaseModel):
    calendar_id: int = Field(..., description="Calendar ID")


class DeleteCalendarBody(BaseModel):
    confirm: bool = Field(..., description="Must be true to confirm deletion")


class CreateCalendarBody(BaseModel):
    name: str = Field(..., description="Calendar display name")
    color: str = Field(default="#4285f4", description="Hex color code")


class UpdateCalendarBody(BaseModel):
    name: str | None = Field(default=None, description="Calendar display name")
    color: str | None = Field(default=None, description="Hex color code")


class EventItem(BaseModel):
    id: int = Field(..., description="Unique event ID")
    uid: str | None = Field(None, description="CalDAV event UID")
    summary: str = Field("", description="Event title")
    description: str = Field("", description="Event description")
    location: str = Field("", description="Event location")
    start: str | None = Field(None, description="Start datetime (ISO 8601)")
    end: str | None = Field(None, description="End datetime (ISO 8601)")
    is_all_day: bool = Field(False, description="Whether this is an all-day event")
    status: str = Field("", description="Event status (e.g. CONFIRMED, TENTATIVE, CANCELLED)")
    calendar_id: int | None = Field(None, description="Calendar ID this event belongs to")


class EventListResponse(BaseModel):
    data: list[EventItem] = Field(..., description="List of events")
    pagination: dict | None = Field(None, description="Pagination metadata (next_cursor, has_more)")


class EventDetailResponse(BaseModel):
    data: EventItem = Field(..., description="Event details")


class EventPath(BaseModel):
    event_id: int = Field(..., description="Event ID")


class ListEventsQuery(BaseModel):
    account_id: int | None = Field(default=None, description="Mail account ID (defaults to primary account)")
    max_results: int = Field(default=50, ge=1, le=200, description="Maximum results (1-200)")
    since: str | None = Field(default=None, description="Start of date range (ISO 8601)")
    until: str | None = Field(default=None, description="End of date range (ISO 8601)")


class SearchEventsQuery(BaseModel):
    q: str = Field(..., description="Search query")
    account_id: int | None = Field(default=None, description="Mail account ID (defaults to primary account)")
    max_results: int = Field(default=50, ge=1, le=200, description="Maximum results (1-200)")


class CreateEventBody(BaseModel):
    summary: str = Field(..., description="Event title")
    description: str = Field("", description="Event description")
    location: str = Field("", description="Event location")
    start: str | None = Field(default=None, description="Start datetime (ISO 8601)")
    end: str | None = Field(default=None, description="End datetime (ISO 8601)")
    calendar_id: int = Field(..., description="Calendar to create event in")
    is_all_day: bool = Field(False, description="Whether this is an all-day event")
    timezone: str | None = Field(None, description="IANA timezone (e.g. America/New_York)")
    attendees: list[dict] = Field(default_factory=list, description="Attendee list with email and optional name")
    reminders: list[dict] = Field(default_factory=list, description="Reminders (type + trigger_minutes)")
    recurrence: str | None = Field(default=None, description="RRULE string")


class UpdateEventBody(BaseModel):
    summary: str | None = Field(None, description="Event title")
    description: str | None = Field(None, description="Event description")
    location: str | None = Field(None, description="Event location")
    start: str | None = Field(None, description="Start datetime (ISO 8601)")
    end: str | None = Field(None, description="End datetime (ISO 8601)")
    calendar_id: int | None = Field(None, description="Calendar ID to move event to")
    is_all_day: bool | None = Field(None, description="Whether this is an all-day event")
    timezone: str | None = Field(None, description="IANA timezone (e.g. America/New_York)")
    attendees: list[dict] | None = Field(None, description="Attendee list with email and optional name")
    reminders: list[dict] | None = Field(None, description="Reminders (type + trigger_minutes)")
    recurrence: str | None = Field(None, description="RRULE string")


class FreeBusyBody(BaseModel):
    start: str = Field(..., description="Range start (ISO 8601)")
    end: str = Field(..., description="Range end (ISO 8601)")
    calendar_ids: list[int] | None = Field(default=None, description="Specific calendar IDs to check (defaults to all)")
    account_id: int | None = Field(default=None, description="Mail account ID (defaults to primary account)")


class BusyEntry(BaseModel):
    start: str | None = Field(None, description="Busy period start (ISO 8601)")
    end: str | None = Field(None, description="Busy period end (ISO 8601)")
    summary: str = Field("", description="Event summary during the busy period")
    calendar_id: int | None = Field(None, description="Calendar ID of the conflicting event")


class FreeBusyResponse(BaseModel):
    data: list[BusyEntry] = Field(..., description="Busy time entries")
