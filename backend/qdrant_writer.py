"""Document chunk writer to Qdrant - supports dense + sparse vectors."""
from embedding import EmbeddingService, embedding_service as _default_embedding_service
from qdrant_store import QdrantManager


class QdrantWriter:
    def __init__(
        self,
        embedding_service: EmbeddingService = _default_embedding_service,
        qdrant_manager: QdrantManager | None = None,
    ):
        self.embedding_service = embedding_service
        self.qdrant_manager = qdrant_manager or QdrantManager()

    def write_documents(self, documents: list[dict]) -> int:
        if not documents:
            return 0

        texts = [doc.get("text", "") for doc in documents]
        embeddings = self.embedding_service.get_all_embeddings(texts)

        self.qdrant_manager.init_collection()
        count = 0
        for doc, (dense_vec, sparse_vec) in zip(documents, embeddings):
            chunk_id = doc.get("chunk_id", "")
            if not chunk_id:
                import uuid
                chunk_id = str(uuid.uuid4())

            payload = {
                "chunk_id": chunk_id,
                "text": doc.get("text", ""),
                "filename": doc.get("filename", ""),
                "file_type": doc.get("file_type", ""),
                "file_path": doc.get("file_path", ""),
                "page_number": doc.get("page_number", 0),
                "chunk_level": doc.get("chunk_level", 0),
                "chunk_idx": doc.get("chunk_idx", 0),
                "parent_chunk_id": doc.get("parent_chunk_id", ""),
                "root_chunk_id": doc.get("root_chunk_id", ""),
            }

            self.qdrant_manager.insert(
                point_id=chunk_id,
                vector=dense_vec,
                sparse_vector=sparse_vec,
                payload=payload,
            )
            count += 1

        self.embedding_service.increment_add_documents(texts)
        return count
