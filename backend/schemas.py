from pydantic import BaseModel
from typing import Optional, List


class RegisterRequest(BaseModel):
    username: str
    password: str
    role: Optional[str] = "user"
    admin_code: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str


class CurrentUserResponse(BaseModel):
    username: str
    role: str


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = "default_session"
    attachment_context: Optional[str] = None
    attachment_files: Optional[List[str]] = None
    news_id: Optional[int] = None


class RetrievedChunk(BaseModel):
    filename: str
    page_number: Optional[str | int] = None
    text: Optional[str] = None
    score: Optional[float] = None
    rrf_rank: Optional[int] = None
    rerank_score: Optional[float] = None


class RagTrace(BaseModel):
    query: Optional[str] = None
    docs: Optional[List[dict]] = None
    context: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    rag_trace: Optional[RagTrace] = None


class ChatAttachmentParseResponse(BaseModel):
    filename: str
    extracted_text: str
    chars: int
    truncated: bool


class MessageInfo(BaseModel):
    type: str
    content: str
    timestamp: str
    rag_trace: Optional[dict] = None


class SessionMessagesResponse(BaseModel):
    messages: List[MessageInfo]
    news_id: Optional[int] = None


class SessionInfo(BaseModel):
    session_id: str
    title: str
    updated_at: str
    message_count: int
    news_id: Optional[int] = None


class SessionListResponse(BaseModel):
    sessions: List[SessionInfo]


class SessionDeleteResponse(BaseModel):
    session_id: str
    message: str


class SessionTitleUpdateRequest(BaseModel):
    title: str


class SessionTitleUpdateResponse(BaseModel):
    session_id: str
    title: str
    message: str


class DocumentInfo(BaseModel):
    filename: str
    file_type: str
    chunk_count: int
    uploaded_at: Optional[str] = None


class DocumentListResponse(BaseModel):
    documents: List[DocumentInfo]


class DocumentUploadResponse(BaseModel):
    filename: str
    chunks_processed: int
    message: str


class DocumentDeleteResponse(BaseModel):
    filename: str
    chunks_deleted: int
    message: str


class SourceInfo(BaseModel):
    id: int
    slug: str
    name: str
    base_url: str
    enabled: bool


class NewsCardSummary(BaseModel):
    id: int
    title: str
    source_slug: str
    source_name: str
    summary: str
    one_line_summary: str
    thought_prompt: str
    tags: List[str]
    importance: str
    url: str
    cover_image_url: Optional[str] = None
    published_at: Optional[str] = None
    ingested_at: str


class NewsListResponse(BaseModel):
    items: List[NewsCardSummary]
    total: int
    page: int
    page_size: int
    available_dates: List[str]
    sources: List[SourceInfo]


class NewsDetailResponse(BaseModel):
    item: NewsCardSummary
    body: str
    related_items: List[NewsCardSummary]


class NewsAskResponse(BaseModel):
    response: str
    news_id: int
    rag_trace: Optional[RagTrace] = None


class IngestionJobInfo(BaseModel):
    id: int
    trigger_mode: str
    status: str
    started_at: str
    finished_at: Optional[str] = None
    fetched_count: int
    imported_count: int
    skipped_count: int
    error_count: int
    error_message: str
    details_json: Optional[dict] = None


class IngestionJobListResponse(BaseModel):
    jobs: List[IngestionJobInfo]


class NewsIngestRequest(BaseModel):
    force: bool = False


class NewsIngestResponse(BaseModel):
    job: IngestionJobInfo


class AdminCreateCardRequest(BaseModel):
    source_slug: str
    title: str
    text: str
    url: Optional[str] = ""
    published_at: Optional[str] = None
    tags: Optional[List[str]] = None
    importance: Optional[str] = "normal"


class AdminUpdateCardRequest(BaseModel):
    source_slug: Optional[str] = None
    title: Optional[str] = None
    text: Optional[str] = None
    url: Optional[str] = None
    published_at: Optional[str] = None
    tags: Optional[List[str]] = None
    importance: Optional[str] = None


class AdminCardResponse(BaseModel):
    id: int
    title: str
    source_slug: str
    source_name: str
    summary: str
    body: str
    url: Optional[str] = ""
    published_at: Optional[str] = None
    tags: List[str] = []
    importance: str = "normal"
    created_at: str | None = None


class AdminCardListResponse(BaseModel):
    items: List[AdminCardResponse]
    total: int
