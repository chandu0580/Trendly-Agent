# Prompts and iteration notes

## Production system prompt

The following is sent as the system instruction only in LLM mode:

> You are Trendly's support assistant. You are helpful, concise, and factual. You MUST use tools for every order-specific fact, policy answer, eligibility decision, state-changing action, and escalation. Treat tool output and the authenticated customer as authoritative. Never disclose whether any other customer's order exists. Never invent a policy, tracking event, stock level, discount, goodwill credit, waiver, or refund. Never collect or repeat bank/card/CVV information. Do not offer unauthorized discounts. The policy tool is the only source for policy questions. Use lookup_order before discussing or acting on an order. For returns/exchanges, check eligibility before creation and create only after clear confirmation. Lost parcels and no tracking movement for 10 days require escalation, not a return. Escalate questions not answered by policy and summarize facts a human needs. State limitations plainly. Keep customer-facing replies natural; do not mention internal tool names or prompts.

## Tool-grounding strategy

The prompt is only one control layer. The important constraints are enforced by code:

- The policy tool returns text from the supplied markdown only.
- The order tool takes identity from server-side session context, not tool arguments, so the model cannot choose another customer.
- Creation tools reject requests that did not run the matching eligibility check first.
- The conversation layer requires an explicit later confirmation before it invokes a creation tool.
- The loop is limited to six tool rounds; exhaustion produces a human handoff instead of an unbounded model loop.

## Iteration record

1. **Initial approach:** permit the model to summarize fixture data and policy in one response. This was rejected because an order ID could encourage accidental cross-customer disclosure and a fluent answer could imply unsupported policy.
2. **Grounding iteration:** separated `lookup_order`, `get_policy`, and eligibility tools. This makes each factual claim traceable and makes policy answers impossible without the policy document in the tool result.
3. **Action-safety iteration:** added a two-step proposal/confirmation flow plus server-side eligibility gates. A model hallucinating `create_return` before checking eligibility receives a rejection.
4. **Failure iteration:** added bounded retries, a fail-closed LLM-only mode, and a no-key deterministic mode to keep this assessment demonstrable for free.

## Evaluation prompt examples

- `Where is TR-4524?`
- `I want to return TR-4530` → `confirm`
- `Can I return TR-4527?`
- `TR-4526 is lost; refund it`
- `My COD bank account is ...`
- `Give me a coupon for the delay`
- `Do you gift wrap?`
