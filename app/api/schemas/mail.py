from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, BeforeValidator


def _str_to_list(v):
    if isinstance(v, str):
        return [v]
    return v


_StringOrList = Annotated[list[str], BeforeValidator(_str_to_list)]


class FolderItem(BaseModel):
    id: str = Field(..., description="Folder name (used as ID)")
    name: str = Field(..., description="Display name")
    unread_count: int = Field(default=0, description="Unread message count")


class FolderListResponse(BaseModel):
    data: list[FolderItem] = Field(..., description="List of folders")


class FolderMutationResponse(BaseModel):
    data: dict = Field(..., description="Result of the folder operation")


class MessageItem(BaseModel):
    id: int = Field(..., description="Unique message ID")
    folder: str | None = Field(None, description="Folder containing the message")
    subject: str = Field("", description="Email subject line")
    from_: str = Field("", alias="from", description="Sender address")
    to: str = Field("", description="Recipient addresses (comma-separated)")
    cc: str = Field("", description="CC addresses (comma-separated)")
    date: str = Field("", description="Message date (RFC 2822)")
    flags: str = Field("", description="IMAP flags (JSON-encoded list)")
    snippet: str = Field("", description="Short preview of the message body")
    thread_id: str | None = Field(None, description="Conversation thread ID")
    unread: bool = Field(False, description="Whether the message is unread")
    flagged: bool = Field(False, description="Whether the message is flagged")

    model_config = {"populate_by_name": True}


class MessageDetail(MessageItem):
    body_plain: str = Field("", description="Plain-text body")
    body_html: str = Field("", description="HTML body")


class MessageListResponse(BaseModel):
    data: list[MessageItem] = Field(..., description="List of messages")
    pagination: dict | None = Field(None, description="Pagination metadata (next_cursor, has_more)")


class MessageDetailResponse(BaseModel):
    data: MessageDetail = Field(..., description="Message details")


class FolderPath(BaseModel):
    folder: str = Field(..., description="Folder name")


class CreateFolderBody(BaseModel):
    name: str = Field(..., description="Mailbox/folder name to create")
    parent: str | None = Field(default=None, description="Optional parent folder for nesting")
    account_id: int | None = Field(default=None, description="Mail account ID (defaults to primary account)")


class RenameFolderBody(BaseModel):
    name: str = Field(..., description="New folder name")
    account_id: int | None = Field(default=None, description="Mail account ID (defaults to primary account)")


class MessagePath(BaseModel):
    message_id: int = Field(..., description="Message ID")


class ThreadPath(BaseModel):
    thread_id: str = Field(..., description="Thread ID")


class UpdateFlagsBody(BaseModel):
    flags: dict[str, bool] = Field(..., description="Flags to update, e.g. {\"read\": true, \"flagged\": false, \"locked\": true}")


class MoveMessageBody(BaseModel):
    folder_id: str = Field(..., description="Destination folder name")
    destination: str | None = Field(default=None, description="Alias for folder_id")


class SendMessageBody(BaseModel):
    to: _StringOrList = Field(default_factory=list, description="Recipient address(es)")
    cc: _StringOrList = Field(default_factory=list, description="CC address(es)")
    bcc: _StringOrList = Field(default_factory=list, description="BCC address(es)")
    subject: str = Field("", description="Email subject line")
    body_plain: str = Field("", description="Plain-text body")
    body_html: str = Field("", description="HTML body")
    draft_id: str | None = Field(default=None, description="Draft UID to delete after send")
    draft_uid: str | None = Field(default=None, description="Alias for draft_id")


class CreateDraftBody(BaseModel):
    to: _StringOrList = Field(default_factory=list, description="Recipient address(es)")
    cc: _StringOrList = Field(default_factory=list, description="CC address(es)")
    bcc: _StringOrList = Field(default_factory=list, description="BCC address(es)")
    subject: str = Field("", description="Email subject line")
    body_plain: str = Field("", description="Plain-text body")
    body_html: str = Field("", description="HTML body")
    replace_uid: str | None = Field(default=None, description="Draft UID to replace")


class DraftPath(BaseModel):
    uid: str = Field(..., description="Draft UID")


class AttachmentPath(BaseModel):
    message_id: int = Field(..., description="Message ID")
    attachment_index: int = Field(..., description="Zero-based attachment index")


class SearchQuery(BaseModel):
    q: str = Field(..., description="Search query string")
    account_id: int | None = Field(default=None, description="Mail account ID (defaults to primary account)")
    max_results: int = Field(default=50, ge=1, le=200, description="Maximum results (1-200)")


class ListMessagesQuery(BaseModel):
    account_id: int | None = Field(default=None, description="Mail account ID (defaults to primary account)")
    max_results: int = Field(default=50, ge=1, le=200, description="Maximum results (1-200)")
    cursor: str | None = Field(default=None, description="Pagination cursor (message ID)")
    unread: str | None = Field(default=None, description="Filter: 'true' for unread only")
    flagged: str | None = Field(default=None, description="Filter: 'true' for flagged only")


class GetMessageQuery(BaseModel):
    account_id: int | None = Field(default=None, description="Mail account ID (defaults to primary account)")
    mark_read: str | None = Field(default=None, description="Set to 'true' to mark as read")


class BulkFlagBody(BaseModel):
    items: list[dict] = Field(..., description="Array of {message_id, flags{read?, flagged?}}")


class BulkMoveBody(BaseModel):
    items: list[dict] = Field(..., description="Array of {message_id}")
    folder_id: str | None = Field(default=None, description="Destination folder")
    destination: str | None = Field(default=None, description="Alias for folder_id")


class BulkDeleteBody(BaseModel):
    items: list[dict] = Field(..., description="Array of {message_id}")
