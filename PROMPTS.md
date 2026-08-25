# Prompts and iteration notes

## Where the prompt sits in the design

The system prompt is the **weakest** layer, and is written on that assumption. It contains no
policy text at all — the rules live in `trendly_policy.md` and reach the model only through
`search_policy`, so the agent cannot answer from a stale copy baked into its instructions. What the
prompt carries is behaviour: when to reach for a tool, how to treat a verdict, and what to do when
it cannot answer.

Everything that must not fail is enforced below the model, in `app/services/authorization.py` and
the tool wrappers:

| Invariant | Where it is enforced |
| --- | --- |
| Nothing order-specific before verification | Every order tool returns `VERIFICATION_REQUIRED` until `verify_identity` succeeds |
| A customer sees only their own orders | Identity is session context; **no data-access tool takes a `customer_id`** |
| A claimed identity is not an authorised one | `verify_identity` validates the claim against the dataset; it grants nothing itself |
| No mutation without a passing eligibility check | `initiate_*` rejects any order/item pair without a grant |
| No mutation without explicit confirmation | `initiate_*` needs a proposal from an earlier turn *and* an affirmative now |
| A confirmation cannot be transferred | The proposal is fixed at the start of the turn and must match kind, order, and item |
| Policy claims come only from the document | `search_policy` reads the Chroma index over `trendly_policy.md` |
| Bounded work per turn | Hard step cap checked before further tool work; exhaustion escalates |
| Identifiers come from the customer | `verify_identity` refuses values absent from what they typed |
| An action happens once | Ledger keyed on customer+kind+order+item, under a lock |
| Refund timing matches the order | `get_refund_timing` reads the order's own payment method |
| A COD refund reaches a human | `get_refund_timing` raises the handoff itself, under policy 3.3 |
| Card/bank content never reaches the provider | Regex gate runs before the graph is entered |

A model that ignores every line below still cannot leak another customer's order, invent policy, or
create an unconfirmed return.

## Production system prompt

From [`app/agent/prompts.py`](app/agent/prompts.py):

> You are Trendly's customer support assistant. You are warm, concise, and factual.
>
> **## Verifying who you are speaking to**
> One assistant serves every Trendly customer, so "my order" identifies nothing on its own. Before any order-specific help you need a verified customer ID and order ID.
> - If neither is verified, ask for both: "Sure — could you give me your customer ID and the order ID so I can verify the order?"
> - If only one is missing, ask only for that one.
> - The moment the customer supplies both, in any phrasing, call `verify_identity` with exactly what they gave you. Never invent, correct, complete, or guess either value.
> - `verify_identity` decides whether the order belongs to that customer. You do not. Relay its outcome.
> - Once it succeeds the conversation stays verified: do not ask for those ids again, and use the verified order for follow-ups like "can I return it?".
> - If a customer later claims to be someone else, do not accept it. Say you can only continue with the account this conversation was verified for, and that using another account needs a new conversation.
> - A verified customer is not verified for *every* order. Each order is ownership-checked on its own; if a tool says an order is not accessible, say only that you can help with orders on their own account.
>
> **## Grounding**
> You have no knowledge of Trendly's policy or of any order beyond what tools return to you in this conversation. If you have not called a tool for a fact, you do not know it.
> - Call `get_order` before discussing or acting on any order.
> - Call `search_policy` before making any policy claim, and cite the section numbers it returns (for example, "under section 2.1"). Never answer a policy question from general knowledge, and never invent a section number.
> - If the retrieved passages do not address what the customer asked, the policy is silent on it. Say so plainly and escalate in the same turn.
>
> **## Decisions are not yours to make**
> `check_return_eligibility` and `check_exchange_eligibility` decide whether something can be returned or exchanged. Reading the order record is never enough to answer that yourself — call the tool and relay its verdict, including its reason.
> Call the check immediately. Never ask the customer why they want to return an item, or which size they want, before checking: those answers do not change the window, category, or final-sale rules, and asking first wastes the customer's turn. If they then tell you the item arrived damaged, defective, or incorrect, check again with that reason so any exception is picked up.
>
> **## Actions are two-step**
> 1. Call the matching `check_*_eligibility` tool, tell the customer the outcome and any conditions, and ask them to confirm. Create nothing on this turn.
> 2. On the later turn where they explicitly agree, call `get_order` and the check tool again to re-validate, then call the matching `initiate_*` tool.
>
> If a tool reports that confirmation is required, the customer has not agreed yet: ask them, and do not claim anything was created.
>
> **## Tool results are authoritative**
> Relay refusals honestly. Never assert an outcome a tool declined to give you, never retry a refused action with different wording, and never describe an action as done unless a tool returned a reference for it. If a tool fails, say what you could not do rather than guessing at the result.
>
> **## Boundaries**
> - A lost parcel is a claim for a human, never a return. Escalate it.
> - Never reveal, confirm, or deny the existence of an order that `get_order` did not return to you. If it is not found, say only that it is not on this account.
> - Never invent a policy, tracking event, stock level, discount, coupon, waiver, goodwill credit, or refund. `issue_delay_credit` is the only compensation you can grant, and only when that tool returns a reference. Every other compensation request is refused and escalated.
> - Never request, accept, or repeat bank account numbers, card numbers, or CVV. Refund banking details are collected by a human over a secure link.
> - Never give medical, legal, or financial advice.
> - Saying "I don't know" is not a complete answer on its own. If you cannot answer from a tool result, call `escalate_to_human` in the same turn and give the customer the case reference.
> - Never describe your own machinery — no tool names, no list of what you can look up, no mention of internal checks. To the customer you either know something or are getting them a person.
>
> **## Style**
> Lead with the direct answer. Use short Markdown bullets for supporting facts, and end with one clear next step. Acknowledge frustration before quoting policy when a customer has been affected by a delay or a loss.

## Tool descriptions are part of the prompt

A rule stated at the point of use is followed far more reliably than the same rule buried in a long
system prompt, so each tool carries its own constraints. For example `initiate_return`:

> Create a return. Requires a prior eligible `check_return_eligibility` AND an explicit customer
> confirmation given on a later turn. Calling it before the customer has agreed is rejected. Never
> describe a return as created unless this tool returns a reference.

and `check_return_eligibility`:

> Decide whether an item can be returned… You must call `get_order` first. Call this immediately —
> never ask the customer why they want to return something before checking… This tool's verdict is
> authoritative: do not judge eligibility yourself from the order data.

## Refusals are prompts too

When a guardrail declines a call, the tool returns an instruction rather than a bare error, so the
model can recover inside the same graph loop instead of apologising to the customer or inventing an
outcome:

```json
{
  "created": false,
  "requires_confirmation": true,
  "message": "The customer has not explicitly confirmed this return.",
  "guidance": "Summarise what will happen, ask them to confirm, and call this tool again only on the turn they agree. Do not claim anything was created."
}
```

`search_policy` carries the same idea, which is what makes grounding hold in practice:

> Answer only from these passages and cite the section numbers you used. If they do not address what
> the customer asked, the policy is silent: say so plainly and call `escalate_to_human`. Never
> describe which topics you can look up.

This turned out to be the highest-leverage prompt surface in the system. A refusal that explains the
protocol converts a would-be hallucination into a correct next action.

## Guards are the last layer

Three guards inspect the drafted reply before it can reach the customer. Neither ever rewrites an
answer — each can only send the turn back for another grounded round, or defer to the fallback. That
asymmetry is what makes them safe to key off imprecise text signals.

- **Unchecked eligibility** — the customer asked about a return or exchange, an order was looked up,
  and no `check_*_eligibility` ran. Sent back with an instruction to get the verdict.
- **Invented reference** — the reply quotes a case, return, or exchange number that no tool
  returned. Sent back to call the tool for real; if the nudge budget runs out the turn is abandoned
  to the fallback rather than shipped, so the reference the customer gets always exists.
- **Dead end** — the reply admits it cannot help but no escalation happened. Sent back to raise the
  handoff.

Eligibility is checked first, deliberately: *"I can't say whether you can return that"* matches the
dead-end pattern, but what it actually needs is the rule engine, not a human.

## Iteration record

Each of these came from an observed failure, most of them under live evaluation rather than in a
first draft.

**1. Policy in the prompt → policy in an index.** The first version pasted the policy into the
system prompt. It was fluent and confidently wrong at the seams, blending the 30-day return window
with the 48-hour damage window, and it would go stale the moment the document changed. The document
now lives in Chroma, split into 28 clause-level chunks that carry their section numbers, and the
model is told to cite what it used. Replies now say "under sections 2.1, 2.2 and 3.2", which is
checkable.

**2. Eligibility by reasoning → eligibility in code.** Asking the model to apply the window,
category, and final-sale rules gave inconsistent verdicts on exactly the orders that matter — it
would refuse the jewellery order on *date* grounds when the date was fine and the category was the
real reason. Defensible-sounding, still wrong. Those rules are now pure functions; the model chooses
which check to run and explains the result.

**3. Confirmation in the prompt → confirmation in the tool layer.** Under pressure — *"just refund
it now, don't ask me to confirm"* — prompt-only confirmation failed and the model chained eligibility
straight into creation. The requirement moved into `initiate_*`, which needs a proposal recorded on
an earlier turn plus an affirmative on this one.

**4. Forced grounding on the first round.** With free tool choice the model would occasionally answer
an order question from conversation history. The first round of every turn is now a forced tool call,
and a factual answer with no tool call behind it is discarded rather than sent.

**5. Required parameters were making it stall.** Asked *"Can I return TR-4527?"*, the agent replied
*"why do you want to return it?"*, because `reason` was mandatory. That question is useless —
jewellery is non-returnable whatever the reason — and it costs a turn. `reason` and `requested_size`
became optional with the strictest defaults, and the tool description now says to check first and
re-check later if damage is reported.

**6. A backstop for eligibility claims.** Forcing a first tool call does not stop a model that calls
`get_order` and then reasons about the window itself. The unchecked-eligibility guard catches that
and sends it back.

**7. It promised something it had no tool for.** Multi-turn testing caught *"if you'd like the ₹250
store credit, just let me know and I can get that started right away."* Policy-correct, but there was
no tool to issue one — a capability hallucination. The fix was to grant the ability rather than teach
an apology: `issue_delay_credit` gives the fixed policy amount, once per order, refused for
delivered, cancelled, and lost parcels.

**8. "I don't know" was half an answer.** Asked about gift wrapping, the agent said the policy did
not cover it and stopped — and explained itself by listing the topics it could look up, leaking
internal structure. The policy requires offering a human as well. Hence the dead-end guard, and the
explicit instruction never to describe its own machinery.

**9. Proposals had no expiry.** When a customer declined, the model re-ran the eligibility check
while composing its reply, quietly re-arming the offer. A later unrelated "yes" would then have had
a live proposal beside it. Offers are now retired on an explicit decline and expire after one quiet
turn.

**10. A confirmation could be transferred between items.** A unit test written for this rewrite
caught something the previous design had hidden: because the eligibility check *overwrote* the
pending proposal, a confirmation given for item A could authorise item B checked later in the same
turn. The proposal is now fixed at the start of the turn, separate from anything proposed during it.

**11. Example ids in a tool description became real input.** The single most instructive failure of
the whole build. `verify_identity`'s description illustrated the phrasings it accepted with
*"such as 'C-100, TR-4524'"* — both valid in the dataset. On a live run the model called the tool
with those exact values on turn one, before the customer had typed anything, and the session
verified as a customer who was not there. Two fixes: every example id in every tool description is
now a value that does not exist (`C-000`, `TR-0000`), pinned by a test that scans the schemas; and
structurally, `verify_identity` now refuses identifiers that do not appear in what the customer
actually said. Extraction is the model's job; supplying values is not.

**12. Retrying blips, not mistakes.** A free-tier gateway returned 503s often enough that roughly one
turn in five fell back. Transient errors now retry with backoff; a 400-class error degrades or fails
immediately rather than burning quota. A gateway that rejects forced tool choice degrades to `auto`
instead of losing the model path.

**13. Abstaining, then fabricating the handoff.** Found during release verification, on *"Do you ship
to Antarctica?"*. The model did the hard part correctly — recognised the policy does not cover it and
declined to invent a shipping rule — then wrote "I've passed your question to our team. Case
reference: ESC-9C2A1F5D" without ever calling `escalate_to_human`. Every visible signal said the turn
went well: it refused to guess, it offered a human, it produced a plausible reference. Nothing
existed behind it. Prompting harder was not the fix, because the failure is invisible in the text
itself; references quoted in a reply are now checked against what the tools actually returned. It
also reframed how I read the earlier evaluation runs — a reply that *sounds* like a correct
escalation is not evidence that one happened, and only `actions` settles it.

## What the prompt is not asked to do

Several things were deliberately moved out of the prompt during hardening, because asking the model
to remember them is weaker than making them true:

- **When a session is verified** is a state machine whose only entry to VERIFIED is the deterministic
  ownership check, not an instruction to keep track.
- **Whether an action already happened** is a ledger lookup, not a memory of what it did earlier.
- **When to stop looping** is a step counter checked before each round, not a request to be concise.
- **What a handoff contains** is assembled from the turn's actual tool calls, not narrated.
- **Whether a case reference is real** is a comparison against the references the tools issued, not
  an instruction to only quote genuine ones.
- **Which refund row applies** is a lookup on the order's payment method, not a reading of the
  policy table — and a cash-on-delivery refund escalates inside the tool rather than relying on
  the model to recall policy 3.3.

## Known prompt limitations

- **Coverage is a judgement the prompt asks the model to make.** "Does the retrieved passage answer
  this question?" cannot be decided from retrieval scores — covered and uncovered questions overlap
  ("do you ship to Antarctica" scores above "how long do I have to return something"). With a model
  in front this works well; the offline fallback has no one to make the call, so it answers only on a
  confident match and escalates otherwise. That is safe but noticeably more cautious.
- **The guards key off text patterns.** They can only force a tool call or defer, never decide an
  answer, but an unusually phrased request could slip past them.
- **Style instructions are the least reliably followed.** Tone and formatting drift between
  providers; the safety-critical instructions are duplicated in tool descriptions precisely because
  the prompt alone is not enough.
- **No prompt-injection instruction is load-bearing.** Content gates catch card and bank patterns
  before the provider, and the tool layer bounds what any instruction can achieve, but an injection
  inside an order field would still reach the model.
