"""Parent chunk document storage for Auto-merging Retriever."""
from datetime import datetime
from typing import List

from cache import cache
from database import SessionLocal
from models import ParentChunk


class ParentChunkStore:
    @staticmethod
    def _to_dict(chunk: ParentChunk) -> dict:
        return {
            "chunk_id": chunk.chunk_id,
            "text": chunk.text,
            "filename": chunk.filename,
            "file_type": chunk.file_type,
            "file_path": chunk.file_path,
            "page_number": chunk.page_number,
            "parent_chunk_id": chunk.parent_chunk_id,
            "root_chunk_id": chunk.root_chunk_id,
            "chunk_level": chunk.chunk_level,
            "chunk_idx": chunk.chunk_idx,
        }

    @staticmethod
    def _cache_key(filename: str) -> str:
        return f"parent_chunks:{filename}"

    def upsert_documents(self, documents: List[dict]) -> None:
        if not documents:
            return
        db = SessionLocal()
        try:
            for doc in documents:
                chunk_id = doc.get("chunk_id", "")
                existing = db.query(ParentChunk).filter(ParentChunk.chunk_id == chunk_id).first()
                if existing:
                    existing.text = doc.get("text", existing.text)
                    existing.filename = doc.get("filename", existing.filename)
                    existing.file_type = doc.get("file_type", existing.file_type)
                    existing.file_path = doc.get("file_path", existing.file_path)
                    existing.page_number = doc.get("page_number", existing.page_number)
                    existing.parent_chunk_id = doc.get("parent_chunk_id", existing.parent_chunk_id)
                    existing.root_chunk_id = doc.get("root_chunk_id", existing.root_chunk_id)
                    existing.chunk_level = doc.get("chunk_level", existing.chunk_level)
                    existing.chunk_idx = doc.get("chunk_idx", existing.chunk_idx)
                    existing.updated_at = datetime.utcnow()
                else:
                    new_chunk = ParentChunk(
                        chunk_id=chunk_id,
                        text=doc.get("text", ""),
                        filename=doc.get("filename", ""),
                        file_type=doc.get("file_type", ""),
                        file_path=doc.get("file_path", ""),
                        page_number=doc.get("page_number", 0),
                        parent_chunk_id=doc.get("parent_chunk_id", ""),
                        root_chunk_id=doc.get("root_chunk_id", ""),
                        chunk_level=doc.get("chunk_level", 0),
                        chunk_idx=doc.get("chunk_idx", 0),
                    )
                    db.add(new_chunk)
            db.commit()
        finally:
            db.close()

    def get_documents_by_ids(self, chunk_ids: List[str]) -> List[dict]:
        if not chunk_ids:
            return []
        db = SessionLocal()
        try:
            chunks = db.query(ParentChunk).filter(ParentChunk.chunk_id.in_(chunk_ids)).all()
            return [self._to_dict(c) for c in chunks]
        finally:
            db.close()

    def delete_by_filename(self, filename: str) -> None:
        db = SessionLocal()
        try:
            db.query(ParentChunk).filter(ParentChunk.filename == filename).delete()
            db.commit()
        finally:
            db.close()
            cache.delete(self._cache_key(filename))
