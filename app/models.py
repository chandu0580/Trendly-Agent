from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str = Field(min_length=1, max_length=128)
    customer_id: str = Field(min_length=1, max_length=64)
    as_of: str | None = Field(default=None, description="Optional YYYY-MM-DD test clock")


class Action(BaseModel):
    type: Literal["return_created", "exchange_created", "escalated"]
    reference: str
    details: dict = Field(default_factory=dict)


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    actions: list[Action] = Field(default_factory=list)
    handoff_summary: str | None = None
    mode: Literal["llm", "deterministic"]
