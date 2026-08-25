# Solution note — Trendly support agent

## Architecture

A FastAPI service with one conversational endpoint, orchestrated by a single LangGraph agent.

```
START -> agent -> tools -> agent -> guard -> END
```

`agent` reasons and selects tools. `tools` executes them under the authorization layer. `guard`
inspects the drafted reply and either sends it back for another grounded round or lets it through —
it can never rewrite an answer. The first round of every turn is a forced tool call, so no
customer-facing claim is ever made from memory alone.

Underneath sit layers the model cannot reach past:

| Concern | Component | Control |
| --- | --- | --- |
| Identity | `resolve_trusted_customer`, in the API layer | Validated and bound to the session **before the agent runs**; unknown → 403, conflict → 409. |
| Order facts | `get_order`, `list_my_orders` | Identity is runtime context; **no tool accepts a `customer_id`**. "Not found" and "not yours" return the same result with no order fields. |
| Policy | `search_policy` over a Chroma index | 28 clause-level chunks carrying section numbers; hybrid semantic + lexical; answers cite sections. |
| Decisions | `check_*_eligibility` | Pure functions over the order record. The model never computes a verdict. |
| Order-specific policy | `get_refund_timing`, `quote_shipping_fee` | Joins the order's own payment method or total to a clause. A COD refund escalates *inside* the tool under 3.3. |
| Mutations | `initiate_*`, `issue_delay_credit` | Require a passing check, a proposal from an earlier turn, and a matching affirmative now. |
| Exceptions | `escalate_to_human` | Case reference plus a handoff a person can act on. |

**Retrieval supplies evidence; deterministic services make decisions.** That split is the core of
the design — RAG is never in the transactional path.

**State and memory.** Three things persist per session: recent messages, the active order, and at
most one outstanding proposal. Every fact is re-fetched per turn rather than remembered, so the
agent cannot drift from the order record as a conversation lengthens. There is deliberately **no
long-term customer memory**: what outlives a conversation already has a home — policy in the index,
orders in the repository — and persisting more would add a retention policy, a deletion path, and a
privacy surface to serve no requirement, while introducing the exact failure this design avoids, an
agent acting on a remembered fact rather than a checked one.

**Actions and idempotency.** A state change needs a proposal plus an affirmative matching customer,
kind, order, and item; read-only work never asks. An action's identity is
`(customer, kind, order, item)`, so a repeat returns the first reference instead of creating a
second — checked before the grant and confirmation gates, since a replay already passed them, and
lock-guarded so two concurrent requests cannot both create. Escalations are deliberately *not*
idempotent: two genuine handoffs are two tickets.

**Time.** One injectable clock feeds every dated rule, so no verdict depends on when the suite runs.
Policy windows are IST calendar dates while the dataset stores UTC — `2026-07-26T20:00:00Z` is the
27th to the customer — converted once rather than at each call site. A date-only request is read as
start-of-day so a window never expires early against the customer.

**Bounded work and recovery.** A hard step cap is checked before any further tool work; retries and
guard nudges are separately bounded, and LangGraph's recursion limit is derived from the cap so the
two cannot drift. Recovery is layered: transient 429/5xx retry with backoff while a malformed
request does not; a gateway rejecting forced tool choice degrades to `auto`; a refused tool returns
an instruction the model acts on in the same loop; the step ceiling escalates; a turn abandoned
mid-flight discards provisional escalations so the fallback cannot raise a duplicate, while a
mutation already written is still reported. Only when all of that is exhausted does a deterministic
fallback answer — it calls the same tools, so it is worse at language and identical in safety.

**Observability.** Every turn carries a trace id through the context, the logs, and the response,
alongside step count, tool count, per-component latency, and verification state. One run is
reconstructable from the logs. A handful of fields, deliberately, not a platform.

## The security boundary

One assistant serves every customer, so *"my order"* identifies nothing, and the policy forbids
discussing another customer's order. That makes verification a correctness requirement, and the line
is drawn so the model has no say in it:

> **The model decides** what the customer wants and which tool to reach for, and extracts
> identifiers from whatever phrasing they used. **The application decides** whether those
> identifiers are valid, whether the order belongs to that customer, and whether an action may run.

Identity arrives two ways. A channel-supplied `customer_id` is validated and bound before the agent
runs. Without one the session starts *unverified* — deliberately, with no default identity — and the
agent asks for a customer ID and order ID, then submits them through `verify_identity`. Either way
the outcome becomes runtime context no tool parameter can override.

Verification is **per-order, not per-session**: being verified for TR-4524 does not unlock TR-4522,
and ownership is re-checked at execution time, not merely when eligibility was granted. Denials are
uniform — "no such order" and "not your order" return the same result — because distinguishing them
would itself disclose that the order exists. Eligibility denies before any business rule runs, so a
non-owner cannot infer an item's window, category, or price.

**This is not authentication.** Anyone reaching the endpoint can claim to be any of the four
customers. What the design guarantees is that having claimed one, they are confined to it, the model
cannot move them, and every order is checked individually. In production identity would come from an
authenticated session; `resolve_trusted_customer` is the only function that would change.

## Key trade-offs

**One agent with many tools, not many agents.** Every request is one customer, one order, one policy
question. A planner/critic split adds hops and failure modes without changing a single answer. The
complexity that *is* justified went into the authorization layer instead.

**Function calling, not MCP.** MCP earns its keep when tools belong to other teams or processes.
These are internal functions over local data; a protocol boundary would add operational surface with
no integration benefit.

**Eligibility in code, not in the model.** The cost is that a genuinely novel policy situation
cannot be reasoned about at all — it escalates. For an agent that decides refunds, I take that trade
every time.

**Hybrid retrieval, not a reranker.** Policy questions turn on exact terms ("final sale", "cash on
delivery", "48 hours") and clause numbers, where a nearest-neighbour miss produces a fluent answer
grounded in the wrong rule. Lexical matching anchors those; semantic covers the paraphrases.
Section-aware chunking does more for accuracy here than a learned reranker would. Chroma with local
ONNX embeddings runs on CPU with no API key and no torch.

**Similarity is not coverage.** "Does Trendly ship to Antarctica?" scores highly against the
shipping section *because* it is about shipping, and no threshold fixes that — the passage is
genuinely the nearest text, it simply does not answer the question. So retrieval also reports which
content words appear nowhere in the policy; when it finds any, the result is marked not-covered and
carries instructions to say so and escalate. Derived from the indexed vocabulary, so it generalises
to questions nobody anticipated. Biased toward caution: a covered question can be flagged, costing
an unnecessary handoff, whereas the opposite error invents a shipping policy for a continent Trendly
does not serve.

**Out of scope is not an escalation.** A question the policy omits ("do you offer gift wrapping?")
needs a human. One nobody at Trendly should answer ("what is Python?") needs a polite decline and no
ticket. Conflating them floods a real support queue.

**An anchored clock for a frozen fixture.** `orders.json` is a snapshot whose scenarios are defined
relative to an implied "today". On 2026-07-29 every annotation holds at once; against a moving clock
three distinct refusal paths collapse into "outside the return window" within weeks and the dataset
stops testing what it was built to test. Overridable per request, disabled by clearing `DEMO_AS_OF`.

## Known limitations

- **`customer_id` is not authentication** — a trusted demo identity asserted by the caller. Real auth
  belongs in front of this.
- **All state is in-process.** Sessions, identity bindings, and the action ledger do not survive a
  restart, so idempotency does not span a deploy and a session id can be re-bound. Redis with a TTL
  is the fix.
- **Actions are simulated.** Nothing reaches a real returns, refund, or carrier system.
- **Inventory is absent from the dataset**, so an exchange is created with availability explicitly
  unconfirmed; policy 4.3's conversion to a refund is explained but not executed.
- **Half the lost-parcel rule is unimplemented** — "no tracking movement for 10 consecutive days"
  needs an event history the fixture does not have.
- **Business days are approximated as calendar days**, because the policy references public holidays
  but supplies no calendar. Affects dispatch timing, delivery estimates, and the delay threshold.
- **Coverage detection is word-level and over-fires.** An inflection can trip it — "delivery to a
  metro *city*" flags `city` because the document says "cities" — so a covered question can be
  handed to a human it did not need.
- **The deterministic helpers assume this dataset's id format.** Business logic is fully
  data-driven — swapping in a different retailer's orders with `CUST-88213` / `ORD-2026-0001` ids
  works untouched, ownership and eligibility included. But `ORDER_ID_RE` matches `TR-0000`
  literally, so on a different format the *offline* fallback cannot carry an order across turns and
  a bare id with no topic word would not force a grounding call. The model-driven path is
  unaffected, since the model extracts identifiers itself. One regex, and the format belongs in
  config rather than in code.
- **Contact details are a demo fixture.** `data/customer_profiles.json` is invented; the supplied
  dataset has no name, email, or phone. In production this comes from the CRM, and the order source
  is a path constant rather than a configurable connection.
- **The reply guards key off text patterns.** They can only force a tool call or defer to the
  fallback, never decide an answer, but an unusually phrased request could slip past them. Scope
  discipline is likewise a prompt-level rule, not a hard guarantee like authorization.
- **Prompt-injection hardening is partial.** Content gates catch card and bank patterns before the
  provider and the tool layer bounds the blast radius, but an injection inside an order field would
  reach the model.
- **No rate limiting or observability backend.** Structured logs, `tool_trace`, and
  `policy_sections` are the beginnings of the latter.

## Five discovery questions for Trendly operations

1. **What is the system of record for order, shipment, refund, and return state, and which of its
   mutations are idempotent?** Pickup scheduling, replacements, refunds, and exchanges all need
   retry-safe semantics before this can create anything real.
2. **Which identity signals exist on every channel, and what re-authentication do you require before
   exposing order data or initiating a return?** The answer decides whether an asserted id stays a
   header or becomes a step-up flow.
3. **Which actions may the agent complete alone, and which need human approval?** I have drawn that
   line at "policy-defined and reversible" — the ₹250 delay credit is automated, everything else
   escalates — but that is an operations decision, not an engineering one.
4. **How are business calendars, public holidays, serviceable pincodes, and city tiers maintained,
   and what API exposes them?** Three shipping rules and the delay threshold are approximations
   today because none of that is in the data.
5. **What are the SLA, escalation queue, audit, and observability requirements?** I would rather
   match the handoff payload to what an agent's console actually needs than guess at it.
