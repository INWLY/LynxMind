"""RAG utilities: retrieval, reranking, auto-merge, step-back expansion, HyDE."""
import json
import os
from collections import defaultdict
from typing import Any, Dict, List, Tuple

import requests
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

from embedding import embedding_service as _embedding_service
from parent_chunk_store import ParentChunkStore
from qdrant_store import QdrantManager

load_dotenv()

MODEL = os.getenv("MODEL", "ep-20250227110822-5lvjg")
BASE_URL = os.getenv("BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "openai")
RERANK_MODEL = os.getenv("RERANK_MODEL", "")
RERANK_BINDING_HOST = os.getenv("RERANK_BINDING_HOST", "")
RERANK_API_KEY = os.getenv("RERANK_API_KEY", "")
AUTO_MERGE_ENABLED = os.getenv("AUTO_MERGE_ENABLED", "true").lower() == "true"
AUTO_MERGE_THRESHOLD = int(os.getenv("AUTO_MERGE_THRESHOLD", "2"))
LEAF_RETRIEVE_LEVEL = int(os.getenv("LEAF_RETRIEVE_LEVEL", "3"))

_vector_manager = QdrantManager()
_parent_chunk_store = ParentChunkStore()
_stepback_model = None


def _get_rerank_endpoint() -> str:
    host = RERANK_BINDING_HOST.strip().rstrip("/")
    if not host.endswith("/v1"):
        host = host + "/v1"
    return f"{host}/rerank"


def _merge_to_parent_level(docs: List[Dict], threshold: int) -> List[Dict]:
    if not docs or threshold <= 0:
        return docs

    source_groups = defaultdict(list)
    for doc in docs:
        filename = (doc.get("filename") or "").strip()
        source_groups[filename].append(doc)

    merged: List[Dict] = []
    for filename, group in source_groups.items():
        sibling_offsets = defaultdict(list)
        for doc in group:
            root_chunk_id = doc.get("root_chunk_id") or ""
            sibling_offsets[root_chunk_id].append(doc)

        for root_id, siblings in sibling_offsets.items():
            if len(siblings) >= threshold:
                parent_ids = sorted(set(d.get("parent_chunk_id") or "" for d in siblings))
                parent_docs = _parent_chunk_store.get_documents_by_ids(parent_ids)
                seen_texts = set()
                for pd in parent_docs:
                    text = (pd.get("text") or "").strip()
                    if text and text not in seen_texts:
                        seen_texts.add(text)
                        score = max((d.get("score") or 0) for d in siblings)
                        pd["score"] = score
                        merged.append(pd)
            else:
                merged.extend(siblings)

    return merged


def _auto_merge_documents(docs: List[Dict], top_k: int) -> List[Dict]:
    if not AUTO_MERGE_ENABLED:
        return docs
    merged = _merge_to_parent_level(docs, AUTO_MERGE_THRESHOLD)
    merged.sort(key=lambda x: x.get("score", 0), reverse=True)
    return merged[:top_k]


def _rerank_documents(query: str, docs: List[Dict], top_k: int) -> List[Dict]:
    if not docs or not RERANK_MODEL or not RERANK_API_KEY:
        return docs

    try:
        endpoint = _get_rerank_endpoint()
        payload = {
            "model": RERANK_MODEL,
            "query": query,
            "documents": [d.get("text", "") for d in docs],
            "top_n": min(top_k, len(docs)),
        }
        headers = {
            "Authorization": f"Bearer {RERANK_API_KEY}",
            "Content-Type": "application/json",
        }
        resp = requests.post(endpoint, json=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            return docs

        data = resp.json()
        reranked = []
        for item in data.get("results", []):
            idx = item.get("index")
            if idx is not None and idx < len(docs):
                doc = dict(docs[idx])
                doc["rerank_score"] = item.get("relevance_score", 0)
                reranked.append(doc)

        reranked.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
        return reranked[:top_k]
    except Exception:
        return docs


def _get_stepback_model():
    global _stepback_model
    if _stepback_model is None:
        _stepback_model = init_chat_model(
            model=MODEL,
            model_provider=MODEL_PROVIDER,
            api_key=os.getenv("ARK_API_KEY"),
            base_url=BASE_URL,
        )
    return _stepback_model


def _generate_step_back_question(query: str) -> str | None:
    try:
        model = _get_stepback_model()
        prompt = (
            f"You are an AI assistant tasked with generating a broader, more fundamental question "
            f"that helps reason about the original question.\n\n"
            f"Original question: {query}\n\n"
            f"Generate a step-back question that captures the broader context:"
        )
        response = model.invoke(prompt)
        return response.content.strip() if hasattr(response, "content") else str(response).strip()
    except Exception:
        return None


def _answer_step_back_question(step_back_question: str) -> str | None:
    try:
        model = _get_stepback_model()
        response = model.invoke(step_back_question)
        return response.content.strip() if hasattr(response, "content") else str(response).strip()
    except Exception:
        return None


def generate_hypothetical_document(query: str) -> str | None:
    try:
        model = _get_stepback_model()
        prompt = (
            f"Given the question: {query}\n\n"
            f"Generate a hypothetical document that would answer this question. "
            f"Write it as a short factual passage that contains the answer."
        )
        response = model.invoke(prompt)
        return response.content.strip() if hasattr(response, "content") else str(response).strip()
    except Exception:
        return None


def step_back_expand(query: str) -> Dict[str, Any]:
    step_back_q = _generate_step_back_question(query)
    step_back_a = _answer_step_back_question(step_back_q) if step_back_q else None
    return {
        "step_back_question": step_back_q,
        "step_back_answer": step_back_a,
    }


def retrieve_documents(query: str, top_k: int = 10) -> Dict[str, Any]:
    top_k = min(top_k, 30)

    # Get embeddings
    dense_vecs = _embedding_service.get_embeddings([query])
    dense_vector = dense_vecs[0] if dense_vecs else []
    _, sparse_vector = _embedding_service.get_sparse_embedding(query)

    # Hybrid retrieve from Qdrant
    _vector_manager.init_collection()
    initial_docs = _vector_manager.hybrid_retrieve(
        dense_vector=dense_vector,
        sparse_vector=sparse_vector,
        top_k=top_k,
    )

    # Rerank if available
    reranked = _rerank_documents(query, initial_docs, top_k)
    rerank_applied = reranked != initial_docs

    # Auto-merge
    merged = _auto_merge_documents(reranked, top_k) if AUTO_MERGE_ENABLED else reranked

    # Format context
    context_parts = []
    for doc in merged:
        text = doc.get("text", "")
        source = doc.get("filename", "")
        page = doc.get("page_number", "")
        if text:
            parts = [text]
            if source:
                parts.append(f"\n--- 来源: {source}")
            if page:
                parts.append(f" 第{page}页")
            context_parts.append("".join(parts))

    return {
        "docs": merged,
        "context": "\n\n".join(context_parts),
        "rerank_applied": rerank_applied,
        "initial_count": len(initial_docs),
        "final_count": len(merged),
    }
