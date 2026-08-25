"""Policy evidence retrieval.

This layer supplies passages; it never decides anything. Transactional decisions
belong to `eligibility.py`, which reads verified order data rather than prose.
"""

from __future__ import annotations

from ..models.tool_results import PolicySearchResult
from ..retrieval.retriever import get_retriever

INSUFFICIENT_EVIDENCE = (
    "The policy document returned nothing relevant to this question. Tell the customer plainly "
    "that Trendly's policy does not cover it and call escalate_to_human — do not answer from "
    "general knowledge."
)

GROUNDING_GUIDANCE = (
    "Answer only from these passages and cite the section numbers you used. If they do not "
    "address what the customer asked, the policy is silent: say so plainly and call "
    "escalate_to_human. Never describe which topics you can look up."
)


def search_policy(query: str) -> PolicySearchResult:
    """Retrieve ranked policy passages for a natural-language question."""
    try:
        retriever = get_retriever()
        passages, mode = retriever.search(query)
        gaps = retriever.unsupported_terms(query)
    except Exception as exc:  # index missing, store corrupt, embedding failure
        return PolicySearchResult(
            ok=False,
            query=query,
            passages=[],
            retrieval="failed",
            guidance=(
                "Policy retrieval is unavailable, so you have no policy evidence. Do not answer "
                "the policy question from general knowledge. Tell the customer you cannot look "
                f"it up right now and call escalate_to_human. ({type(exc).__name__})"
            ),
        )

    if not passages:
        return PolicySearchResult(
            ok=False, query=query, passages=[], retrieval=mode, guidance=INSUFFICIENT_EVIDENCE
        )

    if gaps:
        # The passages are the nearest text, not an answer: the document never
        # uses these words at all.
        return PolicySearchResult(
            ok=False,
            query=query,
            passages=passages,
            retrieval=mode,
            unsupported_terms=gaps,
            guidance=(
                f"The policy never mentions {', '.join(gaps)}, so these passages are the closest "
                "text rather than an answer. Unless they genuinely address the question, say the "
                "policy does not cover it and call escalate_to_human. Do not stretch a nearby "
                "clause to fit."
            ),
        )

    return PolicySearchResult(
        ok=True, query=query, passages=passages, retrieval=mode, guidance=GROUNDING_GUIDANCE
    )
