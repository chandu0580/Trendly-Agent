from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .agent import SupportAgent
from .models import Action, ChatRequest, ChatResponse
from .web import chat_page

app = FastAPI(title="Trendly Agentic Support Assistant", version="1.0.0")
agent = SupportAgent()


@app.get("/", include_in_schema=False)
def root():
    return chat_page()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "trendly-support-agent"}


@app.post("/v1/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    try:
        reply, actions, handoff, mode = agent.respond(request.session_id, request.customer_id, request.message, request.as_of)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="as_of must be a YYYY-MM-DD date") from exc
    return ChatResponse(reply=reply, session_id=request.session_id, actions=[Action(**a) for a in actions], handoff_summary=handoff, mode=mode)
