"""Shared fixtures.

Nothing here touches the network. Tests that exercise the agent loop drive the
real LangGraph graph with a scripted stand-in model, so orchestration, the
authorization layer, and the guards are all genuinely executed.
"""

from __future__ import annotations

from datetime import date

import pytest
from langchain_core.messages import AIMessage

from app.agent.orchestrator import TrendlyAgent
from app.services.action_service import reset_action_ledger
from app.services.identity import reset_identity_registry
from app.tools.context import ToolContext

# The fixture's authored reference date; see docs/architecture.md §4.
AS_OF = date(2026, 7, 29)

ORDERS = {
    "in_transit": ("C-100", "TR-4521", "TR-DRS-014"),
    "multi_item": ("C-101", "TR-4522", "TR-TSH-002"),
    "socks": ("C-101", "TR-4522", "TR-SOK-031"),
    "old": ("C-102", "TR-4523", "TR-JKT-008"),
    "partial": ("C-100", "TR-4524", "TR-BLT-005"),
    "delayed": ("C-103", "TR-4525", "TR-SNK-017"),
    "lost": ("C-101", "TR-4526", "TR-BAG-011"),
    "jewellery": ("C-102", "TR-4527", "TR-EAR-042"),
    "final_sale": ("C-103", "TR-4528", "TR-SHR-009"),
    "cancelled": ("C-100", "TR-4529", "TR-SCF-027"),
    "happy": ("C-101", "TR-4530", "TR-KRT-033"),
}


@pytest.fixture(autouse=True)
def clean_ledger():
    """Simulated actions and session identity bindings must not leak between tests."""
    reset_action_ledger()
    reset_identity_registry()
    yield
    reset_action_ledger()
    reset_identity_registry()


@pytest.fixture(autouse=True)
def offline_by_default(monkeypatch):
    """Keep the default suite off the network even when a real key is present.

    `settings` is built once at import, so setting the environment variable here
    would be read too late and the suite would quietly call the live provider.
    Patch the resolved object, and drop the cached agent so it is rebuilt in
    deterministic mode.
    """
    from app.agent import orchestrator
    from app.config import settings

    monkeypatch.setattr(settings, "agent_mode", "deterministic")
    monkeypatch.setattr(orchestrator, "_agent", None)
    yield
    orchestrator._agent = None


# --------------------------------------------------------------- scripted LLM


def tool_turn(*calls: tuple[str, dict]) -> AIMessage:
    """An assistant turn that requests one or more tools."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"call_{index}"}
            for index, (name, args) in enumerate(calls)
        ],
    )


def text_turn(content: str) -> AIMessage:
    return AIMessage(content=content)


class ScriptedLLM:
    """Replays scripted assistant turns through the real graph.

    Implements only what the orchestrator uses — `bind_tools` and `invoke` — so
    the graph, the ToolNode, and every guard run for real.
    """

    def __init__(self, *turns: AIMessage, fail_with: Exception | None = None,
                 reject_required: bool = False, fail_first: int = 0):
        self.turns = list(turns)
        self.calls: list[dict] = []
        self.fail_with = fail_with
        self.reject_required = reject_required
        self.fail_first = fail_first
        # `fail_first` means "blip N times then recover"; `fail_with` alone means
        # "the provider is down". Without this they compose into never recovering.
        self._always_fail = fail_with is not None and fail_first == 0
        self._tool_choice = "auto"

    def bind_tools(self, tools, **kwargs):
        self._tool_choice = kwargs.get("tool_choice", "auto")
        return self

    def invoke(self, messages):
        self.calls.append({"tool_choice": self._tool_choice, "messages": list(messages)})
        if self.reject_required and self._tool_choice == "required":
            raise RuntimeError("tool_choice 'required' is not supported by this endpoint")
        if self.fail_first > 0:
            self.fail_first -= 1
            raise self.fail_with or transient(503)
        if self._always_fail:
            raise self.fail_with
        assert self.turns, "the scripted model ran out of turns"
        return self.turns.pop(0)

    # --- assertions helpers ---------------------------------------------

    @property
    def tool_choices(self) -> list[str]:
        return [c["tool_choice"] for c in self.calls]

    def nudges(self) -> list[str]:
        """Guard instructions that were injected back into the conversation."""
        seen: list[str] = []
        for call in self.calls:
            for message in call["messages"]:
                text = getattr(message, "content", "")
                if isinstance(text, str) and text.startswith("[system]") and text not in seen:
                    seen.append(text)
        return seen


def transient(status: int) -> Exception:
    exc = RuntimeError(f"{status} upstream unavailable")
    exc.status_code = status
    return exc


@pytest.fixture
def make_agent():
    """Build an agent driven by a scripted model."""

    def _build(*turns: AIMessage, mode: str = "llm", **kwargs) -> tuple[TrendlyAgent, ScriptedLLM]:
        scripted = ScriptedLLM(*turns, **kwargs)
        agent = TrendlyAgent(mode=mode, llm_factory=lambda: scripted)
        return agent, scripted

    return _build


@pytest.fixture
def offline_agent() -> TrendlyAgent:
    """Agent with no model at all — exercises the deterministic fallback."""
    return TrendlyAgent(mode="deterministic")


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext.build("C-101", AS_OF)


# ------------------------------------------------------------ leakage helpers

OWNERSHIP: dict[str, list[str]] = {
    "C-100": ["TR-4521", "TR-4524", "TR-4529"],
    "C-101": ["TR-4522", "TR-4526", "TR-4530"],
    "C-102": ["TR-4523", "TR-4527"],
    "C-103": ["TR-4525", "TR-4528"],
}


def owner_of(order_id: str) -> str:
    return next(c for c, orders in OWNERSHIP.items() for o in orders if o == order_id)


def sensitive_values(order_id: str) -> list[str]:
    """Every field of the real order that a non-owner must never see."""
    from app.services.order_repository import get_order_repository

    repo = get_order_repository()
    owner = owner_of(order_id)
    order = repo.get_for_customer(order_id, owner)
    customer = repo._customers[owner]

    values = [
        customer.name,
        customer.email,
        customer.phone,
        order.status,
        order.tracking_number,
        order.carrier,
        order.shipping_city,
        order.payment_method,
    ]
    for item in order.items:
        values += [item.sku, item.name, str(item.price)]
    # Statuses like "delivered" are common English; only assert on values that
    # would be meaningful evidence of disclosure.
    return [str(v) for v in values if v and len(str(v)) > 4]


def assert_no_disclosure(text: str, order_id: str, visible_to: str | None = None) -> None:
    """Assert nothing about `order_id` appears in `text`.

    Field values are not unique — two orders can share a carrier or a status — so
    when the customer legitimately sees their own orders, values that also appear
    there are excluded. Otherwise a correct answer about the customer's own order
    would read as a leak about someone else's.
    """
    shared: set[str] = set()
    if visible_to:
        for own in OWNERSHIP.get(visible_to, []):
            if own != order_id:
                shared |= {v.lower() for v in sensitive_values(own)}

    lowered = text.lower()
    for value in sensitive_values(order_id):
        if value.lower() in shared:
            continue
        assert value.lower() not in lowered, f"disclosed {value!r} about {order_id}:\n{text}"
