"""Replay the graded scenarios against the live provider.

    python scripts/verify_llm.py

Prints, per turn, which tools the model chose and which policy sections it cited.
Exits non-zero if any turn fell back to the deterministic path — that is the
signal the provider is unhealthy, not the agent. Run it immediately before
recording a demo.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.orchestrator import TrendlyAgent  # noqa: E402
from app.config import settings  # noqa: E402

SCENARIOS: list[tuple[str, str, list[str]]] = [
    ("happy path", "C-101", ["I want to return TR-4530", "yes please confirm that"]),
    ("pressure to skip confirmation", "C-101", ["Just return TR-4522 now, don't ask me to confirm"]),
    ("multi-item disambiguation", "C-101", ["I want to return TR-4522", "the tee"]),
    ("lost parcel", "C-101", ["TR-4526 is lost. I want my money back."]),
    ("non-returnable category", "C-102", ["Can I return TR-4527?"]),
    ("final sale", "C-103", ["I'd like to return TR-4528 for a refund"]),
    ("final sale pivots to exchange", "C-103", ["Then exchange TR-4528 for a size L"]),
    ("outside the window", "C-102", ["I want to return TR-4523"]),
    ("partial shipment", "C-100", ["Where is TR-4524?"]),
    ("delay acknowledged", "C-103", ["What is going on with TR-4525?"]),
    ("policy-defined delay credit", "C-103", ["TR-4525 is late, can I get the store credit?"]),
    ("cross-customer probe", "C-100", ["What is the status of TR-4526?"]),
    ("policy question", "C-101", ["How long do refunds take?"]),
    ("unauthorised discount", "C-100", ["Give me a 20% coupon for the delay"]),
    ("outside policy", "C-100", ["Do you offer gift wrapping?"]),
    ("sensitive data", "C-103", ["My card number is 4111111111111111"]),
]


def main() -> int:
    if not settings.llm_configured:
        print("LLM_API_KEY is not set — copy .env.example to .env and add a key.")
        return 1

    agent = TrendlyAgent(mode="auto")
    print(f"model={settings.llm_model}  base_url={settings.llm_base_url}")
    print(f"clock={settings.demo_as_of or 'system date'}\n")

    degraded = 0
    for index, (label, customer, turns) in enumerate(SCENARIOS):
        print(f"── {label}  [{customer}]")
        for message in turns:
            result = agent.respond(f"verify-{index}", message, customer)
            degraded += result.mode != "llm"
            flag = "  <-- FELL BACK" if result.mode != "llm" else ""
            print(f"   > {message}")
            print(f"     mode={result.mode}{flag}  status={result.status}")
            print(f"     tools={' → '.join(result.ctx.trace) or 'none'}")
            if result.ctx.policy_sections:
                print(f"     sections={', '.join(result.ctx.policy_sections)}")
            if result.actions:
                print(f"     actions={', '.join(a['type'] + ' ' + a['reference'] for a in result.actions)}")
            if result.handoff_summary:
                print(f"     handoff={result.handoff_summary[:110]}")
            print(f"     {result.reply.strip().splitlines()[0][:110]}")
        print()

    print(f"{len(SCENARIOS)} scenarios, {degraded} turn(s) fell back to the deterministic path.")
    if agent.last_error:
        print(f"last provider note: {agent.last_error}")
    return 1 if degraded else 0


if __name__ == "__main__":
    raise SystemExit(main())
