import json
import os
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from agent import chat_with_agent, chat_with_agent_stream, storage
from auth import authenticate_user, create_access_token, get_current_user, get_db, get_password_hash, require_admin
from document_loader import DocumentLoader
from embedding import embedding_service
from models import IngestionJob, User
from news_service import _ingest_progress, create_manual_card, fetch_article, get_news_context, get_news_detail, ingest_news, list_jobs, list_news, now_utc, parse_datetime, serialize_admin_card, serialize_job, to_utc_iso
from parent_chunk_store import ParentChunkStore
from qdrant_store import QdrantManager
from qdrant_writer import QdrantWriter
from schemas import *

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR.parent / "data"
UPLOAD_DIR = DATA_DIR / "documents"
CHAT_UPLOAD_DIR = DATA_DIR / "chat_uploads"

CHAT_ATTACHMENT_MAX_CHARS = max(2000, int(os.getenv("CHAT_ATTACHMENT_MAX_CHARS", "120000")))
CHAT_ATTACHMENT_MAX_FILE_SIZE = max(
    1 * 1024 * 1024,
    int(os.getenv("CHAT_ATTACHMENT_MAX_FILE_SIZE", str(8 * 1024 * 1024))),
)

loader = DocumentLoader()
parent_chunk_store = ParentChunkStore()
vector_manager = QdrantManager()
vector_writer = QdrantWriter(embedding_service=embedding_service, qdrant_manager=vector_manager)
router = APIRouter()


def _remove_bm25_stats_for_filename(filename: str) -> None:
    rows = vector_manager.query_all(
        filter_expr=f'filename == "{filename}"',
        output_fields=["text"],
    )
    texts = [row.get("text") or "" for row in rows]
    embedding_service.increment_remove_documents(texts)


def _resolve_news_context(db: Session, news_id: int | None) -> dict | None:
    if not news_id:
        return None
    context = get_news_context(db, news_id)
    if not context:
        raise HTTPException(status_code=404, detail="News item not found")
    return context


@router.post("/auth/register", response_model=AuthResponse)
async def register(request: RegisterRequest, db: Session = Depends(get_db)):
    username = (request.username or "").strip()
    password = (request.password or "").strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")
    exists = db.query(User).filter(User.username == username).first()
    if exists:
        raise HTTPException(status_code=409, detail="Username already exists")
    role = request.role or "user"
    if role == "admin":
        admin_code = os.getenv("ADMIN_INVITE_CODE", "")
        if not admin_code or request.admin_code != admin_code:
            raise HTTPException(status_code=403, detail="管理员邀请码错误")
    user = User(username=username, password_hash=get_password_hash(password), role=role)
    db.add(user)
    db.commit()
    token = create_access_token(username=username, role=role)
    return AuthResponse(access_token=token, username=username, role=role)


@router.post("/auth/login", response_model=AuthResponse)
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(db, request.username, request.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_access_token(username=user.username, role=user.role)
    return AuthResponse(access_token=token, username=user.username, role=user.role)


@router.get("/auth/me", response_model=CurrentUserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return CurrentUserResponse(username=current_user.username, role=current_user.role)


@router.get("/news", response_model=NewsListResponse)
async def list_news_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=50),
    date: str | None = Query(None),
    source: str | None = Query(None),
    db: Session = Depends(get_db),
):
    try:
        payload = list_news(db, page=page, page_size=page_size, date_str=date, source_slug=source)
        return NewsListResponse(**payload)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, expected YYYY-MM-DD")


@router.get("/news/{news_id}", response_model=NewsDetailResponse)
async def news_detail_endpoint(news_id: int, db: Session = Depends(get_db)):
    payload = get_news_detail(db, news_id)
    if not payload:
        raise HTTPException(status_code=404, detail="News item not found")
    return NewsDetailResponse(**payload)


@router.post("/news/{news_id}/ask", response_model=NewsAskResponse)
async def news_ask_endpoint(
    news_id: int,
    request: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        news_context = _resolve_news_context(db, news_id)
        session_id = request.session_id or f"news_{news_id}"
        response = chat_with_agent(
            request.message, current_user.username, session_id,
            attachment_context=request.attachment_context,
            attachment_files=request.attachment_files,
            news_context=news_context,
        )
        return NewsAskResponse(news_id=news_id, **response)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/admin/news/ingest", response_model=NewsIngestResponse)
async def manual_news_ingest(request: NewsIngestRequest, _: User = Depends(require_admin)):
    try:
        from database import SessionLocal
        from models import IngestionJob

        db = SessionLocal()
        try:
            job = IngestionJob(
                trigger_mode="manual",
                status="running",
                started_at=now_utc(),
                details_json={"current": "正在启动...", "progress": 0, "total": 0},
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            job_id = job.id
        finally:
            db.close()

        import threading
        thread = threading.Thread(
            target=ingest_news,
            kwargs={"trigger_mode": "manual", "force": request.force, "job_id": job_id},
            daemon=True,
        )
        thread.start()

        return NewsIngestResponse(job=serialize_job(job))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to start ingestion: {exc}")


@router.get("/admin/news/ingest/{job_id}/stream")
async def ingest_stream(job_id: int, _: User = Depends(require_admin)):
    """SSE endpoint that pushes real-time progress for an ingestion job."""
    last = None
    idle_cycles = 0

    async def event_generator():
        nonlocal last, idle_cycles
        import asyncio
        from database import SessionLocal
        from models import IngestionJob
        while True:
            cur = _ingest_progress.get(job_id)
            if cur and cur is not last:
                last = cur
                idle_cycles = 0
                yield f"data: {json.dumps(cur, ensure_ascii=False)}\n\n"
                if cur.get("type") in ("success", "failed"):
                    break
            else:
                idle_cycles += 1
                # ~30 seconds without progress → check if job is actually dead
                if idle_cycles >= 60:
                    db = SessionLocal()
                    try:
                        job = db.query(IngestionJob).filter(IngestionJob.id == job_id).first()
                        if job and job.status != "running":
                            yield f"data: {json.dumps({'type': 'failed', 'status': job.status, 'error_message': job.error_message or '任务已中断'})}\n\n"
                            break
                    finally:
                        db.close()
                    idle_cycles = 0  # reset and keep waiting if still running
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/admin/news/jobs", response_model=IngestionJobListResponse)
async def news_jobs_endpoint(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return IngestionJobListResponse(jobs=list_jobs(db))


@router.post("/admin/news/jobs/{job_id}/cancel")
async def cancel_job(job_id: int, _: User = Depends(require_admin)):
    from database import SessionLocal
    from models import IngestionJob
    from news_service import now_utc

    db = SessionLocal()
    try:
        job = db.query(IngestionJob).filter(IngestionJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status != "running":
            raise HTTPException(status_code=400, detail="Job is not running")

        job.status = "failed"
        job.error_message = "用户手动取消"
        job.finished_at = now_utc()
        db.commit()
        return {"message": f"Job #{job_id} cancelled"}
    finally:
        db.close()


@router.delete("/admin/news/items/{news_id}")
async def delete_news_item(news_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    from models import NewsItem
    item = db.query(NewsItem).filter(NewsItem.id == news_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="News item not found")
    db.delete(item)
    db.commit()
    return {"message": f"News item #{news_id} deleted"}


@router.delete("/admin/news/items")
async def delete_all_news(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    from models import NewsItem, NewsCard
    count = db.query(NewsItem).count()
    db.query(NewsCard).delete()
    db.query(NewsItem).delete()
    db.commit()
    return {"message": f"All {count} news items deleted"}


# === Admin: Manual Card CRUD ===


@router.get("/admin/items", response_model=AdminCardListResponse)
async def admin_list_items(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from models import NewsItem, NewsCard
    from sqlalchemy.orm import joinedload

    query = db.query(NewsItem).options(joinedload(NewsItem.source), joinedload(NewsItem.card)).order_by(
        NewsItem.created_at.desc(),
    )
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return AdminCardListResponse(items=[serialize_admin_card(item) for item in items], total=total)


@router.post("/admin/items", response_model=AdminCardResponse)
async def admin_create_card(
    request: AdminCreateCardRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    title = (request.title or "").strip()
    text = (request.text or "").strip()
    source_slug = (request.source_slug or "").strip()
    if not title or not text or not source_slug:
        raise HTTPException(status_code=400, detail="title, text, and source_slug are required")

    published_at = None
    if request.published_at:
        published_at = parse_datetime(request.published_at)
    if published_at is None:
        published_at = now_utc()

    try:
        item = create_manual_card(
            db, source_slug, title, text,
            url=request.url or "",
            published_at=published_at,
            tags=request.tags or [],
            importance=request.importance or "normal",
        )
        db.commit()
        db.refresh(item)
        return serialize_admin_card(item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/items/parse-url")
async def admin_parse_url(request: dict, _: User = Depends(require_admin)):
    """Fetch and extract article metadata from a URL without persisting."""
    url = (request.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    try:
        article = fetch_article(url)
        return {
            "title": article.get("title", ""),
            "text": article.get("body", ""),
            "published_at": to_utc_iso(article.get("published_at")) if article.get("published_at") else None,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"解析失败: {exc}")


@router.put("/admin/items/{item_id}", response_model=AdminCardResponse)
async def admin_update_card(
    item_id: int,
    request: AdminUpdateCardRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from sqlalchemy.orm import joinedload

    item = db.query(NewsItem).options(joinedload(NewsItem.source), joinedload(NewsItem.card)).filter(
        NewsItem.id == item_id
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    if request.source_slug is not None:
        source = db.query(Source).filter(Source.slug == request.source_slug).first()
        if not source:
            raise HTTPException(status_code=400, detail=f"Source '{request.source_slug}' not found")
        item.source_id = source.id
    if request.title is not None:
        item.title = request.title.strip()
    if request.text is not None:
        item.body = request.text.strip()
        item.summary = request.text[:500]
    if request.url is not None:
        item.url = request.url.strip()
    if request.published_at is not None:
        parsed = parse_datetime(request.published_at)
        if parsed:
            item.published_at = parsed
    if request.tags is not None and item.card:
        item.card.tags = request.tags
    if request.importance is not None and item.card:
        item.card.importance = request.importance if request.importance in ("high", "normal", "low") else "normal"

    item.updated_at = now_utc()
    if item.card:
        item.card.updated_at = now_utc()
    db.commit()
    db.refresh(item)
    return serialize_admin_card(item)


@router.delete("/admin/items/{item_id}")
async def admin_delete_item(item_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    from models import NewsItem
    item = db.query(NewsItem).filter(NewsItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(item)
    db.commit()
    return {"message": f"Item #{item_id} deleted"}


# === Sessions ===

@router.get("/sessions/{session_id}", response_model=SessionMessagesResponse)
async def get_session_messages(session_id: str, current_user: User = Depends(get_current_user)):
    try:
        result = storage.get_session_messages(current_user.username, session_id)
        messages = [
            MessageInfo(type=msg["type"], content=msg["content"], timestamp=msg["timestamp"], rag_trace=msg.get("rag_trace"))
            for msg in result["messages"]
        ]
        return SessionMessagesResponse(messages=messages, news_id=result.get("news_id"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(current_user: User = Depends(get_current_user)):
    try:
        sessions = [SessionInfo(**item) for item in storage.list_session_infos(current_user.username)]
        sessions.sort(key=lambda x: x.updated_at, reverse=True)
        return SessionListResponse(sessions=sessions)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/sessions/{session_id}", response_model=SessionDeleteResponse)
async def delete_session(session_id: str, current_user: User = Depends(get_current_user)):
    try:
        deleted = storage.delete_session(current_user.username, session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Session not found")
        return SessionDeleteResponse(session_id=session_id, message="Session deleted")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.patch("/sessions/{session_id}/title", response_model=SessionTitleUpdateResponse)
async def update_session_title(
    session_id: str, request: SessionTitleUpdateRequest, current_user: User = Depends(get_current_user),
):
    title = (request.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")
    updated_title = storage.update_session_title(current_user.username, session_id, title)
    if not updated_title:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionTitleUpdateResponse(session_id=session_id, title=updated_title, message="Session title updated")


# === Chat ===

@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    request: ChatRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    try:
        session_id = request.session_id or "default_session"
        news_context = _resolve_news_context(db, request.news_id)
        response = chat_with_agent(
            request.message, current_user.username, session_id,
            attachment_context=request.attachment_context,
            attachment_files=request.attachment_files,
            news_context=news_context,
        )
        return ChatResponse(**response)
    except HTTPException:
        raise
    except Exception as exc:
        msg = str(exc)
        match = re.search(r"Error code:\s*(\d{3})", msg)
        if match:
            raise HTTPException(status_code=int(match.group(1)), detail=msg)
        raise HTTPException(status_code=500, detail=msg)


@router.post("/chat/stream")
async def chat_stream_endpoint(
    request: ChatRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    news_context = _resolve_news_context(db, request.news_id)

    async def event_generator():
        try:
            session_id = request.session_id or "default_session"
            async for chunk in chat_with_agent_stream(
                request.message, current_user.username, session_id,
                attachment_context=request.attachment_context,
                attachment_files=request.attachment_files,
                news_context=news_context,
            ):
                yield chunk
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'content': str(exc)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/attachments/parse", response_model=ChatAttachmentParseResponse)
async def parse_chat_attachment(file: UploadFile = File(...), _: User = Depends(get_current_user)):
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required")
    suffix = Path(filename).suffix.lower()
    allowed_office = {".pdf", ".doc", ".docx", ".xls", ".xlsx"}
    allowed_text = {".txt", ".md", ".csv", ".json"}
    if suffix not in allowed_office and suffix not in allowed_text:
        raise HTTPException(status_code=400, detail="Unsupported attachment format")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Attachment is empty")
    if len(raw) > CHAT_ATTACHMENT_MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"Attachment exceeds {CHAT_ATTACHMENT_MAX_FILE_SIZE // (1024 * 1024)}MB")
    extracted = ""
    if suffix in allowed_text:
        try:
            extracted = raw.decode("utf-8")
        except UnicodeDecodeError:
            extracted = raw.decode("utf-8", errors="ignore")
    else:
        os.makedirs(CHAT_UPLOAD_DIR, exist_ok=True)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=CHAT_UPLOAD_DIR, suffix=suffix, delete=False) as tmp:
                tmp.write(raw)
                tmp_path = tmp.name
            chunks = loader.load_document(tmp_path, filename)
            leaf_texts = [
                (item.get("text") or "").strip()
                for item in chunks if int(item.get("chunk_level", 0) or 0) == 3
            ]
            texts = leaf_texts or [(item.get("text") or "").strip() for item in chunks]
            deduped = []
            seen = set()
            for t in texts:
                if not t or t in seen:
                    continue
                seen.add(t)
                deduped.append(t)
            extracted = "\n\n".join(deduped)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Attachment parse failed: {exc}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
    extracted = (extracted or "").strip()
    if not extracted:
        raise HTTPException(status_code=400, detail="No readable text found in attachment")
    truncated = len(extracted) > CHAT_ATTACHMENT_MAX_CHARS
    text_out = extracted[:CHAT_ATTACHMENT_MAX_CHARS]
    return ChatAttachmentParseResponse(filename=filename, extracted_text=text_out, chars=len(text_out), truncated=truncated)


# === Documents ===

@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(_: User = Depends(require_admin)):
    try:
        vector_manager.init_collection()
        results = vector_manager.query(output_fields=["filename", "file_type"], limit=10000)
        file_stats: dict[str, dict] = {}
        for item in results:
            fname = item.get("filename", "")
            ftype = item.get("file_type", "")
            if fname not in file_stats:
                file_stats[fname] = {"filename": fname, "file_type": ftype, "chunk_count": 0}
            file_stats[fname]["chunk_count"] += 1
        documents = [DocumentInfo(**stats) for stats in file_stats.values()]
        return DocumentListResponse(documents=documents)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to list documents: {exc}")


@router.post("/documents/upload", response_model=DocumentUploadResponse)
async def upload_document(file: UploadFile = File(...), _: User = Depends(require_admin)):
    try:
        filename = file.filename or ""
        file_lower = filename.lower()
        if not filename:
            raise HTTPException(status_code=400, detail="Filename is required")
        if not (file_lower.endswith(".pdf") or file_lower.endswith((".docx", ".doc")) or file_lower.endswith((".xlsx", ".xls"))):
            raise HTTPException(status_code=400, detail="Only PDF, Word, and Excel files are supported")
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        vector_manager.init_collection()
        delete_expr = f'filename == "{filename}"'
        try:
            _remove_bm25_stats_for_filename(filename)
        except Exception:
            pass
        try:
            vector_manager.delete(delete_expr)
        except Exception:
            pass
        try:
            parent_chunk_store.delete_by_filename(filename)
        except Exception:
            pass
        file_path = UPLOAD_DIR / filename
        with open(file_path, "wb") as handle:
            handle.write(await file.read())
        try:
            new_docs = loader.load_document(str(file_path), filename)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Document parsing failed: {exc}")
        if not new_docs:
            raise HTTPException(status_code=500, detail="No document chunks were produced")
        parent_docs = [doc for doc in new_docs if int(doc.get("chunk_level", 0) or 0) in (1, 2)]
        leaf_docs = [doc for doc in new_docs if int(doc.get("chunk_level", 0) or 0) == 3]
        if not leaf_docs:
            raise HTTPException(status_code=500, detail="No leaf chunks were produced")
        parent_chunk_store.upsert_documents(parent_docs)
        vector_writer.write_documents(leaf_docs)
        return DocumentUploadResponse(filename=filename, chunks_processed=len(leaf_docs),
                                      message=f"Uploaded {filename}. Stored {len(leaf_docs)} leaf chunks and {len(parent_docs)} parent chunks.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}")


@router.delete("/documents/{filename}", response_model=DocumentDeleteResponse)
async def delete_document(filename: str, _: User = Depends(require_admin)):
    try:
        vector_manager.init_collection()
        delete_expr = f'filename == "{filename}"'
        _remove_bm25_stats_for_filename(filename)
        result = vector_manager.delete(delete_expr)
        parent_chunk_store.delete_by_filename(filename)
        return DocumentDeleteResponse(
            filename=filename,
            chunks_deleted=result.get("delete_count", 0) if isinstance(result, dict) else 0,
            message=f"Deleted document {filename}",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Delete failed: {exc}")
