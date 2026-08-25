"""Policy retrieval tool — the only permitted source for policy claims."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..services.policy_service import search_policy as _search_policy
from .context import ToolContext


class SearchPolicyArgs(BaseModel):
    query: str = Field(
        description=(
            "The customer's policy question in natural language, for example "
            "'how long do I have to return a dress' or 'refund timing for a UPI order'."
        )
    )


def build_search_policy(ctx: ToolContext) -> StructuredTool:
    def search_policy(query: str) -> dict:
        ctx.record("search_policy")
        with ctx.timed("retrieval"):
            result = _search_policy(query)
        ctx.cite([p.section_number for p in result.passages])
        return result.model_dump(exclude_none=True)

    return StructuredTool.from_function(
        func=search_policy,
        name="search_policy",
        description=(
            "Search Trendly's shipping and returns policy and return the most relevant passages "
            "with their section numbers. This is the ONLY permitted source for any policy claim — "
            "never answer a policy question from general knowledge. Cite the section numbers you "
            "used. If the passages do not answer the question, the policy is silent on it: say so "
            "and escalate."
        ),
        args_schema=SearchPolicyArgs,
    )
