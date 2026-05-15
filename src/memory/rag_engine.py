"""
RAG Engine — Retrieval-Augmented Generation using Qdrant.

Indexes documents, retrieves relevant chunks, and provides context
for knowledge-seeking queries.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from src.infra.logging import get_logger
from src.infra.vector_store import get_vector_store

logger = get_logger("memory.rag")

COLLECTION_NAME = "rag_documents"


class RAGEngine:
    """
    RAG engine backed by Qdrant vector store.

    Handles document chunking, indexing, retrieval, and context formatting.
    """

    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 50) -> None:
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def _chunk_text(self, text: str) -> list[str]:
        """Split text into overlapping chunks by approximate token count."""
        words = text.split()
        chunks = []
        # Approximate: 1 token ≈ 0.75 words
        words_per_chunk = int(self._chunk_size * 0.75)
        overlap_words = int(self._chunk_overlap * 0.75)

        i = 0
        while i < len(words):
            end = min(i + words_per_chunk, len(words))
            chunk = " ".join(words[i:end])
            if chunk.strip():
                chunks.append(chunk)
            i += words_per_chunk - overlap_words
        return chunks or [text]  # Return full text if too short to chunk

    async def index_document(self, text: str, metadata: dict[str, Any] | None = None,
                              doc_id: str | None = None) -> list[str]:
        """Index a document by chunking and storing embeddings."""
        store = await get_vector_store()
        chunks = self._chunk_text(text)
        doc_id = doc_id or str(uuid.uuid4())

        chunk_ids = []
        chunk_texts = []
        chunk_metas = []

        for i, chunk in enumerate(chunks):
            chunk_id = hashlib.md5(f"{doc_id}:{i}".encode()).hexdigest()
            chunk_ids.append(chunk_id)
            chunk_texts.append(chunk)
            chunk_metas.append({**(metadata or {}), "doc_id": doc_id, "chunk_index": i, "total_chunks": len(chunks)})

        await store.upsert(COLLECTION_NAME, chunk_texts, chunk_metas, chunk_ids)
        logger.info("document_indexed", doc_id=doc_id, chunks=len(chunks))
        return chunk_ids

    async def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Search for relevant document chunks."""
        store = await get_vector_store()
        results = await store.search(COLLECTION_NAME, query, top_k=top_k)
        logger.debug("rag_search", query=query[:50], results=len(results))
        return results

    async def get_context(self, query: str, top_k: int = 3) -> str:
        """Get formatted RAG context for an LLM prompt."""
        results = await self.search(query, top_k)
        if not results:
            return "No relevant documents found."
        parts = []
        for r in results:
            score = f"(relevance: {r['score']:.2f})"
            parts.append(f"{score}\n{r['text']}")
        return "Relevant documents:\n\n" + "\n\n---\n\n".join(parts)

    async def delete_document(self, doc_id: str) -> None:
        """Delete all chunks of a document."""
        store = await get_vector_store()
        results = await store.search(COLLECTION_NAME, "", top_k=100,
                                      filter_conditions={"doc_id": doc_id})
        if results:
            await store.delete(COLLECTION_NAME, [r["id"] for r in results])
            logger.info("document_deleted", doc_id=doc_id, chunks_removed=len(results))
