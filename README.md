# Trendly Agentic Support Assistant

An API-first support assistant for Trendly’s shipping, return, exchange, refund, and escalation flows. It uses **real LLM function calling** when a free-tier compatible model key is configured, backed by strict server-side tools. A deterministic, safety-first offline mode is included so reviewers can run the API and regression suite without credentials or cost.

## Run locally

Requirements: Python 3.11+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The base URL is `http://127.0.0.1:8000`; interactive API docs are at `/docs`.

To use an actual free-tier tool-calling model, copy `.env.example` to `.env`, set `LLM_API_KEY` to a Google AI Studio key, then load those environment variables in your shell. The default base URL/model targets Gemini’s OpenAI-compatible API. No paid LLM is required. Without a key, `AGENT_MODE=auto` selects the offline deterministic mode. Use `AGENT_MODE=llm` to fail closed rather than falling back if the LLM is unavailable.

Example request:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/v1/chat -ContentType 'application/json' -Body '{"session_id":"demo-1","customer_id":"C-101","message":"I want to return TR-4530","as_of":"2026-08-24"}'
```

Reply `confirm` with the same `session_id` and `customer_id` to create the return. `as_of` is an optional deterministic test clock; omit it in normal use.

## API contract

`POST /v1/chat`

```json
{
  "session_id": "browser-or-channel-conversation-id",
  "customer_id": "C-101",
  "message": "Where is TR-4526?",
  "as_of": "2026-08-24"
}
```

Responses contain a natural-language `reply`, auditable `actions` (`return_created`, `exchange_created`, or `escalated`), an optional human-readable `handoff_summary`, and the execution `mode`.

`customer_id` represents the identity provided by an upstream authenticated channel. The service does not trust an order ID alone: every order tool checks ownership before returning data.

## Test

```powershell
python -m pytest tests -q
```

The suite covers the happy-path confirmation flow, lost parcel escalation, non-returnable jewellery, final-sale handling, isolation between customers, partial shipments, bank-detail refusal, and unknown-policy escalation.

## Implementation overview

The agent has a bounded ReAct/function-calling loop. The LLM can only see information returned by these server-enforced tools:

- `lookup_order`: ownership-scoped order facts
- `get_policy`: excerpts from `trendly_policy.md`, the sole policy source
- `check_return_eligibility` / `check_exchange_eligibility`: deterministic rule evaluation
- `create_return` / `create_exchange`: state-changing actions gated by prior eligibility and explicit confirmation
- `escalate_to_human`: structured handoff creation

The model cannot bypass the order ownership check, policy source, confirmation step, or action preconditions. `orders.json` and `trendly_policy.md` are copied unchanged from the supplied assignment files into `data/` for portable execution.

See [PROMPTS.md](PROMPTS.md) for the prompt and iteration notes, [SOLUTION.md](SOLUTION.md) for the delivery note, and [DEMO.md](DEMO.md) for a ready-to-record video runbook.

## AI-usage note

I used AI assistance to accelerate boilerplate, test ideas, and documentation drafting. I designed and reviewed the architecture, tool contracts, safety invariants, policy mapping, and tests; the resulting code is intentionally small enough to explain and modify live.
