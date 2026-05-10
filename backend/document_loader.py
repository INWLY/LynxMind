"""Document loading and chunking service."""
import os
from typing import Dict, List

from langchain_community.document_loaders import Docx2txtLoader, PyPDFLoader, UnstructuredExcelLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter


class DocumentLoader:
    def __init__(self):
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=800,
            chunk_overlap=160,
            separators=["\n\n", "\n", "。", ".", " ", ""],
        )
        self._page_splitter = RecursiveCharacterTextSplitter(
            chunk_size=2000,
            chunk_overlap=200,
            separators=["\n\n", "\n", "。", ".", " ", ""],
        )

    @staticmethod
    def _build_chunk_id(filename: str, level: int, page: int, idx: int) -> str:
        return f"{filename}__L{level}__P{page:04d}__I{idx:06d}"

    def _split_page_to_three_levels(
        self, text: str, filename: str, page_number: int, file_type: str, file_path: str
    ) -> List[Dict]:
        chunks: List[Dict] = []
        if not text.strip():
            return chunks

        # Level 1: large chunks (parent)
        l1_chunks = self._page_splitter.split_text(text)
        for l1_idx, l1_text in enumerate(l1_chunks):
            l1_id = self._build_chunk_id(filename, 1, page_number, l1_idx)
            chunks.append({
                "chunk_id": l1_id,
                "text": l1_text,
                "filename": filename,
                "file_type": file_type,
                "file_path": file_path,
                "page_number": page_number,
                "chunk_level": 1,
                "chunk_idx": l1_idx,
                "parent_chunk_id": "",
                "root_chunk_id": l1_id,
            })

            # Level 2: medium chunks
            l2_chunks = self.text_splitter.split_text(l1_text)
            for l2_idx, l2_text in enumerate(l2_chunks):
                l2_id = self._build_chunk_id(filename, 2, page_number, l2_idx)
                chunks.append({
                    "chunk_id": l2_id,
                    "text": l2_text,
                    "filename": filename,
                    "file_type": file_type,
                    "file_path": file_path,
                    "page_number": page_number,
                    "chunk_level": 2,
                    "chunk_idx": l2_idx,
                    "parent_chunk_id": l1_id,
                    "root_chunk_id": l1_id,
                })

                # Level 3: leaf chunks
                l3_chunks = self.text_splitter.split_text(l2_text)
                for l3_idx, l3_text in enumerate(l3_chunks):
                    l3_id = self._build_chunk_id(filename, 3, page_number, l3_idx)
                    chunks.append({
                        "chunk_id": l3_id,
                        "text": l3_text,
                        "filename": filename,
                        "file_type": file_type,
                        "file_path": file_path,
                        "page_number": page_number,
                        "chunk_level": 3,
                        "chunk_idx": l3_idx,
                        "parent_chunk_id": l2_id,
                        "root_chunk_id": l1_id,
                    })

        return chunks

    def load_document(self, file_path: str, filename: str) -> List[Dict]:
        suffix = os.path.splitext(filename)[1].lower()
        file_type = suffix.lstrip(".")

        if suffix == ".pdf":
            loader = PyPDFLoader(file_path)
        elif suffix in (".doc", ".docx"):
            loader = Docx2txtLoader(file_path)
        elif suffix in (".xls", ".xlsx"):
            loader = UnstructuredExcelLoader(file_path)
        else:
            # For text-based files, read directly
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            return self._split_page_to_three_levels(text, filename, 0, file_type, file_path)

        langchain_docs = loader.load()
        all_chunks: List[Dict] = []
        for i, doc in enumerate(langchain_docs):
            page_number = doc.metadata.get("page", i) if hasattr(doc, "metadata") else i
            text = doc.page_content if hasattr(doc, "page_content") else str(doc)
            chunks = self._split_page_to_three_levels(text, filename, page_number, file_type, file_path)
            all_chunks.extend(chunks)

        return all_chunks

    def load_documents_from_folder(self, folder_path: str) -> List[Dict]:
        all_chunks = []
        for root, _, files in os.walk(folder_path):
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt", ".md", ".csv"):
                    fpath = os.path.join(root, fname)
                    try:
                        chunks = self.load_document(fpath, fname)
                        all_chunks.extend(chunks)
                    except Exception as e:
                        print(f"Error loading {fname}: {e}")
        return all_chunks
