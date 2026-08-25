"""LangGraph orchestration.

The graph is deliberately small:

    START -> agent -> (tool calls?) -> tools -> agent
                   \\-> guard -> (needs another grounded round?) -> agent
                                \\-> END

`agent` reasons and selects tools. `tools` executes them under the authorization
layer. `guard` inspects the drafted reply before it can reach the customer and
either sends it back for another grounded round or lets it through. Nothing in
the graph can widen what a tool is allowed to do.

Failure handling is layered: transient provider errors retry, a gateway that
rejects forced tool choice degrades, a refused tool teaches the model the
protocol, the round limit escalates, and only when all of that is exhausted does
the deterministic fallback answer.
"""

from __future__ import annotations

import logging
import time
from uuid import uuid4
from datetime import date

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from ..config import settings
from ..models.conversation import AgentState, ConversationState, VerificationState
from ..services.authorization import age_proposal, reads_as_confirmation, reads_as_decline
from ..services.clock import get_clock
from ..services.identity import bind_verified_customer
from ..tools import build_toolset
from ..tools.context import ToolContext
from . import prompts
from .fallback import fallback_reply
from .state import (
    SessionStore,
    contains_sensitive_data,
    fabricated_references,
    is_dead_end,
    is_out_of_scope,
    needs_grounding,
    resolve_order_id,
    unchecked_eligibility,
)

log = logging.getLogger("trendly.agent")

TRANSIENT_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
TRANSIENT_NAMES = ("Timeout", "Connection", "InternalServer", "RateLimit", "APIError", "Overloaded")


class TurnResult:
    """What one turn produced, ready for the API layer."""

    def __init__(
        self,
        reply: str,
        ctx: ToolContext,
        mode: str,
        status: str = "completed",
    ) -> None:
        self.reply = reply
        self.ctx = ctx
        self.mode = mode
        self.status = status
        self.verification_state = "unverified"
        self.trace_id = ctx.trace_id
        self.elapsed_ms = 0.0

    @property
    def actions(self) -> list[dict]:
        return self.ctx.actions

    @property
    def handoff_summary(self) -> str | None:
        escalations = self.ctx.escalations
        return escalations[-1]["details"].get("summary") if escalations else None


def _is_transient(exc: Exception) -> bool:
    """Distinguish "try again" from "this request is wrong"."""
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    if status in TRANSIENT_STATUS:
        return True
    return any(term in type(exc).__name__ for term in TRANSIENT_NAMES)


class TrendlyAgent:
    """One support agent with a bounded, guarded tool-calling loop."""

    def __init__(self, mode: str | None = None, llm_factory=None) -> None:
        self.mode = (mode or settings.agent_mode).lower()
        self.llm_factory = llm_factory
        self.sessions = SessionStore()
        self.last_error: str | None = None

    # ------------------------------------------------------------------ llm

    def _llm_available(self) -> bool:
        return self.mode in {"auto", "llm"} and bool(settings.llm_configured or self.llm_factory)

    def _make_llm(self):
        if self.llm_factory:
            return self.llm_factory()
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            temperature=settings.llm_temperature,
            max_retries=0,  # retries are handled here so they can be logged and bounded
        )

    def _invoke(self, llm, tools, messages: list, force_tools: bool):
        """One provider call, with transient retries and capability degradation."""
        choice = "required" if force_tools else "auto"

        def call(tool_choice: str):
            bound = llm.bind_tools(tools, tool_choice=tool_choice) if tool_choice == "required" else llm.bind_tools(tools)
            return bound.invoke(messages)

        for attempt in range(settings.transient_retries + 1):
            try:
                return call(choice)
            except Exception as exc:
                if choice == "required" and not _is_transient(exc):
                    # Some free-tier gateways reject forced tool choice outright.
                    # That is a capability limit, not a blip, so degrade instead
                    # of retrying; the grounding guard still catches an
                    # unfounded reply.
                    self.last_error = "tool_choice=required unsupported; retried with auto"
                    log.warning("provider rejected forced tool choice; degrading to auto")
                    choice = "auto"
                    continue
                if attempt == settings.transient_retries or not _is_transient(exc):
                    raise
                self.last_error = f"transient {type(exc).__name__}, retry {attempt + 1}"
                log.warning("transient provider error, retry %s: %s", attempt + 1, exc)
                time.sleep(settings.retry_backoff * (2**attempt))
        raise RuntimeError("unreachable")

    # ---------------------------------------------------------------- graph

    def _build_graph(self, llm, tools, ctx: ToolContext, message: str):
        tool_node = ToolNode(tools)

        def agent_node(state: AgentState) -> dict:
            ctx.agent_steps += 1
            with ctx.timed("agent"):
                response = self._invoke(
                    llm, tools, state["messages"], state.get("force_tools", False)
                )
            return {"messages": [response], "force_tools": False}

        def timed_tool_node(state: AgentState) -> dict:
            # ToolNode runs every tool the model asked for in this round, so the
            # measurement is per round rather than per tool. Retrieval reports
            # itself separately, which is the split worth having.
            with ctx.timed("tools"):
                return tool_node.invoke(state)

        def guard_node(state: AgentState) -> dict:
            """Inspect the drafted reply. Can only send it back, never rewrite it."""
            with ctx.timed("guard"):
                return decide(state)

        def decide(state: AgentState) -> dict:
            last = state["messages"][-1]
            content = last.content if isinstance(last, AIMessage) else None
            nudges = state.get("nudges", 0)

            if nudges >= settings.max_nudges:
                return {"force_tools": False}

            # Eligibility first: it is the more specific remedy, and "I can't say
            # whether you can return that" reads as a dead end when what it needs
            # is the rule engine's verdict.
            missing = unchecked_eligibility(message, ctx)
            if missing:
                log.info("trace=%s guard: missing %s", ctx.trace_id, missing)
                return {
                    "messages": [HumanMessage(content=prompts.nudge_for_missing_check(missing))],
                    "nudges": nudges + 1,
                    "force_tools": True,
                }

            invented = fabricated_references(content, ctx)
            if invented:
                log.warning("trace=%s guard: invented references %s", ctx.trace_id, invented)
                return {
                    "messages": [HumanMessage(content=prompts.nudge_for_invented_reference(invented))],
                    "nudges": nudges + 1,
                    "force_tools": True,
                }

            # An out-of-scope decline is a complete answer, so the dead-end
            # nudge must not turn it into a support case.
            if is_dead_end(content, ctx) and not is_out_of_scope(message, ctx):
                log.info("trace=%s guard: dead end without escalation", ctx.trace_id)
                return {
                    "messages": [HumanMessage(content=prompts.NUDGE_FOR_DEAD_END)],
                    "nudges": nudges + 1,
                    "force_tools": True,
                }

            return {"force_tools": False}

        def limit_node(_state: AgentState) -> dict:
            """Terminal. The turn is over; a human picks it up.

            Reached only by exhausting the step budget, which means the model
            kept asking for tools without arriving at an answer. Escalating is
            the honest end — inventing a result would be worse than admitting
            the loop.
            """
            ctx.loop_limit_reached = True
            log.warning(
                "trace=%s loop limit reached: steps=%s tools=%s",
                ctx.trace_id, ctx.agent_steps, ctx.tool_calls,
            )
            escalate = next(t for t in tools if t.name == "escalate_to_human")
            escalate.invoke(
                {
                    "reason": "tool_loop_limit",
                    "summary": prompts.LOOP_LIMIT_SUMMARY.format(message=message),
                    "required_human_action": (
                        "Pick up the request manually; the assistant could not resolve it "
                        "within its tool budget."
                    ),
                }
            )
            return {"messages": [AIMessage(content=prompts.LOOP_LIMIT_REPLY)], "force_tools": False}

        def route_after_agent(state: AgentState) -> str:
            # Hard cap, checked before any further tool work. The model is never
            # trusted to stop itself.
            if ctx.agent_steps >= settings.max_agent_steps:
                return "limit"
            last = state["messages"][-1]
            return "tools" if getattr(last, "tool_calls", None) else "guard"

        def route_after_guard(state: AgentState) -> str:
            return "agent" if state.get("force_tools") else END

        graph = StateGraph(AgentState)
        graph.add_node("agent", agent_node)
        graph.add_node("tools", timed_tool_node)
        graph.add_node("guard", guard_node)
        graph.add_node("limit", limit_node)
        graph.set_entry_point("agent")
        graph.add_conditional_edges(
            "agent", route_after_agent, {"tools": "tools", "guard": "guard", "limit": "limit"}
        )
        graph.add_edge("limit", END)
        graph.add_edge("tools", "agent")
        graph.add_conditional_edges("guard", route_after_guard, {"agent": "agent", END: END})
        return graph.compile()

    # ----------------------------------------------------------------- turn

    def respond(
        self,
        session_id: str,
        message: str,
        customer_id: str | None = None,
        as_of: date | None = None,
        trace_id: str | None = None,
    ) -> TurnResult:
        """One turn. Serialised per session so concurrent requests on the same
        conversation cannot interleave a read-modify-write on its state."""
        trace = trace_id or uuid4().hex[:12]
        started = time.perf_counter()
        with self.sessions.lock_for(session_id):
            result = self._respond_locked(session_id, message, customer_id, as_of, trace)
        result.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        result.trace_id = trace
        return result

    def _respond_locked(
        self,
        session_id: str,
        message: str,
        customer_id: str | None,
        as_of: date | None,
        trace: str,
    ) -> TurnResult:
        from ..services.order_repository import get_order_repository

        repo = get_order_repository()
        if customer_id and not repo.customer_exists(customer_id):
            ctx = ToolContext.build(None, as_of or _resolved_clock(), trace_id=trace)
            return TurnResult(
                "I couldn't verify that customer ID. Please check it and try again.",
                ctx,
                "deterministic",
                "failed",
            )

        state = self.sessions.get(session_id, customer_id or "")
        if state and customer_id and state.verified_customer_id and state.verified_customer_id != customer_id:
            # Session fixation / tenant mix-up guard.
            ctx = ToolContext.build(state.verified_customer_id, as_of or _resolved_clock(), trace_id=trace)
            return TurnResult(
                "For your privacy, this conversation is tied to a different verified customer. "
                "Please start a new session.",
                ctx,
                "deterministic",
                "failed",
            )
        state = state or self.sessions.start(session_id, customer_id or "")
        # A trusted channel identity verifies the customer immediately; without
        # one the session stays unverified until verify_identity succeeds.
        if customer_id and not state.verified_customer_id:
            # A trusted channel identity verifies the customer; the order is
            # still ownership-checked on every lookup.
            state.customer_id = customer_id
            state.verified_customer_id = customer_id
            state.verification_state = VerificationState.VERIFIED
            state.order_verified = bool(state.active_order_id)

        declined = reads_as_decline(message)
        clock = as_of or _resolved_clock()
        ctx = ToolContext.build(
            customer_id=state.verified_customer_id,
            as_of=clock,
            user_confirmed=reads_as_confirmation(message),
            # An explicit "no" retires the outstanding offer at once, so a later
            # unrelated "yes" cannot land on something already turned down.
            pending=None if declined else state.pending_action,
            trace_id=trace,
            verified=state.has_verified_customer,
            verified_order_id=state.active_order_id if state.order_verified else None,
            customer_utterances=" ".join(
                [m["content"] for m in state.messages if m["role"] == "user"] + [message]
            ),
        )

        # Pre-model gate: card and bank content is refused before it can reach
        # the provider or be echoed back, so it cannot be prompt-injected away.
        if contains_sensitive_data(message):
            log.info("trace=%s session=%s sensitive content refused pre-model", trace, session_id)
            return self._finish(state, message, prompts.SENSITIVE_REFUSAL, ctx, "deterministic")

        if self._llm_available():
            try:
                reply = self._run_graph(state, message, ctx)
                if reply is not None:
                    return self._finish(state, message, reply, ctx, "llm")
                ctx = ctx.for_retry()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.error("trace=%s session=%s provider failure: %s", trace, session_id, self.last_error)
                ctx = ctx.for_retry()
                if self.mode == "llm":
                    reply = (
                        prompts.PROVIDER_DOWN_AFTER_MUTATION_REPLY
                        if ctx.mutations
                        else prompts.PROVIDER_DOWN_REPLY
                    )
                    return self._finish(state, message, reply, ctx, "llm", status="degraded")

        reply = fallback_reply(message, ctx, state)
        return self._finish(state, message, reply, ctx, "deterministic")

    def _run_graph(self, state: ConversationState, message: str, ctx: ToolContext) -> str | None:
        """Returns the reply, or None to hand the turn to the fallback."""
        llm = self._make_llm()
        tools = build_toolset(ctx)
        graph = self._build_graph(llm, tools, ctx, message)

        history = [
            HumanMessage(content=m["content"]) if m["role"] == "user" else AIMessage(content=m["content"])
            for m in state.messages[-6:]
        ]
        pending = state.pending_action
        context_block = prompts.conversation_context_block(
            verification_state=state.verification_state.value,
            verified_customer_id=state.verified_customer_id,
            active_order_id=state.active_order_id,
            active_item_id=pending.item_id if pending else None,
            pending_action=(
                f"{pending.kind} of {pending.item_id} on {pending.order_id}, awaiting confirmation"
                if pending
                else "none"
            ),
        )
        initial: AgentState = {
            "messages": [
                SystemMessage(content=prompts.SYSTEM_PROMPT),
                *history,
                SystemMessage(content=context_block),
                HumanMessage(content=message),
            ],
            "session_id": state.session_id,
            "customer_id": state.customer_id,
            "nudges": 0,
            # The first round must be a tool call whenever the turn could carry
            # a factual claim: models otherwise answer an order question from
            # prior context, and a claim made from memory alone must never
            # reach the customer.
            #
            # Not for a bare greeting, though. Forcing a lookup on "hi" made the
            # tool answer "no identifiers supplied", which pushed the model into
            # demanding a customer ID before the customer had asked for
            # anything. There is no claim to ground in "hi", and the grounding
            # guard still refuses an ungrounded answer to anything that is.
            "force_tools": needs_grounding(message, state.pending_action),
        }

        # LangGraph's own backstop, derived from the real cap rather than set
        # independently: `max_agent_steps` always fires first and ends the turn
        # cleanly, so this only needs to sit above the worst-case node count
        # (agent + tools per step, plus guard and limit). Deriving it means
        # raising AGENT_MAX_STEPS cannot silently trip a raw GraphRecursionError.
        recursion_limit = settings.max_agent_steps * 2 + 4
        result = graph.invoke(initial, {"recursion_limit": recursion_limit})
        final = result["messages"][-1]
        content = final.content if isinstance(final, AIMessage) else None

        if not ctx.trace and needs_grounding(message, ctx.auth.incoming):
            self.last_error = "llm_answered_without_grounding"
            log.warning("trace=%s ungrounded answer refused; using fallback", ctx.trace_id)
            return None

        if unchecked_eligibility(message, ctx):
            self.last_error = "llm_skipped_eligibility_check"
            log.warning("trace=%s eligibility verdict without a check; using fallback", ctx.trace_id)
            return None

        # Last line of defence. The guard nudges first, but a nudge budget can
        # run out, and a reply promising a ticket that does not exist must never
        # ship. The fallback answers honestly instead.
        invented = fabricated_references(content, ctx)
        if invented:
            self.last_error = f"llm_invented_reference:{','.join(invented)}"
            log.error("trace=%s invented reference %s; using fallback", ctx.trace_id, invented)
            return None

        if not content:
            return None
        return content

    def _finish(
        self,
        state: ConversationState,
        message: str,
        reply: str,
        ctx: ToolContext,
        mode: str,
        status: str | None = None,
    ) -> TurnResult:
        state.messages.extend(
            [{"role": "user", "content": message}, {"role": "assistant", "content": reply}]
        )
        state.messages = state.messages[-12:]
        state.pending_action = age_proposal(ctx.auth.pending)

        if ctx.newly_verified:
            # Mirror it into the session registry so a later HTTP request naming
            # a different customer still conflicts.
            bind_verified_customer(state.session_id, ctx.newly_verified["customer_id"])
            state.mark_verified(
                ctx.newly_verified["customer_id"], ctx.newly_verified["order_id"]
            )
        elif ctx.verification_phase == "identifiers_collected":
            state.mark_identifiers_collected()
        elif ctx.verification_phase == "failed":
            state.mark_verification_failed()
        elif ctx.verification_phase == "verifying":
            state.mark_verifying()

        active = resolve_order_id(message, state)
        if active and active in ctx.auth.found_orders:
            state.active_order_id = active
            state.order_verified = True

        if ctx.escalations:
            state.escalation_status = ctx.escalations[-1]["reference"]

        resolved_status = status or ("escalated" if ctx.escalations else "completed")
        log.info(
            "trace=%s session=%s mode=%s tools=%s actions=%s steps=%s",
            ctx.trace_id,
            state.session_id,
            mode,
            ",".join(ctx.trace) or "-",
            ",".join(a["type"] for a in ctx.actions) or "-",
            ctx.agent_steps,
        )
        result = TurnResult(reply, ctx, mode, resolved_status)
        result.verification_state = state.verification_state.value
        return result


def _resolved_clock() -> date:
    """The request clock.

    `DEMO_AS_OF` pins it to the fixture's authored reference date (see
    docs/architecture.md §4); clearing it falls through to the installed clock,
    which is the real one in production and a `FixedClock` under test.
    """
    if settings.demo_as_of:
        try:
            return date.fromisoformat(settings.demo_as_of)
        except ValueError:
            pass
    return get_clock().today()


_agent: TrendlyAgent | None = None


def get_agent() -> TrendlyAgent:
    global _agent
    if _agent is None:
        _agent = TrendlyAgent()
    return _agent
