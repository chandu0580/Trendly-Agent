"""Local, free embeddings.

Chroma's bundled ONNX MiniLM (all-MiniLM-L6-v2, 384-dim) runs entirely on CPU
with no API key and no torch dependency. It is cached under the user's Chroma
cache directory on first use. `sentence-transformers` would pull torch for the
same model, which is a much heavier image for no accuracy gain at this scale.
"""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def get_embedding_function():
    """Return the shared embedding function (loaded once per process)."""
    from chromadb.utils import embedding_functions

    return embedding_functions.ONNXMiniLM_L6_V2()


EMBEDDING_MODEL = "onnx-all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
