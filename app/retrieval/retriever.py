"""Hybrid policy retrieval: semantic candidates + lexical candidates, merged.

Why hybrid rather than pure vector search: policy questions turn on exact terms
("final sale", "cash on delivery", "48 hours") and on clause numbers, and a
nearest-neighbour miss on those returns a fluent answer grounded in the wrong
rule. Lexical matching anchors those; semantic matching covers the paraphrases
lexical cannot ("how long till my money comes back").

Ranking is a weighted blend, not a learned reranker. The goal is reliable
evidence, not a research project.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from ..config import settings
from ..models.tool_results import PolicyPassage
from .ingest import load_chunks
from .vectorstore import VectorStoreUnavailable, get_collection

SEMANTIC_WEIGHT = 0.55
LEXICAL_WEIGHT = 0.45
SECTION_REF_RE = re.compile(r"\b(\d+\.\d+)\b")
STOPWORDS = frozenset(
    """a an and are as at be by can do does for from how i if in is it me my of on or our
    that the their there they this to was what when where which who will with you your""".split()
)


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS and len(t) > 2}


# Words that carry no topic on their own, so their absence from the policy says
# nothing about whether the question is covered.
GENERIC_TERMS = frozenset(
    """able about after again against also always any anyone anything ask back because before
    both check come could customer customers day days did does doing done even ever every
    first from get gets getting give given going got had has have help here hold instead
    just keep know like long look made make many may might much must need needs never new
    now off once one only order orders other out over own person please possible put really
    right said same say see seem send sent should show side since some someone something
    still such support sure take taken tell than thank thanks then there thing things think
    those three through time told too took trendly try trying two under until upon use used
    using very want wanted was way well went were what when where whether which while who
    why will with within without work would year years yet you your""".split()
)


def _policy_vocabulary(chunks) -> frozenset[str]:
    words: set[str] = set()
    for chunk in chunks:
        words |= set(re.findall(r"[a-z0-9]+", chunk.document.lower()))
    return frozenset(words)


def _stem(word: str) -> str:
    """Crude prefix stem, enough to match "pincode"/"pincodes" and "ship"/"shipping".

    A real stemmer would be a dependency for no gain here: the vocabulary is one
    short document, and the only failure this needs to avoid is treating an
    inflected form of a covered word as evidence the policy is silent.
    """
    return word[:5]


@dataclass
class Candidate:
    section_number: str
    section_title: str
    text: str
    semantic: float = 0.0
    lexical: float = 0.0

    @property
    def score(self) -> float:
        return SEMANTIC_WEIGHT * self.semantic + LEXICAL_WEIGHT * self.lexical


class PolicyRetriever:
    """Retrieves policy evidence. Never answers; only supplies passages."""

    def __init__(self, top_k: int | None = None, candidates: int | None = None) -> None:
        self.top_k = top_k or settings.retrieval_top_k
        self.candidates = candidates or settings.retrieval_candidates
        self._chunks = load_chunks()
        self._chunk_tokens = {c.section_number: _tokens(c.document) for c in self._chunks}
        self._vocabulary = _policy_vocabulary(self._chunks)
        self._stems = frozenset(_stem(w) for w in self._vocabulary)
        self._long_vocabulary = frozenset(w for w in self._vocabulary if len(w) >= 4)

    # ------------------------------------------------------------ components

    def _semantic(self, query: str) -> dict[str, Candidate]:
        collection = get_collection()
        result = collection.query(
            query_texts=[query],
            n_results=min(self.candidates, max(1, collection.count() - 1)),
            include=["documents", "metadatas", "distances"],
        )
        found: dict[str, Candidate] = {}
        for doc, meta, distance in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0]
        ):
            section = str(meta.get("section_number", ""))
            if section in {"", "__manifest__"} or meta.get("digest"):
                continue
            # Cosine distance in [0, 2] -> similarity in [0, 1].
            found[section] = Candidate(
                section_number=section,
                section_title=str(meta.get("section_title", "")),
                text=doc,
                semantic=max(0.0, 1.0 - float(distance) / 2.0),
            )
        return found

    def _lexical(self, query: str) -> dict[str, Candidate]:
        query_tokens = _tokens(query)
        wanted_sections = set(SECTION_REF_RE.findall(query))
        lowered = query.lower()

        found: dict[str, Candidate] = {}
        for chunk in self._chunks:
            tokens = self._chunk_tokens[chunk.section_number]
            overlap = len(query_tokens & tokens) / len(query_tokens) if query_tokens else 0.0

            # Multi-word policy terms appearing verbatim are strong evidence.
            phrase = 0.0
            body = chunk.document.lower()
            for size in (3, 2):
                words = [w for w in re.findall(r"[a-z0-9]+", lowered)]
                for start in range(max(0, len(words) - size + 1)):
                    gram = " ".join(words[start : start + size])
                    if len(gram) > 6 and gram in body:
                        phrase = max(phrase, 0.35 if size == 3 else 0.2)

            direct = 1.0 if chunk.section_number in wanted_sections else 0.0
            score = min(1.0, overlap + phrase + direct)
            if score > 0:
                found[chunk.section_number] = Candidate(
                    section_number=chunk.section_number,
                    section_title=chunk.section_title,
                    text=chunk.document,
                    lexical=score,
                )
        return found

    # ---------------------------------------------------------------- public

    def unsupported_terms(self, query: str) -> list[str]:
        """Topic words in the question that appear nowhere in the policy.

        A high similarity score only says the retrieved clause is the *closest*
        text, not that it answers anything. "Does Trendly ship to Antarctica?"
        scores well against the shipping section because it is about shipping —
        but the document never mentions Antarctica, and no amount of nearest-
        neighbour confidence changes that.

        Generic words are excluded, so this fires on the subject of the question
        rather than on its phrasing. It is a signal, not a verdict: the caller
        decides whether to abstain.
        """
        candidates = {
            word
            for word in re.findall(r"[a-z]{4,}", query.lower())
            if word not in GENERIC_TERMS and word not in STOPWORDS
        }
        return sorted(
            word
            for word in candidates
            if word not in self._vocabulary
            and _stem(word) not in self._stems
            # "part" should not read as absent when the policy says "partial".
            and not any(v.startswith(word) or word.startswith(v) for v in self._long_vocabulary)
        )

    def search(self, query: str) -> tuple[list[PolicyPassage], str]:
        """Return ranked passages and the retrieval mode actually used."""
        lexical = self._lexical(query)
        mode = "hybrid"
        try:
            semantic = self._semantic(query)
        except VectorStoreUnavailable:
            # Degrade to lexical rather than answering from model knowledge.
            semantic, mode = {}, "lexical"
        except Exception:
            semantic, mode = {}, "lexical"

        merged: dict[str, Candidate] = {}
        for section, candidate in semantic.items():
            merged[section] = candidate
        for section, candidate in lexical.items():
            if section in merged:
                merged[section].lexical = candidate.lexical
            else:
                merged[section] = candidate

        ranked = sorted(merged.values(), key=lambda c: c.score, reverse=True)[: self.top_k]
        passages = [
            PolicyPassage(
                section_number=c.section_number,
                section_title=c.section_title,
                text=c.text,
                score=round(c.score, 4),
            )
            for c in ranked
            if c.score > 0
        ]
        return passages, mode


@lru_cache(maxsize=1)
def get_retriever() -> PolicyRetriever:
    return PolicyRetriever()
