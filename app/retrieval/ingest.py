"""Section-aware ingestion of `trendly_policy.md` into Chroma.

Chunking follows the document's own structure rather than a fixed character
window: each numbered clause (`**2.1 Return window.** ...`) becomes one chunk
carrying its section number and title. That matters because a policy answer is
only useful if it can be cited, and a chunk that straddles "returns are allowed
within 30 days" and "non-returnable categories" cannot be cited precisely.

Ingestion is idempotent: chunk ids are stable and derived from the section
number, and a digest of the source file is stored so a re-run with unchanged
input is a no-op.

    python -m app.retrieval.ingest [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass

from ..config import POLICY_COLLECTION, POLICY_PATH, VECTORSTORE_DIR

SECTION_RE = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s*$", re.M)
CLAUSE_RE = re.compile(r"^\*\*(\d+\.\d+)\s+(.+?)\.?\*\*", re.M)
PREAMBLE_SECTION = ("0", "Scope and authority")


@dataclass(frozen=True)
class PolicyChunk:
    section_number: str
    section_title: str
    text: str

    @property
    def chunk_id(self) -> str:
        return f"policy-{self.section_number}"

    @property
    def document(self) -> str:
        # The heading is embedded with the body so a query like "return window"
        # matches the clause title, not just its prose.
        return f"Section {self.section_number} — {self.section_title}\n\n{self.text}"

    @property
    def metadata(self) -> dict:
        return {
            "section_number": self.section_number,
            "section_title": self.section_title,
            "source": POLICY_PATH.name,
        }


def _split_clauses(section_number: str, section_title: str, body: str) -> list[PolicyChunk]:
    """Split a section into numbered clauses, keeping any unnumbered lead-in."""
    marks = list(CLAUSE_RE.finditer(body))
    if not marks:
        text = body.strip()
        return [PolicyChunk(section_number, section_title, text)] if text else []

    chunks: list[PolicyChunk] = []
    lead = body[: marks[0].start()].strip()
    if lead:
        chunks.append(PolicyChunk(section_number, section_title, lead))

    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(body)
        text = body[mark.start() : end].strip()
        if text:
            chunks.append(PolicyChunk(mark.group(1), f"{section_title} — {mark.group(2)}", text))
    return chunks


def load_chunks(policy_text: str | None = None) -> list[PolicyChunk]:
    """Parse the policy markdown into citable chunks."""
    text = policy_text if policy_text is not None else POLICY_PATH.read_text(encoding="utf-8")
    sections = list(SECTION_RE.finditer(text))

    chunks: list[PolicyChunk] = []
    preamble = text[: sections[0].start()] if sections else text
    preamble = re.sub(r"^#\s+.*$", "", preamble, flags=re.M).replace("---", "").strip()
    if preamble:
        chunks.append(PolicyChunk(*PREAMBLE_SECTION, preamble))

    for index, section in enumerate(sections):
        end = sections[index + 1].start() if index + 1 < len(sections) else len(text)
        body = text[section.end() : end].replace("---", "").strip()
        chunks.extend(_split_clauses(section.group(1), section.group(2), body))
    return chunks


def source_digest(policy_text: str | None = None) -> str:
    text = policy_text if policy_text is not None else POLICY_PATH.read_text(encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def ingest(force: bool = False, verbose: bool = True) -> int:
    """Build or refresh the policy index. Returns the number of chunks indexed."""
    import chromadb

    from .embeddings import get_embedding_function

    chunks = load_chunks()
    digest = source_digest()

    VECTORSTORE_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(VECTORSTORE_DIR))
    collection = client.get_or_create_collection(
        POLICY_COLLECTION,
        embedding_function=get_embedding_function(),
        metadata={"hnsw:space": "cosine"},
    )

    existing = collection.get(ids=["__manifest__"], include=["metadatas"])
    stored = (existing["metadatas"] or [{}])[0] if existing["ids"] else {}
    if not force and stored.get("digest") == digest and collection.count() == len(chunks) + 1:
        if verbose:
            print(f"policy index already current: {len(chunks)} chunks (digest {digest})")
        return len(chunks)

    if collection.count():
        client.delete_collection(POLICY_COLLECTION)
        collection = client.get_or_create_collection(
            POLICY_COLLECTION,
            embedding_function=get_embedding_function(),
            metadata={"hnsw:space": "cosine"},
        )

    collection.upsert(
        ids=[c.chunk_id for c in chunks],
        documents=[c.document for c in chunks],
        metadatas=[c.metadata for c in chunks],
    )
    # A manifest row keeps re-ingestion idempotent without a second store.
    collection.upsert(
        ids=["__manifest__"],
        documents=["manifest"],
        metadatas=[{"digest": digest, "chunks": len(chunks), "source": POLICY_PATH.name}],
    )

    if verbose:
        print(f"indexed {len(chunks)} policy chunks from {POLICY_PATH.name} (digest {digest})")
        for chunk in chunks:
            print(f"  {chunk.section_number:>4}  {chunk.section_title[:66]}")
    return len(chunks)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest the Trendly policy into Chroma.")
    parser.add_argument("--force", action="store_true", help="re-index even if unchanged")
    args = parser.parse_args()
    ingest(force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
