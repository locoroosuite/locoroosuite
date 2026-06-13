from __future__ import annotations

from pydantic import BaseModel, Field


class ContactItem(BaseModel):
    id: int = Field(..., description="Unique contact ID")
    uid: str | None = Field(None, description="vCard UID")
    fn: str = Field("", description="Formatted name")
    email_work: str = Field("", description="Work email address")
    email_home: str = Field("", description="Home email address")
    phone_work: str = Field("", description="Work phone number")
    phone_cell: str = Field("", description="Mobile phone number")
    phone_home: str = Field("", description="Home phone number")
    organization: str = Field("", description="Organization name")
    title: str = Field("", description="Job title")
    note: str = Field("", description="Free-form note")


class ContactDetail(ContactItem):
    vcard_raw: str = Field("", description="Raw vCard source")


class ContactListResponse(BaseModel):
    data: list[ContactItem] = Field(..., description="List of contacts")
    pagination: dict | None = Field(None, description="Pagination metadata (next_cursor, has_more)")


class ContactDetailResponse(BaseModel):
    data: ContactDetail = Field(..., description="Contact details")


class ContactPath(BaseModel):
    contact_id: int = Field(..., description="Contact ID")


class ListContactsQuery(BaseModel):
    account_id: int | None = Field(default=None, description="Mail account ID (defaults to primary account)")
    max_results: int = Field(default=50, ge=1, le=200, description="Maximum results (1-200)")
    page: int = Field(default=1, ge=1, description="Page number (1-based)")
    q: str | None = Field(default=None, description="Search filter")


class SearchContactsQuery(BaseModel):
    q: str = Field(..., description="Search query (name, email, or phone)")
    account_id: int | None = Field(default=None, description="Mail account ID (defaults to primary account)")
    max_results: int = Field(default=50, ge=1, le=200, description="Maximum results (1-200)")


class ContactSearchResultItem(BaseModel):
    name: str = Field(..., description="Contact display name")
    email: str = Field(..., description="Primary email address")


class ContactSearchResponse(BaseModel):
    data: list[ContactSearchResultItem] = Field(..., description="Search results")


class CreateContactBody(BaseModel):
    fn: str | None = Field(None, description="Formatted name")
    email_work: str | None = Field(None, description="Work email address")
    email_home: str | None = Field(None, description="Home email address")
    phone_work: str | None = Field(None, description="Work phone number")
    phone_cell: str | None = Field(None, description="Mobile phone number")
    phone_home: str | None = Field(None, description="Home phone number")
    organization: str | None = Field(None, description="Organization name")
    title: str | None = Field(None, description="Job title")
    note: str | None = Field(None, description="Free-form note")


class UpdateContactBody(CreateContactBody):
    pass


class BulkDeleteContactsBody(BaseModel):
    items: list[dict] = Field(..., description="Array of {contact_id}")
