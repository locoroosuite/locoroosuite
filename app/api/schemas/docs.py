from __future__ import annotations

from pydantic import BaseModel, Field


class DocumentItem(BaseModel):
    id: str = Field(..., description="Document UUID")
    name: str = Field("", description="Document name")
    type: str = Field("", description="Document type: odt, ods, or odp")
    size: int = Field(0, description="File size in bytes")
    created_at: str = Field("", description="Creation timestamp (ISO 8601)")
    updated_at: str = Field("", description="Last update timestamp (ISO 8601)")


class DocumentListResponse(BaseModel):
    data: list[DocumentItem] = Field(..., description="List of documents")
    pagination: dict | None = Field(None, description="Pagination metadata (next_cursor, has_more)")


class DocumentDetailResponse(BaseModel):
    data: DocumentItem = Field(..., description="Document details")


class DocPath(BaseModel):
    doc_id: str = Field(..., description="Document UUID")


class DraftPath(BaseModel):
    doc_id: str = Field(..., description="Source document UUID")
    draft_id: str = Field(..., description="Draft document UUID")


class ListDocumentsQuery(BaseModel):
    account_id: int | None = Field(default=None, description="Mail account ID (defaults to primary account)")
    max_results: int = Field(default=50, ge=1, le=200, description="Maximum results (1-200)")


class CreateDocumentBody(BaseModel):
    name: str = Field(default="Untitled Document", description="Document name")
    type: str = Field(default="odt", description="Document type: odt, ods, or odp")


class RenameDocumentBody(BaseModel):
    name: str = Field(..., description="New document name")


class ReadContentQuery(BaseModel):
    account_id: int | None = Field(default=None, description="Mail account ID (defaults to primary account)")
    format: str = Field(default="text", description="Output format: text or markdown")


class ContentResponse(BaseModel):
    data: dict = Field(..., description="Document content and format")


class UpdateContentJsonBody(BaseModel):
    content: str = Field(default="", description="Document content")
    format: str = Field(default="markdown", description="Input format: markdown or text")
    account_id: int | None = Field(default=None, description="Mail account ID (defaults to primary account)")


class CreateDraftBody(BaseModel):
    content: str = Field(default="", description="Draft content")
    format: str = Field(default="markdown", description="Content format")
    summary: str = Field(default="AI modification", description="Change description")


class DraftItem(DocumentItem):
    source_document_id: str | None = Field(None, description="UUID of the source document this draft was created from")
    summary: str = Field("", description="Human-readable description of draft changes")


class DraftListResponse(BaseModel):
    data: list[DraftItem] = Field(..., description="List of drafts")


class ConvertResponse(BaseModel):
    data: DocumentItem = Field(..., description="Converted document")
