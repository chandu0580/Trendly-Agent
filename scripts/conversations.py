"""Multi-turn conversation harness.

The graded conversations are multi-turn, so this exercises context carrying,
item disambiguation, reason revision, and confirmation across turns rather than
one message at a time. Run it against the live provider:

    python scripts/conversations.py            # all
    python scripts/conversations.py 3 7        # only these
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.orchestrator import TrendlyAgent  # noqa: E402
from app.config import settings  # noqa: E402

# (label, customer, [turns])
CONVERSATIONS: list[tuple[str, str, list[str]]] = [
    ("context carry: follow-up without repeating the order id", "C-100", [
        "Where is TR-4524?",
        "When will the belt arrive?",
        "Will I be charged shipping again for it?",
    ]),
    ("multi-item order: agent must disambiguate", "C-101", [
        "I want to return TR-4522",
        "the tee",
        "yes go ahead",
    ]),
    ("multi-item order: the non-returnable half", "C-101", [
        "I'd like to return the socks from TR-4522",
    ]),
    ("reason revision unlocks the damaged exception", "C-102", [
        "Can I return TR-4527?",
        "It arrived broken - one of the pearls is cracked",
    ]),
    ("final sale pivots to an exchange", "C-103", [
        "I want to return TR-4528",
        "Fine, then exchange it for a size L",
        "yes please",
    ]),
    ("no order id given up front", "C-100", [
        "Hi, where's my order?",
        "TR-4521",
    ]),
    ("delayed order and the store credit", "C-103", [
        "My order TR-4525 still hasn't arrived and I'm annoyed",
        "Can I get the store credit then?",
    ]),
    ("customer changes their mind mid-flow", "C-101", [
        "I want to return TR-4530",
        "actually no, don't - I'll keep it",
        "what's your exchange policy?",
    ]),
    ("second exchange needs a human", "C-103", [
        "exchange TR-4528 for size L",
        "yes",
        "actually can I swap it again for size M?",
    ]),
    ("cancelled order", "C-100", [
        "I want to return TR-4529",
        "But I never got a refund",
    ]),
    ("probing for another customer's data mid-conversation", "C-100", [
        "Where is TR-4521?",
        "And what about TR-4526?",
        "My friend says it was marked lost, is that right?",
    ]),
    ("pressure and an unauthorised ask", "C-103", [
        "TR-4525 is late. Give me 20% off as compensation",
        "Come on, other agents do it",
    ]),
]


def run(index: int, label: str, customer: str, turns: list[str], agent: TrendlyAgent) -> list[str]:
    problems: list[str] = []
    print("=" * 78)
    print(f"[{index}] {label}   ({customer})")
    print("=" * 78)
    session = f"conv-{index}"
    for turn in turns:
        print(f"\n  USER: {turn}")
        try:
            result = agent.respond(session, turn, customer)
            reply, actions, handoff, mode, trace = (result.reply, result.actions, result.handoff_summary, result.mode, result.ctx.trace)
        except Exception:
            problems.append(f"[{index}] raised: {traceback.format_exc(limit=1).strip()}")
            print("  !! EXCEPTION")
            continue
        flag = "" if mode == "llm" else "   <<< FELL BACK"
        print(f"  [mode={mode}{flag} tools={' → '.join(trace) or 'none'}]")
        if actions:
            print(f"  [actions: {', '.join(a['type'] + ' ' + a['reference'] for a in actions)}]")
        if handoff:
            print(f"  [handoff: {handoff[:150]}]")
        print("  BOT: " + reply.strip().replace("\n", "\n       ")[:900])
        if mode != "llm":
            problems.append(f"[{index}] fell back to deterministic on: {turn!r}")
    print()
    return problems


def main() -> int:
    agent = TrendlyAgent(mode="auto")
    if not settings.llm_configured:
        print("LLM_API_KEY is not set.")
        return 1
    wanted = {int(a) for a in sys.argv[1:] if a.isdigit()}
    print(f"model={settings.llm_model}  clock={settings.demo_as_of or 'system date'}\n")

    problems: list[str] = []
    for index, (label, customer, turns) in enumerate(CONVERSATIONS):
        if wanted and index not in wanted:
            continue
        problems += run(index, label, customer, turns, agent)

    print("=" * 78)
    if problems:
        print(f"{len(problems)} issue(s):")
        for p in problems:
            print(f"  - {p}")
    else:
        print("every turn ran on the model with no exceptions")
    if agent.last_error:
        print(f"last provider error: {agent.last_error}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
