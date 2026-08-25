# Architecture and requirements mapping

## 1. Dataset schema (observed, not assumed)

Read directly from `data/orders.json`. No field below is invented.

**Order:** `order_id`, `customer_id`, `status`, `placed_at`, `delivered_at`, `expected_delivery`,
`cancelled_at`, `carrier`, `tracking_number`, `payment_method`, `shipping_city`, `refund_status`,
`total`, `items[]`

**Item:** `sku`, `name`, `category`, `size`, `qty`, `price`, `final_sale`, `shipped`, `backorder_eta`

**Customer:** `customer_id`, `name`, `email`, `phone`

| Enum | Observed values |
| --- | --- |
| `status` | `in_transit`, `delivered`, `partially_shipped`, `delayed`, `lost_in_transit`, `cancelled` |
| `category` | `apparel`, `accessories`, `footwear`, `innerwear`, `jewellery` |
| `payment_method` | `credit_card`, `prepaid_card`, `upi`, `cash_on_delivery` |

Fixture records also carry `_note_for_designers`, an authoring annotation. `_`-prefixed keys are
stripped in the repository layer so they can never reach the model or the customer.

`item_id` in the tool contracts maps to the dataset's `sku`. There is no separate item identifier.

## 2. Policy rule → data mapping

| Policy | Rule | Data used | Status |
| --- | --- | --- | --- |
| 1.1 | Dispatch same/next business day | `placed_at` | **Not implementable** — no holiday calendar, no dispatch timestamp. Answered from policy text only. |
| 1.2 | Delivery estimates by city tier | `shipping_city` | **Partial** — no metro/non-metro/remote classification field. Answered from policy text; never asserted per-order. |
| 1.3 | Free shipping ≥ ₹1,499, else ₹99; express ₹199, not for COD | `total`, `payment_method` | Implementable |
| 1.4 | Partial shipment ships free | `status`, `items[].shipped`, `backorder_eta` | Implementable |
| 1.5 | >3 business days late → ₹250 credit **on request** | `status`, `expected_delivery` | Implementable (calendar days; see §4) |
| 1.6 | Carrier-lost **or** 10 days no movement → human claim | `status` | **Partial** — `lost_in_transit` detected; no tracking event history exists for the 10-day clause |
| 1.7 | Address change only before dispatch | `status` | Implementable |
| 2.1 | 30 calendar days from **delivery** | `delivered_at` | Implementable |
| 2.2 | Unworn/unwashed/tags | — | **Not verifiable** — surfaced as a condition, never asserted |
| 2.3 | Non-returnable categories | `items[].category` | Implementable |
| 2.4 | Final sale → size exchange only | `items[].final_sale` | Implementable |
| 2.5 | Footwear needs shoe box, else ₹300 | `items[].category` | Implementable as a stated deduction |
| 2.6 | No return on cancelled orders | `status` | Implementable |
| 3.1 | Refund timings by payment method | `payment_method` | Implementable |
| 3.2 | ₹99 shipping refunded only for Trendly error | `reason` | Implementable |
| 3.3 | COD refund bank details → human, secure link | `payment_method` | Implementable (never collected in chat) |
| 3.4 | Partial refund | `items[]` | Implementable |
| 4.1 | Size exchanges only | request | Implementable |
| 4.2 | Same 30-day window | `delivered_at` | Implementable |
| 4.3 | Size unavailable → refund | — | **Not implementable** — no inventory in dataset. Reported as unconfirmed, never fabricated. |
| 4.4 | One exchange per item; second needs a human | — | **No field.** Tracked in a service-local ledger; counts only exchanges raised by this service. |
| 5.1–5.3 | Pickup, self-ship, failed attempts | — | **No pincode serviceability field.** Answered from policy text only. |
| 6.1 | Damage reported within 48h with photos | `delivered_at` | Implementable at date granularity (§4) |
| 6.2 | Damage covers non-returnable categories | `category`, `reason` | Implementable |
| 7 | Prohibited behaviours | — | Enforced in guardrails and the authorization layer |

## 3. Identity and the authorization boundary

The dataset has `customer_id` but no credential of any kind. For this prototype `customer_id` is a
**trusted demo identity context**: the client asserts it, the application validates it, and it
becomes runtime context. It is not authentication and is not presented as such. In production the
identity would be derived from an authenticated session or token and never read from a
client-supplied field; the only part that would change is `resolve_trusted_customer`.

```
client
  ↓  {session_id, message}                       customer_id optional
API  ── resolve_trusted_customer()  ── validates any asserted id          → 403 unknown_customer
     └─ SessionIdentityRegistry     ── binds session to one customer      → 409 session_identity_conflict
  ↓  verified customer, or None
agent ── unverified? ask for customer ID + order ID
      └─ verify_identity(customer_id, order_id)   model extracts, application decides
           └─ verify_customer_order_access()      order.customer_id == customer_id
  ↓  verified_customer_id + active_order_id (session state)
ToolContext (runtime context)
  ↓
tools  ── no data-access tool accepts a customer_id parameter
  ↓
OrderRepository.get_for_customer(order_id, verified_customer_id)
```

There is deliberately **no default identity**. A session with no asserted `customer_id` starts
unverified, and every order tool returns `VERIFICATION_REQUIRED` until `verify_identity` succeeds.
Silently handing an unidentified caller an account would be the whole vulnerability.

Verification is scoped to one order at a time. A customer verified for TR-4524 is not thereby
verified for TR-4522: each order is ownership-checked on its own, and a claim to be a different
customer returns `IDENTITY_LOCKED` rather than re-verifying.

Both rejections happen **before the agent is invoked**, so an unverifiable or conflicting identity
never reaches a tool, a policy lookup, or the model.

**What the model may decide:** which order id to ask about.
**What the application decides:** who is asking, whether they own it, and whether an action may run.

### Verification lifecycle

```
UNVERIFIED ──identifier seen──> IDENTIFIERS_COLLECTED ──both supplied──> VERIFYING
    ^                                    |                                  |
    |                                    v                                  v
    └──────────────────────── VERIFICATION_FAILED <──denied──   ownership check
                                         |                                  |
                                     (retry)                            granted
                                                                            v
                                                                        VERIFIED
```

The state is a description, never a grant. `ConversationState.is_verified` requires the state **and**
all three facts it claims (`verified_customer_id`, `active_order_id`, `order_verified`), and only
`mark_verified` — reached solely from the success path of the deterministic ownership check — can
produce a consistent VERIFIED. Setting the label by hand opens nothing, which
`test_setting_the_label_by_hand_does_not_grant_access` pins.

A failed attempt never revokes an established verification; the customer may simply retry.

### Actions: confirmation, then exactly once

State-changing tools need a proposal from an earlier turn plus an affirmative now, matching customer,
kind, order, and item. Read-only work — status, policy, listing, eligibility — never stops to ask.

An action's identity is `(customer_id, kind, order_id, item_id)`. Re-submitting one returns the first
reference rather than creating a second, whether the repeat is an impatient customer, a model
retrying after a timeout, or two concurrent requests. The check runs *before* the grant and
confirmation gates, because a replay already passed them; a `threading.Lock` makes it safe when two
requests arrive together. Escalations are deliberately **not** idempotent — two genuine handoffs are
two tickets.

### Clock and dates

All time-sensitive rules read one injectable clock (`app/services/clock.py`). Two things it fixes:

- **Timezone.** The policy's windows are IST calendar dates; the dataset records UTC timestamps.
  `2026-07-26T20:00:00Z` is 27 July in IST, so truncating the UTC string put delivery a day early at
  exactly the boundary that matters. Conversion happens once, centrally.
- **Granularity.** Policy 6.1 is 48 *hours*, and is now measured in hours. A request carrying only a
  date is normalised to **start** of day IST — the earliest it could have been — so a window stays
  open as long as the date allows rather than expiring against the customer.

**Business days are calendar days.** The policy says "business day" and "public holiday"; the
supplied document provides no holiday calendar, and inventing one would be worse than a documented
approximation. This affects rules 1.1, 1.2, and 1.5.

### Bounded work

`AGENT_MAX_STEPS` (default 8) caps agent turns per request, checked before any further tool work —
the model is never trusted to stop itself. It is the *only* loop cap: LangGraph's own recursion
limit is derived from it (`steps × 2 + 4`, covering an agent and a tool node per step plus the
guard and limit nodes) rather than configured separately, so raising the ceiling cannot leave the
backstop behind it and turn a clean escalation into a raw `GraphRecursionError`. Provider retries
and guard nudges are separately bounded. Hitting the ceiling escalates with a handoff rather than
inventing a result. Every response reports `diagnostics.agent_steps`, `tool_calls`,
`loop_limit_reached`, and `timings_ms` — per-component wall time, counted exclusive of nested
steps so the breakdown sums to the turn rather than exceeding it.

### Escalation summaries

Assembled by the application from what the turn actually did — the tools it ran, the verdicts they
returned, the policy sections cited — rather than narrated by the model. The model contributes the
customer's problem and `required_human_action`. Contact details and banking content are never
carried; the colleague looks the customer up by id.

### References must be real

Every case, return, exchange, and credit number the reply quotes is checked against the references
the turn's tools actually returned. A model that writes "I've passed this to our team — case
ESC-9C2A1F5D" without calling `escalate_to_human` has invented a ticket: the customer leaves
believing a colleague will follow up, nothing exists to follow up, and the reference matches nothing
when they quote it next time. Unlike a wrong fact, that cannot be corrected in the same
conversation.

Found live during release verification, on a question the policy does not cover — the model
abstained correctly and then fabricated the handoff it described. The guard nudges the model to call
the tool for real; if the nudge budget runs out the turn is abandoned to the deterministic fallback,
which escalates properly, so the customer still leaves with a reference that exists. The check
compares against `ctx.actions` rather than a list of prefixes, so it keeps working as action types
are added.

### Session isolation

State is keyed by `session_id` and nothing else; tool contexts are rebuilt per turn and closed over
one verified identity. Interleaved and concurrent sessions are covered by
`tests/integration/test_concurrency.py`, including ten parallel verifications and simultaneous
confirmations of the same action.

**In-process limitation:** sessions, identity bindings, and the action ledger live in memory and do
not survive a restart. Within a running process they are isolated and thread-safe; across a restart
a session id can be re-bound and an action reference is forgotten.

Because no tool takes a `customer_id`, there is no argument through which the model — or a prompt
injection steering it — can assert an identity. `tests/integration/test_security.py` includes the
decisive case: a scripted model that *fully cooperates* with an injection, calling `get_order` and
then `initiate_return` on another customer's order. Both are refused below it.

**Denials are uniform and silent.** "No such order" and "not your order" return the same
`ORDER_NOT_ACCESSIBLE` result with no order fields at all — distinguishing them would itself
disclose that the order exists. This applies to `get_order`, both eligibility checks, both action
tools, and `issue_delay_credit`. Eligibility denies **before** any business rule runs, so a
non-owner cannot learn an item's window, category, final-sale state, or price.

**Actions bind four things.** A pending proposal records `customer_id`, `kind`, `order_id`, and
`item_id`, and all four must still match at execution. A proposal cannot be spent by a different
customer, on a different item, or as a different action.

**Session binding.** A session is bound to exactly one customer for the life of the process. A later
request naming a different customer is rejected with 409 rather than silently re-scoping an
established conversation. Omitting `customer_id` on a later turn reuses the binding rather than
falling back to the demo default.

## 4. Ambiguities and the decisions taken

**The fixture has an implied "today" of 2026-07-29.** Its scenarios are defined relative to a
fixed date: on 2026-07-29 TR-4525 is exactly 14 days past its expected delivery (matching its
authoring note verbatim), and TR-4527 and TR-4528 fall *inside* the 30-day window so they are
refused on category and final-sale grounds rather than on date. Evaluated against a moving
clock these three distinct refusal paths collapse into "outside the return window" within weeks
— by late August 2026 nearly every order is simply expired. The service therefore anchors its
clock to that date via `DEMO_AS_OF`, overridable per request and disabled by clearing the
variable. A real deployment reads the service date.

**Business days are approximated with calendar days.** No holiday calendar exists in the data,
and inventing one would be worse than approximating. Affects rules 1.1, 1.2 and 1.5.

**The 48-hour damage window is evaluated in whole days**, because the request clock has date
granularity. Boundaries resolve in the customer's favour.

**`prior_exchanges` has no field**, so policy 4.4 is enforced against a service-local ledger.
It cannot see exchanges raised by any other system.

## 5. Component responsibilities

```
FastAPI /chat
      |
LangGraph agent           reasoning and tool selection only
      |
   tools/                 the only capabilities the model has
      |
   +--+------------------+--------------------+
   |                     |                    |
orders/                policy/              actions/
repository            retrieval             services
   |                     |                    |
orders.json        Chroma + lexical      eligibility (pure rules)
                   over policy.md        authorization (gates)
                                         action ledger
```

The division that matters: **retrieval supplies evidence, deterministic services make decisions.**
The model chooses which tool to call and writes the reply; it never computes an eligibility
verdict, and it cannot reach past the authorization layer to mutate anything.
