# Solution note — Trendly support agent

## Architecture

The service is a small FastAPI application with one conversational endpoint. The caller supplies a channel session ID and the authenticated customer ID; in production the latter would come from a signed session/JWT rather than the request body. The service stores concise recent conversation state plus any pending confirmed action, keyed by session ID.

The orchestration layer has two modes. In normal LLM mode it executes a bounded ReAct loop using the OpenAI-compatible tool-calling interface (tested with Gemini’s free tier configuration). The LLM sees a system prompt, the last six conversational turns, and function schemas. It can request tools, receives structured JSON results, and then composes the customer reply. The tool loop is capped at six rounds, which prevents runaway retries. `AGENT_MODE=llm` fails closed on provider failure; `auto` gives reviewers an offline deterministic mode that exercises the same domain tools and guardrails at no cost.

Tools are deliberately narrow:

| Need | Tool and control |
| --- | --- |
| Order status | `lookup_order` enforces the authenticated customer boundary server-side. |
| Policy question | `get_policy` returns only an excerpt of the supplied policy markdown. |
| Return/exchange decision | Deterministic rule tools combine delivered date, item attributes, and policy rules. |
| Mutation | Creation tools require a prior successful eligibility check and the conversation requires explicit confirmation. |
| Exceptions | `escalate_to_human` creates an auditable case reference and concise factual handoff. |

## Key trade-offs

I chose deterministic eligibility logic rather than asking an LLM to calculate dates or interpret exclusions. It makes final-sale, non-returnable category, cancelled order, damaged-item, and 30-day decisions reliable and auditable. The model retains value where language understanding and multi-turn tool selection matter; it cannot authorise an exception.

An in-memory session/action store keeps the take-home compact. It should be replaced with Redis plus a durable case/returns service in a real deployment. Likewise, requested-size availability is not present in the supplied fixture, so the exchange tool explicitly marks it as pending confirmation rather than fabricating inventory.

The supplied fixed records use dates around the assessment. An optional `as_of` clock makes tests stable. Production omits it and uses the service date.

## Guardrails and recovery

- No customer can see another customer’s order: every lookup is customer-scoped, and a session cannot switch identity.
- Policy answers are grounded only in `trendly_policy.md`; unknown questions produce a human handoff.
- Bank/card/CVV-like content is refused before the LLM sees it. COD refund details are directed to a human secure-link process.
- Discount/coupon/waiver requests are refused and escalated, except the existing delayed-order status response which states only the policy-defined ₹250 credit.
- Lost parcels always become a human lost-parcel claim, never a return.
- Tool, provider, and loop failure do not produce invented answers or partial mutations.

## Known limitations

This is not a production identity system; `customer_id` is an upstream-authentication boundary assumed by the API. Creation references are simulated, inventory availability is intentionally unavailable, and the offline interpreter is a review/test fallback rather than a substitute for an LLM. It uses in-memory state, has no rate limiting or observability backend, and does not yet ingest live carrier-webhook events or support attachments for damaged-item photos.

## Five discovery questions for Trendly operations

1. Which identity signals are available in every channel, and what re-authentication is required before exposing order data or initiating a return?
2. What is the canonical order/returns system of record, and which APIs are idempotent enough for pickup, replacement, refund, and exchange mutations?
3. How do carrier feeds encode “no movement for 10 consecutive days,” partial shipment events, and delivery confirmation, including business-day calendars?
4. What inventory/reservation API can verify a requested exchange size before the customer is promised one, and can an exchange be held while the return is in transit?
5. What are the escalation SLAs, queues, support hours, required handoff fields, and authority boundaries for damaged items, address changes, delay credits, and exception approvals?
