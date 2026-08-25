# Trendly — Agentic Support Assistant

A support agent for a D2C fashion retailer's order, shipping, return, exchange, and refund flows.
Built on **real tool-calling** with LangGraph: the model reasons and chooses tools, but **never
decides anything transactional** — ownership, eligibility, and every state change are settled in
code beneath it.

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8000        # then open http://127.0.0.1:8000/agent
```

> **594 tests** · 12 tools · 28 cited policy clauses · **0 data leaked** across a 40-combination
> ownership matrix · answers grounded only in the supplied policy document

**Jump to:** [What it does](#what-the-agent-does) · [Architecture](#architecture) ·
[Repo layout](#repository-structure) · [Design decisions](#key-design-decisions) ·
[Run it](#quick-start) · [API](#api) · [Example conversations](#example-conversations) ·
[Security](#identity-and-authorization) · [Evaluation](#evaluation-results) ·
[Limitations](#known-limitations) · [Deploy](#deployment) · [AI usage](#ai-usage-note)

---

## The problem

Trendly is a D2C fashion retailer. Its support inbox is dominated by the same handful of questions —
*where is my order, can I return this, why was I charged shipping, my parcel never arrived* — and
each one needs two different things to be answered correctly: the customer's actual order record,
and the right clause of the returns policy. Get either wrong and the cost is real. Promising a
refund on a final-sale item, or quoting a 30-day window to someone whose order was cancelled, is
worse than not answering at all.

This is a support agent for those flows. The hard requirement it is built around is that **the model
never decides anything transactional**: it reasons and chooses tools, while ownership, eligibility,
and every state change are settled in code beneath it.

## What the agent does

| Capability | How |
| --- | --- |
| **Order lookup** | Status, tracking, items, and dates — scoped to the caller's own orders. |
| **Policy Q&A** | Hybrid retrieval over the supplied policy, answered with section citations. |
| **Return eligibility** | Deterministic rules: window, category, condition, final sale, cancellation. |
| **Exchange eligibility** | Size/colour scope, window, one-exchange-per-item limit. |
| **Actions** | Create a return or exchange, or issue a delay credit — only after explicit confirmation. |
| **Human escalation** | Lost parcels, uncovered questions, and anything it cannot safely resolve. |
| **Safety / refusal** | Refuses card and bank details before the model sees them; refuses other customers' orders; refuses to invent policy. |

It is a single LangGraph agent with twelve tools. Policy answers come from a Chroma index over
`trendly_policy.md`, cited by section. Eligibility is computed by pure functions over the order
record. Actions are gated server-side on ownership, a passing eligibility check, and an explicit
customer confirmation — so a hallucinated tool call is refused rather than executed.

## Architecture

```
                              POST /chat
                                  |
                        sensitive-content gate         card/bank input refused pre-model
                                  |
                         LangGraph agent loop
              START -> agent -> tools -> agent -> guard -> END
                          |                         |
                    LLM reasoning            sends a turn back for
                    + tool calling           grounding; never rewrites it
                                  |
        +-------------------------+--------------------------+
        |                         |                          |
   order tools              policy retrieval            action tools
        |                         |                          |
  orders.json             Chroma + lexical            return / exchange
  (ownership-scoped)      (hybrid, cited)             credit / escalation
                                  |                          |
                          policy evidence         deterministic services
                                                   eligibility + authorization
                                                              |
                                                       action ledger
```

The division that matters: **retrieval supplies evidence, deterministic services make decisions.**
Full component notes and the policy-rule-to-data mapping are in [docs/architecture.md](docs/architecture.md).

## Repository structure

```
app/
  main.py          FastAPI app: routes, request validation, identity resolution
  config.py        settings, read once from the environment
  agent/           orchestration and model behaviour
    orchestrator.py  the LangGraph graph, provider calls, retries, post-turn refusals
    prompts.py       system prompt, tool-facing nudges, refusal copy
    state.py         session store, reply guards, sensitive-content gate
    fallback.py      deterministic router used when no provider is available
  tools/           the twelve tools exposed to the agent
    context.py       per-turn context; identity lives here, never in a tool argument
    orders.py returns.py exchanges.py refunds.py policy.py escalation.py
  services/        deterministic business and security logic
    authorization.py identity.py eligibility.py action_service.py clock.py
    order_repository.py policy_service.py
  retrieval/       policy RAG
    ingest.py        clause-level chunking of the policy document
    retriever.py     hybrid semantic + lexical search, and the abstention signal
    vectorstore.py embeddings.py
  models/          typed state and tool results
    conversation.py api.py order.py tool_results.py
data/              the supplied assignment dataset, unmodified
tests/
  unit/            isolated components: rules, retrieval, authorization, clock
  integration/     the real graph and ToolNode, driven by a scripted model
  eval/            scenario matrix, trajectory checks, fault injection
docs/architecture.md   dataset schema, policy-rule mapping, ambiguity decisions
scripts/           live-provider harnesses used to verify before a demo
```

The boundaries are the point: `tools/` is what the model can reach, `services/` is what it cannot
influence, and nothing in `agent/` is trusted to enforce a rule.

## Key design decisions

**LangGraph, not a hand-rolled loop.** Tool calling needs a real state machine once guards, a step
ceiling, and a retry path exist. LangGraph makes the graph explicit and inspectable — `agent → tools
→ agent → guard` is the actual runtime, not a diagram of one.

**Hybrid retrieval, not pure vectors.** Policy questions turn on exact terms — "final sale", "cash
on delivery", "48 hours" — and on clause numbers. A nearest-neighbour miss there produces a fluent
answer grounded in the wrong rule. Lexical matching anchors those; semantic covers the paraphrases.

**Deterministic eligibility.** Asking the model to apply window, category, and final-sale rules gave
inconsistent verdicts on exactly the orders that matter. Those rules are pure functions now; the
model chooses which check to run and explains the result.

**Authorization below the LLM.** No data-access tool takes a `customer_id` — it comes from
server-side context the model cannot address. Prompt injection therefore cannot reach another
customer's order, because there is no parameter to inject into.

**Explicit confirmation.** Every state change is proposed, then confirmed by the customer, then
executed. The proposal is bound to `(customer, kind, order, item)`, so a mid-turn change of subject
cannot transfer a confirmation onto a different item.

**Idempotency.** An action is keyed identically and lock-guarded, so an impatient customer, a retry
after a timeout, and two concurrent requests all produce one record.

**Bounded loops.** A step ceiling is checked before any further tool work; the model is never
trusted to stop itself. Reaching it escalates rather than inventing a result.

**Session state, no long-term memory.** Recent turns, the active order, and any pending proposal
persist per session. Nothing is remembered across sessions — that would need a retention policy and
a deletion path, and this agent needs none of it.

**Human escalation as a real action.** Escalation is a tool call that produces a case reference, not
a sentence. Replies are checked against the references the tools actually issued, so the agent
cannot tell a customer a colleague will follow up when no ticket exists.

## Prerequisites

Python 3.11 or newer. Verified on **3.13.3**, which is the version pinned in `.python-version` and
`render.yaml`. No paid services; the embedding model runs locally on CPU.

## Quick start

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

**Windows (PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

Then open **<http://127.0.0.1:8000/agent>**.

`uvicorn app.main:app --port 8000` is the only command needed to run it. On first boot the service
builds the policy index itself — about 3 seconds, plus a one-time ~80MB download of the local
embedding model — so a fresh clone needs no separate setup step. It starts and answers **without an
API key**, using the deterministic fallback; add a key to enable the agent loop.

| URL | What it is |
| --- | --- |
| `/agent` | Browser chat UI. Each reply shows the tools used and the policy sections cited. |
| `/` | Overview page |
| `/docs` | OpenAPI explorer |
| `/health` | `{"status":"ok","orders_loaded":10,"policy_sections_indexed":28,...}` |

For a deployment, bind all interfaces and take the platform's port:
`uvicorn app.main:app --host 0.0.0.0 --port $PORT`.

## Configuration

Optional — the defaults work. Copy the template if you want to set a key:

```bash
cp .env.example .env                 # Windows: Copy-Item .env.example .env
```

| Variable | Purpose |
| --- | --- |
| `LLM_API_KEY` | Any OpenAI-compatible provider. A free [Google AI Studio](https://aistudio.google.com/apikey) key works. `GEMINI_API_KEY` is accepted as a fallback. |
| `LLM_BASE_URL`, `LLM_MODEL` | Must match the key. Defaults target Google AI Studio; for any other OpenAI-compatible gateway set both to that gateway's URL and a model it serves. `GET /health` reports the model actually in use, so a mismatch is visible rather than silent. |
| `AGENT_MODE` | `auto` (use the model, fall back on outage), `llm` (fail closed), `deterministic` (never call a provider). |
| `DEMO_AS_OF` | Request clock. See **A note on dates** below. |

Never commit `.env`; it is gitignored.

## Rebuilding the policy index

Only needed if you edit `trendly_policy.md` — startup handles the normal case.

```bash
python -m app.retrieval.ingest           # no-op when the index matches the document
python -m app.retrieval.ingest --force   # rebuild regardless
```

Parses the policy into 28 citable clauses, embeds them locally, and persists to `vectorstore/`.
After the first build it is fully offline and needs no API key.

## Test

```bash
pytest
```

No network, no API key. `tests/unit` covers the dataset, retrieval, eligibility rules, the
authorization gates, and the guardrails. `tests/integration` drives the real LangGraph graph with a
scripted model, so orchestration, the ToolNode, and the guards all genuinely execute.
`tests/eval` runs a scenario matrix plus fault-injection cases for lookup, retrieval, action, and
escalation failure.

## API

### `POST /chat`

```json
{
  "session_id": "demo-001",
  "message": "I want to return TR-4530",
  "customer_id": "C-101",
  "as_of": "2026-07-29"
}
```

`customer_id` is a **trusted demo identity** — see [Identity and authorization](#identity-and-authorization)
below. It is optional on later turns, because the session remembers it. `as_of` is an optional clock
override.

| Response | When |
| --- | --- |
| `403 unknown_customer` | The identity is not in the dataset. The agent is never invoked. |
| `409 session_identity_conflict` | The session is already bound to a different customer. |
| `422` | Malformed body, or an `as_of` that is not `YYYY-MM-DD`. |

```json
{
  "session_id": "demo-001",
  "message": "Your Block-Print Kurta is eligible for return…",
  "status": "completed",
  "actions": [],
  "handoff_summary": null,
  "tool_trace": ["get_order", "check_return_eligibility"],
  "policy_sections": ["2.1", "2.2", "3.2"],
  "mode": "llm",
  "diagnostics": {
    "trace_id": "8ef70949e791",
    "agent_steps": 3,
    "tool_calls": 2,
    "elapsed_ms": 4.99,
    "loop_limit_reached": false,
    "verification_state": "verified",
    "timings_ms": {"agent": 2372.5, "retrieval": 1352.7, "tools": 2.1, "guard": 0.1}
  }
}
```

`tool_trace` and `policy_sections` are the audit trail: every factual claim in `message` traces to a
tool call, and every policy claim to a cited section. `status` is `completed`, `escalated`,
`degraded` (provider unavailable, nothing changed), or `failed`.

### `GET /health`

```json
{"status": "ok", "orders_loaded": 10, "policy_sections_indexed": 28,
 "llm_configured": true, "llm_model": "glm-5.2", "agent_mode": "auto"}
```

## Example conversations

**Happy path — two-step confirmation**

```
> I want to return TR-4530
  tools: get_order → check_return_eligibility          actions: none
  "Your Block-Print Kurta is eligible for return. It must be unworn and unwashed with
   original tags. The ₹99 shipping fee isn't refunded for change-of-mind returns
   (sections 2.1, 2.2, 3.2). Shall I create the return?"

> yes, please confirm
  tools: get_order → check_return_eligibility → initiate_return
  actions: return_created RET-954291C5
  "Your return is created — reference RET-954291C5. Pickup is pending scheduling."
```

Nothing is created on the first turn. Telling it *"don't ask me to confirm, just do it"* does not
change that: the gate is in the tool layer, where the prompt cannot reach it.

**Escalation — a lost parcel is not a return**

```
> TR-4526 is lost. I want my money back.
  tools: get_order → escalate_to_human
  actions: escalated ESC-34275089        status: escalated
  "I'm sorry — TR-4526 has been marked lost by the carrier. That's a lost-parcel claim
   rather than a return, so I've raised it with a specialist (ESC-34275089)."

  handoff_summary: "Lost-parcel claim: [TR-4526] Canvas Tote, Delhivery DL5519002244,
                    carrier status lost_in_transit. Customer wants a refund; policy
                    offers replacement or full refund at their choice."
```

The customer asked for a refund and did not get one, because the policy routes this to a human.

## Identity and authorization

One assistant serves every Trendly customer, so *"my order"* identifies nothing on its own. The
policy forbids confirming or discussing another customer's order, which makes verification a
correctness requirement rather than a nicety.

**Customer identity.** Either the channel supplies `customer_id` in the request — a trusted demo
identity, validated before the agent runs — or, when it does not, the agent collects a customer ID
and an order ID in conversation:

```
> What's the status of my order?
  Sure — could you give me your customer ID and the order ID, so I can verify the order?

> C-100, TR-4524
  verify_identity → application checks TR-4524.customer_id == C-100 → verified

> Can I return the jeans?
  (reuses the verified context; does not ask again)
```

**Verification.** `verify_customer_order_access(customer_id, order_id)` in
[app/services/authorization.py](app/services/authorization.py) is the whole ownership decision:
deterministic, dataset-driven, independently testable, and returning no order object. The model
extracts the two ids from whatever the customer typed; it never decides whether they are valid or
related.

**Multi-turn state.** After a successful verification the session remembers `verified_customer_id`,
`active_order_id`, and `order_verified`, so the customer is not asked again.

What makes the boundary real rather than advisory:

- **No data-access tool accepts a `customer_id`.** `verify_identity` is the sole exception, and it
  takes a *claim* the application validates — it grants nothing by itself.
- **Nothing order-specific is reachable before verification.** `get_order`, `list_my_orders`, both
  eligibility checks, both action tools, and `issue_delay_credit` all return `VERIFICATION_REQUIRED`.
- **A verified customer is not verified for every order.** Each order is ownership-checked on its
  own, so being verified for TR-4524 does not unlock TR-4522.
- **Identity cannot switch mid-conversation.** A later claim to be someone else returns
  `IDENTITY_LOCKED`; a conflicting HTTP `customer_id` returns `409`.
- **Denials are uniform and silent.** An unknown order and someone else's order both return
  `ORDER_NOT_ACCESSIBLE` with no order fields — telling them apart would confirm existence. An
  unrecognised *customer* id is reported as such, because that discloses nothing.
- **Eligibility denies before any rule runs**, so a non-owner cannot learn an item's return window,
  category, final-sale state, or price.
- **A proposal binds customer, action kind, order, and item**, and all four must still match when
  the action executes.

**Verification lifecycle.** A session moves `UNVERIFIED → IDENTIFIERS_COLLECTED → VERIFYING →
VERIFIED`, or to `VERIFICATION_FAILED` and back for a retry. The state is a description, never a
grant: `is_verified` requires the state *and* all three facts it claims, so setting the label by
hand opens nothing. Every response reports the current state under `diagnostics`.

**Production limitation.** `customer_id` here is either a client-supplied field or a value the
customer types into the chat. **Neither is authentication.** In production, identity would come from
an authenticated session or token, and the customer-entered ID would at most be a secondary check;
`resolve_trusted_customer` is the only function that would change.

## Reliability

**Confirmation.** State-changing actions need a proposal from an earlier turn plus an affirmative
now, matching customer, kind, order, and item. Read-only work — status, policy, listing,
eligibility — never stops to ask.

**Idempotency.** An action is identified by `(customer, kind, order, item)`. Re-submitting one
returns the first reference rather than creating a second, whether the repeat comes from an
impatient customer, a retry after a timeout, or two concurrent requests. A retry that produced a
different reference would be indistinguishable from a duplicate refund.

**Clock.** Every time-sensitive rule reads one injectable clock, so no test depends on when it runs.
Policy windows are IST calendar dates while the dataset records UTC, and that conversion happens
once, centrally — `2026-07-26T20:00:00Z` is 27 July to the customer. The 48-hour damage window is
measured in hours; a request carrying only a date is read as start-of-day, so a window stays open as
long as the date allows. **Business days are treated as calendar days** — the policy references
public holidays but supplies no calendar, and inventing one would be worse than a documented
approximation.

**Bounded work.** `AGENT_MAX_STEPS` (default 8) caps agent turns per request, checked before any
further tool work, and is the only loop cap — LangGraph's recursion limit is derived from it so the
two cannot drift apart. Provider retries and guard nudges are separately bounded. Reaching the
ceiling escalates with a handoff instead of inventing a result, and every response reports
`agent_steps`, `tool_calls`, and `loop_limit_reached`.

**References must be real.** Every case, return, or exchange number in a reply is checked against
what the turn's tools actually returned. A model that says "I've passed this to our team — case
ESC-9C2A1F5D" without calling `escalate_to_human` has invented a ticket, and the customer leaves
waiting on nothing. The guard sends it back to call the tool for real; if that fails the turn is
abandoned to the fallback, which escalates properly, so the reference the customer receives always
exists.

**Order-specific answers.** Refund timing comes from the order's own payment method under policy
3.1 — "your refund goes to the original UPI ID and takes 3–5 business days after inspection", not
the whole table. Shipping charges come from the order's total under 1.3. A cash-on-delivery refund
escalates automatically, because only a human may collect bank details, over a secure link.

**Tracing.** Every turn has a trace id, reported in `diagnostics` alongside step count, tool count,
elapsed time, and verification state, and repeated on the log lines for that run. `timings_ms`
attributes the wait to a component — model, retrieval, tools, guard — so a slow turn can be
diagnosed rather than only observed. Steps nest, so each is counted exclusive of the steps inside
it and the parts sum to the whole. In practice the model dominates: a typical grounded turn spends
roughly 2.4s in the provider and 1.4s in first-call retrieval, and under a millisecond in the guards.

**Escalation handoffs** are assembled from what the turn actually did — tools run, verdicts
returned, sections cited — plus the model's statement of what a human must do next. Contact details
and banking content are never included.

**Session isolation.** State is keyed by `session_id` and nothing else. Interleaved and concurrent
sessions are covered by real threaded tests, including ten parallel verifications and simultaneous
confirmations of the same action. Sessions, identity bindings, and the action ledger are in-process
and do not survive a restart.

What makes the boundary real rather than advisory:

- **No tool accepts a `customer_id`.** There is no argument through which the model, or an injection
  steering it, can assert who it is. The trusted identity is closed over per turn.
- **Ownership is checked below the model**, in the repository, for `get_order`, both eligibility
  checks, both action tools, and `issue_delay_credit`.
- **Denials are uniform and silent.** "No such order" and "not your order" return the same
  `ORDER_NOT_ACCESSIBLE` with no order fields — telling them apart would itself disclose existence.
- **Eligibility denies before any rule runs**, so a non-owner cannot learn an item's return window,
  category, final-sale state, or price.
- **A session is bound to one customer.** Switching mid-conversation is a `409`, not a re-scope.
- **A proposal binds customer, action kind, order, and item**, and all four must still match when the
  action executes.

Verified by a 40-combination ownership matrix (10 allowed, 30 denied, zero leaking any order field)
and a security suite that includes a scripted model *cooperating* with an injection — it calls
`get_order` and `initiate_return` on another customer's order, and both are refused below it.

## Evaluation results

Every figure below was measured against this code. Nothing is estimated.

**Test suite** — `590 passed, 0 failed, 0 skipped` in ~9s. Unit 371, integration 149, eval 70.

**Retrieval** — Recall@1 `1.00`, Recall@3 `1.00`, MRR `1.000` over **14** query→section pairs whose
labels were checked against the policy document by hand. The ground-truth set is deliberately small
and honest; I discarded a larger one whose labels I had guessed rather than verified.

**Live authorization matrix** — all **40** customer × order combinations, run against the real
model: 10/10 own-order requests answered, 30/30 foreign-order requests refused, **0 fields leaked**.

**Live abstention** — 5 questions × 4 repetitions. The three the policy genuinely does not cover
escalated 12/12; the two it *does* cover (§2.1 return window, §1.7 address changes) were answered
from the document 8/8 rather than escalated. **0 fabricated case references** across all 20 turns.

**Idempotency** — 7 confirmations of one return produced 1 record; likewise for an exchange.

**Latency** — 1.9–4.4s per turn, dominated by the provider. `diagnostics.timings_ms` attributes each
turn across model, retrieval, tools, and guards; the guards cost under a millisecond.

## Known limitations

Stated plainly, because they matter more than the parts that work:

- **`customer_id` is not authentication.** It is a demo identity asserted by the caller. Anyone
  reaching the endpoint can claim to be any of the four customers. What is guaranteed is that having
  claimed one, they are confined to it and the model cannot move them. Real auth belongs in front.
- **All state is in-process.** Sessions, identity bindings, and the action ledger do not survive a
  restart, so idempotency does not span a deploy. Redis with a TTL is the fix.
- **Actions are simulated.** Nothing reaches a real returns, refund, or carrier system.
- **Business days are approximated as calendar days.** The policy references public holidays but
  supplies no calendar, and inventing one would be worse than a documented approximation.
- **The dataset is a frozen snapshot.** The clock defaults to 2026-07-29 for that reason — see
  *A note on dates*.
- **Inventory is absent from the dataset**, so an exchange is created with availability explicitly
  unconfirmed.
- **Abstention is tuned to over-escalate.** A synonym-phrased question the policy does cover can be
  handed to a human. That costs a handoff; the opposite error invents policy.
- **Prompt-injection hardening is partial.** The tool layer bounds the blast radius, but an
  injection inside an order field would still reach the model.

## A note on dates

`orders.json` is a frozen snapshot whose scenarios are defined relative to a fixed "today". On
**2026-07-29** every annotation in it holds at once: TR-4525 is exactly 14 days past its expected
delivery, and TR-4527 and TR-4528 sit *inside* the 30-day window, so they are refused on category
and final-sale grounds rather than on date. Against a moving clock those three distinct refusal
paths collapse into "outside the return window" within weeks, and the dataset stops testing what it
was built to test.

The service therefore defaults its clock to that date via `DEMO_AS_OF`, overridable per request with
`as_of` and disabled entirely by clearing the variable. It is a demo affordance; a real deployment
reads the service date.

## Deployment

`render.yaml` is a Render blueprint using the native Python runtime — no container image is
involved. Push the repo to GitHub, then Render → **New Blueprint Instance** → pick the repo. The
build installs `requirements.txt` and runs the policy ingest, so the index and the embedding model
are already warm before the first request; the service starts with

```
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Set `LLM_API_KEY` in the Render dashboard. It is marked `sync: false` in the blueprint, so it is
never committed. Python is pinned to 3.13.3 in both `render.yaml` and `.python-version`, matching
the version the test suite was verified against. `/health` is the health-check path.

Any host that runs a Python process works the same way; nothing is Render-specific beyond the
blueprint file.

## AI-usage note

I used AI assistance (Claude) heavily throughout. Roughly speaking: **most of the code was
AI-generated in first draft** — the FastAPI scaffolding, the LangGraph graph wiring, the Chroma
ingestion and retrieval code, the HTML pages, and a large share of the test suite — and **all of it
was read, run, and revised by me.** The documentation was drafted with assistance and edited by
hand.

What I own are the decisions. The architecture is mine: putting every invariant in the tool layer
rather than the prompt, keeping eligibility as pure functions instead of model reasoning, splitting
retrieval-as-evidence from services-as-decisions, and the two-step confirmation protocol. Several
things came out of testing rather than a first draft and I would call them the real work: the
confirmation gate had to move server-side after the model chained straight to creation under
pressure; a mid-turn eligibility check was able to redirect an existing confirmation onto a
different item until a unit test caught it; and the agent promised a store credit it had no tool to
issue, which is why `issue_delay_credit` exists.

Where generated code conflicted with those decisions I rewrote it. The result is small enough that I
can explain and modify any part of it live.

## Further reading

- [docs/architecture.md](docs/architecture.md) — dataset schema, policy-rule mapping, ambiguities
- [PROMPTS.md](PROMPTS.md) — prompts, tool descriptions, and the iteration record
- [SOLUTION.md](SOLUTION.md) — architecture, trade-offs, limitations, discovery questions
- [DEMO.md](DEMO.md) — video runbook
