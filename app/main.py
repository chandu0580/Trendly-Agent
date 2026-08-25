"""FastAPI application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .agent import get_agent
from .config import settings
from .models.api import (
    Action,
    ChatRequest,
    ChatResponse,
    ErrorResponse,
    HealthResponse,
    TurnDiagnostics,
)
from .retrieval.vectorstore import indexed_section_count
from .services.identity import (
    IdentityError,
    profile_for_verified,
    resolve_trusted_customer,
)
from .services.order_repository import get_order_repository
from .web import chat_page, landing_page

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("trendly.api")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Build the policy index on first boot if it is not there yet.

    Makes `uvicorn app.main:app` a genuine one-command start, and lets the
    service recover if a deployment's build step did not run. `ingest()` is
    idempotent — an index that matches the policy digest is a no-op — so the
    cost on every later boot is a digest comparison.

    Never fatal: retrieval degrades to lexical search over the same clauses if
    this fails, and refusing to start would be the worse outcome.
    """
    from .retrieval.ingest import ingest
    from .retrieval.vectorstore import VECTORSTORE_DIR

    try:
        if not VECTORSTORE_DIR.exists():
            log.info("no policy index found; building it now")
        log.info("policy index ready: %s clauses", ingest(verbose=False))
    except Exception:
        log.exception("policy ingest failed; retrieval will fall back to lexical search")
    log.info(
        "llm: model=%s endpoint=%s key=%s mode=%s",
        settings.llm_model,
        settings.llm_base_url,
        "set" if settings.llm_configured else "MISSING",
        settings.agent_mode,
    )
    yield


app = FastAPI(
    title="Trendly Agentic Support Assistant",
    version="2.0.0",
    description="LangGraph support agent with policy RAG, deterministic eligibility, and gated actions.",
    lifespan=lifespan,
)


@app.get("/", include_in_schema=False)
def root():
    return landing_page()


@app.get("/agent", include_in_schema=False)
def agent_page():
    return chat_page()


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    indexed = indexed_section_count()
    return HealthResponse(
        status="ok" if indexed else "degraded",
        orders_loaded=get_order_repository().order_count,
        policy_sections_indexed=indexed,
        llm_configured=settings.llm_configured,
        # Named so a provider mismatch is visible rather than silent: a key for
        # one endpoint against another's URL fails every call and degrades to
        # the fallback, which still answers. "Which model is this actually
        # running?" is the first question when replies look worse than expected.
        llm_model=settings.llm_model,
        agent_mode=settings.agent_mode,
    )


@app.post("/chat", response_model=ChatResponse, responses={422: {"model": ErrorResponse}})
def chat(request: ChatRequest):
    as_of: date | None = None
    if request.as_of:
        try:
            as_of = date.fromisoformat(request.as_of)
        except ValueError:
            return JSONResponse(
                status_code=422,
                content=ErrorResponse(
                    error="invalid_as_of", detail="as_of must be a YYYY-MM-DD date"
                ).model_dump(),
            )

    # Identity is resolved and bound to the session before the agent is invoked.
    # An unverifiable identity, or one that conflicts with this session's
    # existing binding, never reaches a tool.
    try:
        trusted_customer = resolve_trusted_customer(request.session_id, request.customer_id)
    except IdentityError as exc:
        log.warning(
            "identity rejected session=%s reason=%s", request.session_id, exc.error
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(error=exc.error, detail=exc.detail).model_dump(),
        )

    try:
        result = get_agent().respond(
            session_id=request.session_id,
            message=request.message,
            customer_id=trusted_customer,
            as_of=as_of,
        )
    except Exception:
        # Never leak internals to a customer.
        log.exception("unhandled error on session=%s", request.session_id)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error="internal_error",
                detail="The assistant could not complete that request. Please try again or ask for a human agent.",
            ).model_dump(),
        )

    return ChatResponse(
        session_id=request.session_id,
        message=result.reply,
        status=result.status,
        actions=[Action(**a) for a in result.actions],
        handoff_summary=result.handoff_summary,
        tool_trace=result.ctx.trace,
        policy_sections=result.ctx.policy_sections,
        mode=result.mode,
        diagnostics=TurnDiagnostics(
            trace_id=result.trace_id,
            elapsed_ms=result.elapsed_ms,
            agent_steps=result.ctx.agent_steps,
            tool_calls=result.ctx.tool_calls,
            loop_limit_reached=result.ctx.loop_limit_reached,
            verification_state=result.verification_state,
            timings_ms=result.ctx.timings,
        ),
        # Only on a turn that actually reached VERIFIED, and only this
        # customer's own record. The gate lives in the service, not here.
        customer=profile_for_verified(
            result.ctx.customer_id, result.verification_state == "verified"
        ),
    )


# Compatibility alias so an already-published demo URL keeps working.
@app.post("/v1/chat", response_model=ChatResponse, include_in_schema=False)
def chat_v1(request: ChatRequest):
    return chat(request)


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=False,
    )
