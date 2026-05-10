"""多模态嵌入服务 - 支持密集向量 + 稀疏向量 (BM25)，带实时 DF 持久化 + 协程安全"""
import json
import math
import os
import re
import threading
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from langchain_huggingface import HuggingFaceEmbeddings

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_STATE_PATH = BASE_DIR / "data" / "bm25_state.json"


def _create_dense_embedder():
    model_name = (os.getenv("EMBEDDING_MODEL") or "BAAI/bge-m3").strip()
    return HuggingFaceEmbeddings(model_name=model_name)


class EmbeddingService:
    """Embedding service supporting dense vectors + sparse BM25 with real-time DF persistence."""

    def __init__(self):
        self.dense_embedder = _create_dense_embedder()
        self._lock = threading.Lock()
        self._df: Counter = Counter()
        self._avg_len: float = 100.0
        self._doc_count: int = 0
        self._loaded = False

    def _recompute_avg_len(self):
        total = sum(v for k, v in self._df.items())
        total_docs = self._doc_count or 1
        self._avg_len = total / total_docs if total > 0 else 100.0

    def _load_state(self):
        path = Path(os.getenv("BM25_STATE_PATH", str(_DEFAULT_STATE_PATH)))
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._df = Counter(data.get("df", {}))
                self._doc_count = data.get("doc_count", 0)
                self._avg_len = data.get("avg_len", 100.0)
            except Exception:
                pass
        self._loaded = True

    def _persist_unlocked(self):
        path = Path(os.getenv("BM25_STATE_PATH", str(_DEFAULT_STATE_PATH)))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {"df": dict(self._df), "doc_count": self._doc_count, "avg_len": self._avg_len},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _persist(self):
        with self._lock:
            self._persist_unlocked()

    def increment_add_documents(self, texts: list[str]):
        with self._lock:
            if not self._loaded:
                self._load_state()
            for text in texts:
                tokens = self.tokenize(text)
                for token in set(tokens):
                    self._df[token] += 1
                self._doc_count += 1
            self._recompute_avg_len()
            self._persist_unlocked()

    def increment_remove_documents(self, texts: list[str]):
        with self._lock:
            if not self._loaded:
                self._load_state()
            for text in texts:
                tokens = self.tokenize(text)
                for token in set(tokens):
                    if self._df[token] > 1:
                        self._df[token] -= 1
                    else:
                        del self._df[token]
                self._doc_count = max(0, self._doc_count - 1)
            self._recompute_avg_len()
            self._persist_unlocked()

    @staticmethod
    def tokenize(text: str) -> list[str]:
        text = text.lower()
        tokens = re.findall(r"\w+", text)
        return [t for t in tokens if len(t) >= 2]

    def get_embeddings(self, texts: list[str]) -> list[list[float]]:
        return self.dense_embedder.embed_documents(texts)

    def get_sparse_embedding(self, text: str) -> tuple[dict[str, float], dict[str, float]]:
        with self._lock:
            if not self._loaded:
                self._load_state()
        tokens = self.tokenize(text)
        token_counts = Counter(tokens)
        n = len(tokens) or 1
        k1 = 1.5
        b = 0.75

        query_vector = {}
        for token, count in token_counts.items():
            tf = count / n
            qtf = 1.0 + math.log(count) if count > 0 else 0
            query_vector[token] = qtf

        doc_vector = {}
        for token in set(tokens):
            df = self._df.get(token, 1)
            idf = math.log((self._doc_count - df + 0.5) / (df + 0.5) + 1.0)
            tf = token_counts[token] / n
            doc_vector[token] = idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * n / self._avg_len))

        return query_vector, doc_vector

    def get_sparse_embeddings(self, texts: list[str]) -> list[dict[str, float]]:
        results = []
        for text in texts:
            _, doc_vec = self.get_sparse_embedding(text)
            results.append(doc_vec)
        return results

    def get_all_embeddings(self, texts: list[str]):
        dense = self.get_embeddings(texts)
        sparse = self.get_sparse_embeddings(texts)
        return list(zip(dense, sparse))


embedding_service = EmbeddingService()
