"""Per-turn tool context.

Tools are built fresh for each request and closed over this object, so identity
and authorization come from server-side context rather than from tool arguments.
The model cannot pass a different `customer_id` because there is no such
parameter on any tool.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date

from ..models.conversation import PendingAction
from ..services.action_service import ActionLedger, get_action_ledger
from ..services.authorization import TurnAuthorization
from ..services.order_repository import OrderRepository, get_order_repository

log = logging.getLogger(__name__)


@dataclass
class ToolContext:
    # None until the application has verified an identity for this session. Every
    # order tool refuses while it is None, so an unverified conversation cannot
    # reach order data by any route.
    customer_id: str | None
    as_of: date
    auth: TurnAuthorization
    repository: OrderRepository = field(default_factory=get_order_repository)
    ledger: ActionLedger = field(default_factory=get_action_ledger)
    actions: list[dict] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
    policy_sections: list[str] = field(default_factory=list)
    # Written by `verify_identity` when the application confirms an identity or
    # an order for this turn; the orchestrator persists it into session state.
    newly_verified: dict | None = None
    verified_order_id: str | None = None
    # Which lifecycle phase this turn reached, for the orchestrator to persist.
    verification_phase: str | None = None
    # What the customer has actually typed in this conversation. Identifiers
    # submitted for verification must appear here; the model may extract them,
    # never supply them.
    customer_utterances: str = ""
    # The turn's own toolset, so a tool that must escalate deterministically
    # can reach escalate_to_human without rebuilding one.
    pending_toolset: list = field(default_factory=list)
    # Narrated into an escalation summary so a human sees what was already tried.
    last_eligibility: str | None = None
    last_action_attempted: str | None = None
    last_action_result: str | None = None
    # Loop accounting, reported on the response so a runaway turn is visible.
    agent_steps: int = 0
    tool_calls: int = 0
    loop_limit_reached: bool = False
    # Mirrored from ConversationState.has_verified_customer, which is the
    # authoritative definition. A truthy customer id is not, by itself,
    # verification.
    verified: bool = False
    # Correlates every log line, tool call, and the response for one run.
    trace_id: str = ""
    # Per-step wall time, so a slow turn can be attributed to a component rather
    # than only observed as slow. Appended in completion order.
    steps: list[dict] = field(default_factory=list)
    # Open timing frames; each holds the ms already claimed by nested steps.
    _nested: list[float] = field(default_factory=list, repr=False)

    @property
    def is_verified(self) -> bool:
        return self.verified and bool(self.customer_id)

    @contextmanager
    def timed(self, component: str):
        """Time one step and record it against this turn's trace.

        Wall time, not CPU: the number that matters here is how long the
        customer waited, and most of it is spent blocked on a provider.

        Steps nest — retrieval runs inside a tool round — so each records both
        the elapsed wall time and the share not spent inside a nested step.
        Without that split the components sum to more than the turn took, and
        a breakdown that exceeds its own total is worse than none.
        """
        started = time.perf_counter()
        self._nested.append(0.0)
        try:
            yield
        finally:
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            children = self._nested.pop()
            if self._nested:
                self._nested[-1] += elapsed
            self.steps.append(
                {
                    "step": len(self.steps) + 1,
                    "component": component,
                    "elapsed_ms": elapsed,
                    "self_ms": round(max(elapsed - children, 0.0), 2),
                }
            )
            log.debug(
                "trace=%s step=%s component=%s elapsed_ms=%s",
                self.trace_id, len(self.steps), component, elapsed,
            )

    @property
    def timings(self) -> dict[str, float]:
        """Ms per component, exclusive of nested steps, so the parts sum to the whole."""
        totals: dict[str, float] = {}
        for step in self.steps:
            totals[step["component"]] = round(
                totals.get(step["component"], 0.0) + step["self_ms"], 2
            )
        return totals

    @classmethod
    def build(
        cls,
        customer_id: str | None,
        as_of: date,
        user_confirmed: bool = False,
        pending: PendingAction | None = None,
        verified_order_id: str | None = None,
        customer_utterances: str = "",
        verified: bool | None = None,
        trace_id: str = "",
    ) -> "ToolContext":
        return cls(
            customer_id=customer_id,
            as_of=as_of,
            auth=TurnAuthorization(
                customer_id=customer_id or "", user_confirmed=user_confirmed, incoming=pending
            ),
            verified_order_id=verified_order_id,
            customer_utterances=customer_utterances,
            # The orchestrator always passes the authoritative value. Direct
            # construction (tests, the fallback) treats a supplied identity as
            # already verified, which is the only way it can have been obtained.
            verified=(customer_id is not None) if verified is None else verified,
            trace_id=trace_id,
        )

    # --- bookkeeping -----------------------------------------------------

    def record(self, tool_name: str) -> None:
        self.trace.append(tool_name)
        self.tool_calls += 1

    def add_action(self, action_type: str, reference: str, details: dict) -> None:
        self.actions.append({"type": action_type, "reference": reference, "details": details})

    def cite(self, sections: list[str]) -> None:
        for section in sections:
            if section not in self.policy_sections:
                self.policy_sections.append(section)

    # --- queries ---------------------------------------------------------

    @property
    def escalations(self) -> list[dict]:
        return [a for a in self.actions if a["type"] == "escalated"]

    @property
    def mutations(self) -> list[dict]:
        return [a for a in self.actions if a["type"] != "escalated"]

    def for_retry(self) -> "ToolContext":
        """A clean context for the fallback after an abandoned model turn.

        Mutations are already durable in the ledger and must still be reported.
        Escalations are provisional records whose case reference the customer
        never saw — carrying them would let the fallback raise a second ticket
        for one question.
        """
        fresh = ToolContext.build(
            self.customer_id,
            self.as_of,
            self.auth.user_confirmed,
            self.auth.pending,
            customer_utterances=self.customer_utterances,
            verified=self.verified,
            trace_id=self.trace_id,
        )
        fresh.actions = list(self.mutations)
        fresh.trace = list(self.trace)
        fresh.policy_sections = list(self.policy_sections)
        # Carry the cost forward. The abandoned rounds still happened — the
        # customer waited for them — and zeroing the counters made the most
        # expensive turns look like the cheapest: one live turn burned seven
        # tool calls over 39s and reported "0 steps, 1 tool".
        fresh.agent_steps = self.agent_steps
        fresh.tool_calls = self.tool_calls
        fresh.steps = list(self.steps)
        fresh.loop_limit_reached = self.loop_limit_reached
        return fresh
