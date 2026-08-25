"""Thin wrapper over the persistent Chroma collection."""

from __future__ import annotations

from functools import lru_cache

from ..config import POLICY_COLLECTION, VECTORSTORE_DIR


class VectorStoreUnavailable(RuntimeError):
    """Raised when the policy index cannot be opened or is empty."""


@lru_cache(maxsize=1)
def get_collection():
    """Open the persisted policy collection.

    Raises VectorStoreUnavailable rather than returning an empty collection, so
    callers degrade deliberately instead of silently retrieving nothing.
    """
    import chromadb

    from .embeddings import get_embedding_function

    if not VECTORSTORE_DIR.exists():
        raise VectorStoreUnavailable(
            f"No policy index at {VECTORSTORE_DIR}. Run: python -m app.retrieval.ingest"
        )
    try:
        client = chromadb.PersistentClient(path=str(VECTORSTORE_DIR))
        collection = client.get_collection(
            POLICY_COLLECTION, embedding_function=get_embedding_function()
        )
    except Exception as exc:  # collection missing, corrupt store, load failure
        raise VectorStoreUnavailable(str(exc)) from exc

    if collection.count() <= 1:  # only the manifest row
        raise VectorStoreUnavailable("Policy index is empty. Run: python -m app.retrieval.ingest")
    return collection


def indexed_section_count() -> int:
    """Chunks currently indexed, excluding the manifest row. 0 when unavailable."""
    try:
        return max(0, get_collection().count() - 1)
    except VectorStoreUnavailable:
        return 0


def reset_cache() -> None:
    get_collection.cache_clear()
