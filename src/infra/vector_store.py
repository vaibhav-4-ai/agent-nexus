"""
Async vector store using Qdrant Cloud.

Handles embedding generation via sentence-transformers and provides
a clean interface for storing/searching vectors used by RAG and Episodic Memory.
"""

from __future__ import annotations

import hashlib
from typing import Any

from src.config import get_settings
from src.infra.logging import get_logger

logger = get_logger("infra.vector_store")


class VectorStore:
    """
    Async vector store backed by Qdrant Cloud.

    Handles embedding generation and provides high-level methods
    for the RAG engine and Episodic Memory system.
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._embedding_model: Any = None
        self._dimension: int = 384

    async def initialize(self) -> None:
        """Initialize the Qdrant client and embedding model."""
        settings = get_settings()
        self._dimension = settings.vector_db.embedding_dimension

        # Initialize Qdrant client
        try:
            from qdrant_client import AsyncQdrantClient
            from qdrant_client.http import models as qmodels

            api_key = settings.vector_db.api_key.get_secret_value()
            if api_key:
                self._client = AsyncQdrantClient(
                    url=settings.vector_db.url,
                    api_key=api_key,
                )
            else:
                self._client = AsyncQdrantClient(url=settings.vector_db.url)

            logger.info("qdrant_connected", url=settings.vector_db.url)
        except Exception as e:
            logger.error("qdrant_connection_failed", error=str(e))
            raise

        # Initialize embedding model
        try:
            from sentence_transformers import SentenceTransformer
            self._embedding_model = SentenceTransformer(settings.vector_db.embedding_model)
            logger.info("embedding_model_loaded", model=settings.vector_db.embedding_model)
        except Exception as e:
            logger.error("embedding_model_load_failed", error=str(e))
            raise

        # Ensure collections exist
        await self._ensure_collections()

    async def _ensure_collections(self) -> None:
        """Create required collections if they don't exist."""
        from qdrant_client.http import models as qmodels

        collections = ["rag_documents", "episodic_memory"]
        existing = await self._client.get_collections()
        existing_names = {c.name for c in existing.collections}

        for name in collections:
            if name not in existing_names:
                await self._client.create_collection(
                    collection_name=name,
                    vectors_config=qmodels.VectorParams(
                        size=self._dimension,
                        distance=qmodels.Distance.COSINE,
                    ),
                )
                logger.info("collection_created", name=name, dimension=self._dimension)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a list of texts."""
        embeddings = self._embedding_model.encode(texts, show_progress_bar=False)
        return embeddings.tolist()

    def _text_to_id(self, text: str) -> str:
        """Generate a deterministic ID from text content."""
        return hashlib.md5(text.encode()).hexdigest()

    async def upsert(
        self,
        collection: str,
        texts: list[str],
        metadatas: list[dict[str, Any]] | None = None,
        ids: list[str] | None = None,
    ) -> list[str]:
        """
        Embed texts and upsert into a collection.

        Returns the list of point IDs.
        """
        from qdrant_client.http import models as qmodels

        embeddings = self._embed(texts)
        if ids is None:
            ids = [self._text_to_id(t) for t in texts]
        if metadatas is None:
            metadatas = [{} for _ in texts]

        points = []
        for i, (text, emb, meta, point_id) in enumerate(zip(texts, embeddings, metadatas, ids)):
            payload = {**meta, "text": text}
            points.append(qmodels.PointStruct(
                id=point_id,
                vector=emb,
                payload=payload,
            ))

        await self._client.upsert(collection_name=collection, points=points)
        logger.info("vectors_upserted", collection=collection, count=len(points))
        return ids

    async def search(
        self,
        collection: str,
        query: str,
        top_k: int = 5,
        score_threshold: float = 0.3,
        filter_conditions: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Search for similar vectors by text query.

        Returns list of {id, score, text, metadata} dicts.
        """
        from qdrant_client.http import models as qmodels

        query_embedding = self._embed([query])[0]

        search_filter = None
        if filter_conditions:
            must_conditions = []
            for key, value in filter_conditions.items():
                must_conditions.append(
                    qmodels.FieldCondition(key=key, match=qmodels.MatchValue(value=value))
                )
            search_filter = qmodels.Filter(must=must_conditions)

        results = await self._client.search(
            collection_name=collection,
            query_vector=query_embedding,
            limit=top_k,
            score_threshold=score_threshold,
            query_filter=search_filter,
        )

        return [
            {
                "id": str(r.id),
                "score": r.score,
                "text": r.payload.get("text", "") if r.payload else "",
                "metadata": {k: v for k, v in (r.payload or {}).items() if k != "text"},
            }
            for r in results
        ]

    async def delete(self, collection: str, ids: list[str]) -> None:
        """Delete points by ID."""
        from qdrant_client.http import models as qmodels
        await self._client.delete(
            collection_name=collection,
            points_selector=qmodels.PointIdsList(points=ids),
        )
        logger.info("vectors_deleted", collection=collection, count=len(ids))

    async def count(self, collection: str) -> int:
        """Get the number of points in a collection."""
        info = await self._client.get_collection(collection)
        return info.points_count or 0

    async def estimate_size_bytes(self, collection: str) -> int:
        """Estimate the storage bytes used by a collection.

        Qdrant doesn't expose raw bytes directly via the client; we approximate as
        count * (vector_bytes + average_payload_bytes). For dim=384 + ~500B payload
        that's ~2KB/vector, which is conservative for episodic memory entries.
        """
        n = await self.count(collection)
        per_vector_bytes = self._dimension * 4 + 500
        return n * per_vector_bytes

    async def delete_oldest(self, collection: str, count_to_delete: int) -> int:
        """Delete the oldest `count_to_delete` points in a collection, ordered by `created_at` payload.

        Returns the number actually deleted. Points without a `created_at` payload
        are treated as oldest (deleted first).
        """
        if count_to_delete <= 0:
            return 0
        from qdrant_client.http import models as qmodels

        # Scroll through the entire collection collecting (id, created_at) tuples.
        collected: list[tuple[Any, float]] = []
        offset = None
        batch_size = 1000
        while True:
            result = await self._client.scroll(
                collection_name=collection,
                limit=batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            points, offset = result
            for p in points:
                ts = 0.0
                if p.payload and "created_at" in p.payload:
                    try:
                        ts = float(p.payload["created_at"])
                    except (TypeError, ValueError):
                        ts = 0.0
                collected.append((p.id, ts))
            if offset is None or not points:
                break

        collected.sort(key=lambda t: t[1])
        ids_to_delete = [t[0] for t in collected[:count_to_delete]]
        if not ids_to_delete:
            return 0
        await self._client.delete(
            collection_name=collection,
            points_selector=qmodels.PointIdsList(points=ids_to_delete),
        )
        logger.info("vectors_lru_evicted", collection=collection, deleted=len(ids_to_delete))
        return len(ids_to_delete)

    async def close(self) -> None:
        """Close the Qdrant client."""
        if self._client:
            await self._client.close()
        logger.info("vector_store_closed")


# Module-level singleton
_vector_store: VectorStore | None = None


async def get_vector_store() -> VectorStore:
    """Get the singleton VectorStore, initializing if needed."""
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStore()
        await _vector_store.initialize()
    return _vector_store


async def close_vector_store() -> None:
    """Close the vector store."""
    global _vector_store
    if _vector_store is not None:
        await _vector_store.close()
        _vector_store = None
