from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRoomItem(BaseModel):
    room_id: str = Field(..., description="Matrix room ID")
    name: str | None = Field(None, description="Explicit room name (null for DMs)")
    display_name: str = Field("", description="Computed display name (DM peer or room name)")
    topic: str | None = Field(None, description="Room topic")
    is_direct: bool = Field(False, description="Whether this is a direct message room")
    is_public: bool = Field(False, description="Whether the room is public")
    membership: str = Field("join", description="Membership state (join/invite/leave)")
    notification_count: int = Field(0, description="Unread notification count")
    highlight_count: int = Field(0, description="Unread highlight (mention) count")
    member_count: int = Field(0, description="Joined member count")
    last_event_ts: int | None = Field(None, description="Timestamp (ms) of the newest message")
    last_event_preview: str | None = Field(None, description="Snippet of the newest message")


class ChatRoomListResponse(BaseModel):
    data: list[ChatRoomItem] = Field(..., description="List of rooms")


class ChatRoomResponse(ChatRoomItem):
    pass


class ChatMessageItem(BaseModel):
    event_id: str = Field(..., description="Matrix event ID")
    room_id: str = Field(..., description="Room the message belongs to")
    sender: str = Field(..., description="Sender Matrix user ID")
    type: str = Field(..., description="Event type (m.room.message)")
    body: str | None = Field(None, description="Plain text body")
    content: dict = Field(..., description="Full Matrix event content")
    origin_server_ts: int = Field(..., description="Origin server timestamp (ms)")
    redacted: bool = Field(False, description="Whether the message was deleted")
    edited: bool = Field(False, description="Whether the message was edited")
    reactions: list[dict] = Field(..., description="Aggregated reactions [{key, count, mine}]")


class ChatMessageListResponse(BaseModel):
    data: list[ChatMessageItem] = Field(..., description="List of messages (chronological)")
    pagination: dict = Field(..., description="Pagination info")


class ChatRoomIdPath(BaseModel):
    room_id: str = Field(..., description="Matrix room ID")


class ChatEventIdPath(BaseModel):
    event_id: str = Field(..., description="Matrix event ID")


class ListMessagesQuery(BaseModel):
    account_id: int | None = Field(
        None, description="Mail account ID (defaults to primary account)"
    )
    limit: int = Field(50, ge=1, le=200, description="Max messages to return")
    before_ts: int | None = Field(
        None, description="Return messages older than this timestamp (ms)"
    )


class CreateChatRoomBody(BaseModel):
    name: str | None = Field(None, description="Room name (required unless is_direct)")
    topic: str | None = Field(None, description="Room topic")
    is_public: bool = Field(False, description="Whether the room is public")
    is_direct: bool = Field(False, description="Create a direct message room")
    invite: list[str] = Field(default_factory=list, description="Matrix user IDs to invite")


class SendChatMessageBody(BaseModel):
    body: str = Field(..., min_length=1, description="Plain text message body")


class ReactChatMessageBody(BaseModel):
    key: str = Field(..., min_length=1, description="Reaction key (emoji)")


class EditChatMessageBody(BaseModel):
    body: str = Field(..., min_length=1, description="New message body")


class ReadChatRoomBody(BaseModel):
    event_id: str = Field(..., description="Event ID to mark read up to")


class InviteChatUserBody(BaseModel):
    user_id: str = Field(..., description="Matrix user ID to invite")


class ChatEventIdResponse(BaseModel):
    data: dict = Field(..., description="Result payload (e.g. event_id)")
