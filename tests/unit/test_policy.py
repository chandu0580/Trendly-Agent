"""Policy chunking, retrieval, and grounding."""

from __future__ import annotations

import pytest

from app.retrieval.ingest import load_chunks, source_digest
from app.services.policy_service import search_policy

pytestmark = pytest.mark.unit


def test_every_policy_clause_becomes_its_own_citable_chunk():
    sections = {c.section_number for c in load_chunks()}
    # One per numbered clause in the supplied document, plus the preamble and
    # the unnumbered prohibitions section.
    for expected in ["1.1", "1.6", "2.1", "2.3", "2.4", "3.1", "3.3", "4.1", "4.4", "5.2", "6.1", "7"]:
        assert expected in sections, f"policy clause {expected} was not indexed"


def test_chunks_carry_their_section_metadata():
    chunk = next(c for c in load_chunks() if c.section_number == "2.1")
    assert "Return window" in chunk.section_title
    assert "30 calendar days" in chunk.text
    assert chunk.metadata["source"] == "trendly_policy.md"


def test_digest_is_stable_for_unchanged_input():
    assert source_digest() == source_digest()
    assert source_digest("different text") != source_digest()


@pytest.mark.parametrize(
    "query,expected_section",
    [
        ("how long do I have to return something", "2.1"),
        ("can I return jewellery", "2.3"),
        ("final sale item refund", "2.4"),
        ("how long do refunds take on my credit card", "3.1"),
        ("cash on delivery refund bank details", "3.3"),
        ("can I exchange for a different colour", "4.1"),
        ("my parcel is lost in transit", "1.6"),
        ("how much is shipping", "1.3"),
        ("my order is late, any compensation", "1.5"),
        ("change my delivery address", "1.7"),
        ("can I exchange the same item twice", "4.4"),
        ("do I need the shoe box", "2.5"),
        ("cancelled order return", "2.6"),
        ("part of my order shipped separately", "1.4"),
    ],
)
def test_retrieval_surfaces_the_governing_clause(query, expected_section):
    """Ranking quality only.

    `ok` is a separate property — whether the policy demonstrably covers the
    subject — and is asserted by the abstention tests below. A synonym-phrased
    question ("is my order late?" for "delayed") still retrieves the right clause
    while being flagged as not provably covered.
    """
    result = search_policy(query)
    assert result.passages, f"{query!r} retrieved nothing"
    top_sections = [p.section_number for p in result.passages[:3]]
    assert expected_section in top_sections, f"{query!r} -> {top_sections}"


def test_retrieval_returns_section_numbers_for_citation():
    result = search_policy("how long do refunds take")
    assert result.passages
    assert all(p.section_number for p in result.passages)
    assert all(p.source == "trendly_policy.md" for p in result.passages)


def test_results_carry_grounding_guidance_for_the_model():
    result = search_policy("what is the return window")
    assert "cite the section numbers" in result.guidance
    assert "escalate" in result.guidance


def test_retrieval_failure_tells_the_model_to_escalate_not_improvise(monkeypatch):
    def boom(_query):
        raise RuntimeError("index unavailable")

    monkeypatch.setattr("app.services.policy_service.get_retriever", lambda: type("R", (), {"search": staticmethod(boom)})())
    result = search_policy("what is the return window")
    assert not result.ok
    assert result.retrieval == "failed"
    assert "do not answer" in result.guidance.lower()
    assert "escalate_to_human" in result.guidance


def test_retrieval_degrades_to_lexical_when_the_vector_store_is_missing(monkeypatch):
    """Losing the index must not mean losing policy grounding entirely."""
    from app.retrieval import retriever as retriever_module
    from app.retrieval.vectorstore import VectorStoreUnavailable

    def unavailable():
        raise VectorStoreUnavailable("no index")

    monkeypatch.setattr(retriever_module, "get_collection", unavailable)
    passages, mode = retriever_module.PolicyRetriever().search("how long do refunds take")
    assert mode == "lexical"
    assert "3.1" in [p.section_number for p in passages[:3]]


# ------------------------------------------------------------- abstention


@pytest.mark.parametrize(
    "query,absent_term",
    [
        ("Does Trendly ship to Antarctica?", "antarctica"),
        ("Do you deliver internationally?", "internationally"),
        ("Do you provide gift wrapping?", "wrapping"),
        ("Can I add a handwritten note?", "handwritten"),
        ("Do you do alterations?", "alterations"),
        ("Is there a physical store?", "physical"),
    ],
)
def test_a_question_the_policy_never_mentions_is_not_treated_as_answered(query, absent_term):
    """Similarity finds the nearest text; it does not find an answer.

    "Does Trendly ship to Antarctica?" scores highly against the shipping
    section precisely because it is about shipping — but the document never says
    Antarctica, and no confidence score changes that.
    """
    result = search_policy(query)
    assert result.ok is False, f"{query!r} was treated as covered"
    assert absent_term in result.unsupported_terms
    assert "escalate_to_human" in result.guidance
    assert "does not cover" in result.guidance or "policy never mentions" in result.guidance


@pytest.mark.parametrize(
    "query",
    [
        "Can I return an item after one year?",
        "Can I change my address after dispatch?",
        "How long do I have to return something?",
        "Can I return jewellery?",
        "How long do refunds take on my credit card?",
        "Do I need the shoe box?",
    ],
)
def test_a_question_the_policy_does_cover_is_answerable(query):
    """The abstention signal must not swallow genuinely covered questions."""
    result = search_policy(query)
    assert result.ok is True, f"{query!r} wrongly abstained on {result.unsupported_terms}"
    assert result.passages


def test_the_abstention_signal_is_generic_not_a_question_list():
    """Nothing is hardcoded: an invented word the policy lacks is flagged too."""
    result = search_policy("do you offer monogramming on shirts?")
    assert result.ok is False
    assert "monogramming" in result.unsupported_terms


def test_abstention_does_not_fire_on_ordinary_vocabulary():
    from app.retrieval.retriever import get_retriever

    retriever = get_retriever()
    # Inflections and near-forms of policy words are not evidence of silence.
    for word in ("pincode", "shipped", "returning", "part", "refunds", "exchanges"):
        assert retriever.unsupported_terms(word) == [], word
