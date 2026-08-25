"""HTTP contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    customer_id: str | None = Field(
        default=None,
        description=(
            "Identity asserted by an upstream authenticated channel. Optional so the "
            "documented {session_id, message} contract works as-is; when omitted the "
            "session keeps a demo identity. Every order tool re-checks ownership "
            "server-side regardless."
        ),
    )
    as_of: str | None = Field(
        default=None,
        description="Optional YYYY-MM-DD clock override. See README, 'A note on dates'.",
    )


class Action(BaseModel):
    type: Literal["return_created", "exchange_created", "credit_issued", "escalated"]
    reference: str
    details: dict = Field(default_factory=dict)


class TurnDiagnostics(BaseModel):
    """What the turn cost. Makes a runaway loop visible instead of merely slow."""

    trace_id: str = ""
    agent_steps: int = 0
    tool_calls: int = 0
    elapsed_ms: float = 0.0
    loop_limit_reached: bool = False
    verification_state: str = "unverified"
    timings_ms: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Wall time per component (agent, tools, retrieval, guard). Attributes a "
            "slow turn instead of only reporting that it was slow."
        ),
    )


class CustomerProfile(BaseModel):
    """Contact details for the *verified* customer, for the support desk panel.

    Present only on a turn that ended verified, and only ever the caller's own
    record. Demo fixture data; the supplied dataset carries no contact fields.
    """

    customer_id: str
    name: str
    email: str
    mobile: str


class ChatResponse(BaseModel):
    session_id: str
    message: str
    status: Literal["completed", "escalated", "degraded", "failed"] = "completed"
    actions: list[Action] = Field(default_factory=list)
    handoff_summary: str | None = None
    tool_trace: list[str] = Field(
        default_factory=list,
        description="Tools invoked this turn, in order. Every factual claim traces to one.",
    )
    policy_sections: list[str] = Field(
        default_factory=list,
        description="Policy sections retrieved as evidence for this turn.",
    )
    mode: Literal["llm", "deterministic"] = "llm"
    customer: CustomerProfile | None = Field(
        default=None,
        description="The verified customer's own profile. Null until verification succeeds.",
    )
    diagnostics: TurnDiagnostics = Field(default_factory=TurnDiagnostics)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str = "trendly-support-agent"
    orders_loaded: int = 0
    policy_sections_indexed: int = 0
    llm_configured: bool = False
    llm_model: str = ""
    agent_mode: str = ""


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
