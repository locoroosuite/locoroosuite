from __future__ import annotations

from pydantic import BaseModel, Field


class DocumentItem(BaseModel):
    id: str = Field(..., description="Document UUID")
    name: str = Field("", description="Document name")
    type: str = Field("", description="Document type: odt, ods, or odp")
    size: int = Field(0, description="File size in bytes")
    created_at: str = Field("", description="Creation timestamp (ISO 8601)")
    updated_at: str = Field("", description="Last update timestamp (ISO 8601)")
    folder_path: str = Field("", description="Slash-separated folder path relative to account root (empty string = root)")
    tags: list[str] = Field(default_factory=list, description="Document tags")


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
    folder: str | None = Field(default=None, description="Filter to documents directly in this folder path (exact match)")
    tag: str | None = Field(default=None, description="Filter to documents carrying this tag")


class CreateDocumentBody(BaseModel):
    name: str = Field(default="Untitled Document", description="Document name")
    type: str = Field(default="odt", description="Document type: odt, ods, or odp")
    folder: str | None = Field(default=None, description="Folder path to create the document in (empty/omitted = root)")


class RenameDocumentBody(BaseModel):
    name: str = Field(..., description="New document name")


class MoveDocumentBody(BaseModel):
    folder: str = Field(default="", description="Target folder path (empty string = root)")


class UpdateTagsBody(BaseModel):
    add: list[str] | None = Field(default=None, description="Tags to add")
    remove: list[str] | None = Field(default=None, description="Tags to remove")


class SetTagsBody(BaseModel):
    set: list[str] | None = Field(default=None, description="Replace all tags with this list")


class FolderItem(BaseModel):
    path: str = Field(..., description="Full slash-separated folder path")
    name: str = Field(..., description="Leaf folder name")
    parent: str = Field("", description="Parent folder path (empty for top-level)")
    count: int = Field(0, description="Number of documents directly in this folder")


class FolderListResponse(BaseModel):
    data: list[FolderItem] = Field(..., description="Folders")


class CreateFolderBody(BaseModel):
    name: str = Field(..., description="Folder name (leaf segment)")
    parent: str | None = Field(default=None, description="Parent folder path (empty/omitted = top-level)")


class RenameFolderBody(BaseModel):
    path: str = Field(..., description="Existing folder path to rename")
    name: str = Field(..., description="New leaf folder name")


class DeleteFolderBody(BaseModel):
    path: str = Field(..., description="Folder path to delete (contents move to parent)")


class TagsResponse(BaseModel):
    data: dict = Field(..., description="Document tags")


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
