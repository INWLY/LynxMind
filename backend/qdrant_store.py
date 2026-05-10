"""Qdrant client - supports dense + sparse hybrid retrieval with RRF fusion."""
import os
import uuid
from typing import Any

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client import models

load_dotenv()


class QdrantManager:
    def __init__(self):
        self._client: QdrantClient | None = None

    def _get_client(self) -> QdrantClient:
        if self._client is None:
            host = os.getenv("QDRANT_HOST", "localhost")
            port = int(os.getenv("QDRANT_PORT", "6333"))
            self._client = QdrantClient(host=host, port=port, check_compatibility=False)
        return self._client

    def init_collection(self, collection_name: str | None = None) -> None:
        client = self._get_client()
        name = collection_name or os.getenv("QDRANT_COLLECTION", "documents")
        vectors_config = models.VectorParams(size=1024, distance=models.Distance.COSINE)
        sparse_config = models.SparseVectorParams(
            index=models.SparseIndexParams(on_disk=False, full_scan_threshold=10000)
        )
        try:
            client.create_collection(
                collection_name=name,
                vectors_config=vectors_config,
                sparse_vectors_config={"bm25": sparse_config},
            )
        except Exception:
            pass  # collection already exists

    @staticmethod
    def _to_sparse_vector(sparse_dict: dict[str, float]) -> models.SparseVector:
        indices = []
        values = []
        for token, score in sparse_dict.items():
            indices.append(hash(token) % (2**31 - 1))
            values.append(score)
        return models.SparseVector(indices=indices, values=values)

    def insert(
        self,
        point_id: str,
        vector: list[float],
        sparse_vector: dict[str, float],
        payload: dict,
        collection_name: str | None = None,
    ) -> None:
        client = self._get_client()
        name = collection_name or os.getenv("QDRANT_COLLECTION", "documents")
        client.upsert(
            collection_name=name,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector={"": vector, "bm25": self._to_sparse_vector(sparse_vector)},
                    payload=payload,
                )
            ],
        )

    def query(
        self,
        output_fields: list[str] | None = None,
        limit: int = 10,
        collection_name: str | None = None,
    ) -> list[dict]:
        client = self._get_client()
        name = collection_name or os.getenv("QDRANT_COLLECTION", "documents")
        scroll_result = client.scroll(
            collection_name=name,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        points = scroll_result[0]
        result = []
        fields = set(output_fields or [])
        for point in points:
            item = {"id": point.id}
            if point.payload:
                if fields:
                    item.update({k: v for k, v in point.payload.items() if k in fields})
                else:
                    item.update(point.payload)
            result.append(item)
        return result

    def query_all(
        self,
        filter_expr: str | None = None,
        output_fields: list[str] | None = None,
        limit: int = 10000,
        collection_name: str | None = None,
    ) -> list[dict]:
        client = self._get_client()
        name = collection_name or os.getenv("QDRANT_COLLECTION", "documents")
        scroll_filter = None
        if filter_expr:
            scroll_filter = self._parse_filter_expr(filter_expr)

        result = []
        next_offset = None
        while True:
            scroll_result = client.scroll(
                collection_name=name,
                limit=min(limit - len(result), 1000),
                offset=next_offset,
                with_payload=True,
                with_vectors=False,
                filter=scroll_filter,
            )
            points = scroll_result[0]
            next_offset = scroll_result[1]
            fields = set(output_fields or [])
            for point in points:
                item = {"id": point.id}
                if point.payload:
                    if fields:
                        item.update({k: v for k, v in point.payload.items() if k in fields})
                    else:
                        item.update(point.payload)
                result.append(item)
            if next_offset is None or len(result) >= limit:
                break
        return result

    def get_chunks_by_ids(
        self, chunk_ids: list[str], collection_name: str | None = None
    ) -> list[dict]:
        if not chunk_ids:
            return []
        client = self._get_client()
        name = collection_name or os.getenv("QDRANT_COLLECTION", "documents")
        results = client.retrieve(
            collection_name=name,
            ids=chunk_ids,
            with_payload=True,
            with_vectors=False,
        )
        return [
            {"id": r.id, **(r.payload or {})}
            for r in results
        ]

    @staticmethod
    def _parse_filter_expr(expr: str) -> models.Filter:
        """Parse simple filter expressions like `filename == \"test.pdf\"`"""
        import re
        match = re.match(r'(\w+)\s*==\s*"([^"]*)"', expr)
        if match:
            key, value = match.group(1), match.group(2)
            return models.Filter(
                must=[
                    models.FieldCondition(
                        key=key,
                        match=models.MatchValue(value=value),
                    )
                ]
            )
        return models.Filter(must=[])

    @staticmethod
    def _rrf_fuse(
        results_list: list[list[tuple[str, float]]], k: int = 60
    ) -> list[tuple[str, float]]:
        scores: dict[str, float] = {}
        for results in results_list:
            for rank, (doc_id, score) in enumerate(results):
                scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
        sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_items

    def hybrid_retrieve(
        self,
        dense_vector: list[float],
        sparse_vector: dict[str, float],
        top_k: int = 10,
        collection_name: str | None = None,
    ) -> list[dict]:
        client = self._get_client()
        name = collection_name or os.getenv("QDRANT_COLLECTION", "documents")

        dense_results = client.search(
            collection_name=name,
            query_vector=("", dense_vector),
            limit=top_k,
            with_payload=True,
        )
        sparse_results = client.search(
            collection_name=name,
            query_vector=("bm25", self._to_sparse_vector(sparse_vector)),
            limit=top_k,
            with_payload=True,
        )

        dense_items = [(str(r.id), r.score) for r in dense_results]
        sparse_items = [(str(r.id), r.score) for r in sparse_results]
        fused = self._rrf_fuse([dense_items, sparse_items], k=60)

        id_to_payload = {}
        for r in dense_results:
            id_to_payload[str(r.id)] = r.payload or {}
        for r in sparse_results:
            if str(r.id) not in id_to_payload:
                id_to_payload[str(r.id)] = r.payload or {}

        result = []
        for doc_id, score in fused:
            item = {"id": doc_id, "score": score, **(id_to_payload.get(doc_id, {}))}
            result.append(item)
        return result

    def dense_retrieve(
        self,
        dense_vector: list[float],
        top_k: int = 10,
        collection_name: str | None = None,
    ) -> list[dict]:
        client = self._get_client()
        name = collection_name or os.getenv("QDRANT_COLLECTION", "documents")
        results = client.search(
            collection_name=name,
            query_vector=("", dense_vector),
            limit=top_k,
            with_payload=True,
        )
        return [{"id": str(r.id), "score": r.score, **(r.payload or {})} for r in results]

    def delete(
        self,
        filter_expr: str,
        collection_name: str | None = None,
    ) -> dict:
        client = self._get_client()
        name = collection_name or os.getenv("QDRANT_COLLECTION", "documents")
        qfilter = self._parse_filter_expr(filter_expr)
        result = client.delete(
            collection_name=name,
            points_selector=models.FilterSelector(filter=qfilter),
        )
        return {"delete_count": 0 if result is None else 1}

    def has_collection(self, collection_name: str | None = None) -> bool:
        client = self._get_client()
        name = collection_name or os.getenv("QDRANT_COLLECTION", "documents")
        collections = client.get_collections()
        return any(c.name == name for c in collections.collections)
