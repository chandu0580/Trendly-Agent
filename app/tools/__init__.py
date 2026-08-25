"""The complete toolset. These are the only capabilities the model has."""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from .context import ToolContext
from .escalation import build_escalate_to_human, build_issue_delay_credit
from .exchanges import build_check_exchange_eligibility, build_initiate_exchange
from .orders import build_get_order, build_list_my_orders, build_verify_identity
from .policy import build_search_policy
from .refunds import build_get_refund_timing, build_quote_shipping_fee
from .returns import build_check_return_eligibility, build_initiate_return

MUTATING_TOOLS = frozenset({"initiate_return", "initiate_exchange", "issue_delay_credit"})


def build_toolset(ctx: ToolContext) -> list[StructuredTool]:
    """Build tools bound to one turn's context.

    Rebuilding per turn is what keeps identity and authorization server-side:
    no tool takes a `customer_id`, so the model has no way to ask about anyone
    else's order.
    """
    toolset = [
        build_verify_identity(ctx),
        build_get_order(ctx),
        build_list_my_orders(ctx),
        build_search_policy(ctx),
        build_get_refund_timing(ctx),
        build_quote_shipping_fee(ctx),
        build_check_return_eligibility(ctx),
        build_initiate_return(ctx),
        build_check_exchange_eligibility(ctx),
        build_initiate_exchange(ctx),
        build_issue_delay_credit(ctx),
        build_escalate_to_human(ctx),
    ]
    # Exposed on the context so a tool with a deterministic handoff (a COD
    # refund) can invoke escalate_to_human through the same guarded path.
    ctx.pending_toolset = toolset
    return toolset


__all__ = ["MUTATING_TOOLS", "ToolContext", "build_toolset"]
