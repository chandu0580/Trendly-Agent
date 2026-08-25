"""Policy retrieval: section-aware ingestion, Chroma vector store, hybrid search.

Submodules are imported lazily so `python -m app.retrieval.ingest` does not
re-enter this package while the module is still executing.
"""

__all__ = ["PolicyRetriever", "get_retriever", "ingest", "load_chunks"]


def __getattr__(name: str):
    if name in {"PolicyRetriever", "get_retriever"}:
        from . import retriever

        return getattr(retriever, name)
    if name in {"ingest", "load_chunks"}:
        from . import ingest as ingest_module

        return getattr(ingest_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
