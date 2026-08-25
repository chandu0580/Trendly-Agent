"""Environment-driven configuration. No secrets are hardcoded."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
ORDERS_PATH = DATA_DIR / "orders.json"
POLICY_PATH = DATA_DIR / "trendly_policy.md"
# Demo-only contact details. The supplied dataset has none, so this is a
# separate fixture rather than an edit to orders.json.
PROFILES_PATH = DATA_DIR / "customer_profiles.json"
VECTORSTORE_DIR = ROOT / "vectorstore" / "chroma"
POLICY_COLLECTION = "trendly_policy"


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    # --- LLM -------------------------------------------------------------
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "openai_compatible"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gemini-2.5-flash"))
    llm_api_key: str | None = field(
        default_factory=lambda: os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY")
    )
    llm_base_url: str = field(
        default_factory=lambda: os.getenv(
            "LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"
        )
    )
    llm_temperature: float = field(default_factory=lambda: _float("LLM_TEMPERATURE", 0.1))

    # --- Orchestration ---------------------------------------------------
    agent_mode: str = field(default_factory=lambda: os.getenv("AGENT_MODE", "auto").lower())
    transient_retries: int = field(default_factory=lambda: _int("LLM_TRANSIENT_RETRIES", 2))
    retry_backoff: float = field(default_factory=lambda: _float("LLM_RETRY_BACKOFF", 0.6))
    max_nudges: int = field(default_factory=lambda: _int("AGENT_MAX_NUDGES", 2))
    # Hard ceiling on agent turns within one request. A legitimate turn is
    # agent -> tools -> agent -> guard; the budget allows a few tool rounds
    # and the two guard retries on top, then stops.
    max_agent_steps: int = field(default_factory=lambda: _int("AGENT_MAX_STEPS", 8))

    # --- Retrieval -------------------------------------------------------
    retrieval_top_k: int = field(default_factory=lambda: _int("RETRIEVAL_TOP_K", 4))
    retrieval_candidates: int = field(default_factory=lambda: _int("RETRIEVAL_CANDIDATES", 8))

    # --- Demo affordances ------------------------------------------------
    # The supplied fixture is a frozen snapshot; see docs/architecture.md §4.
    demo_as_of: str | None = field(
        default_factory=lambda: (os.getenv("DEMO_AS_OF", "2026-07-29").strip() or None)
    )

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key)


settings = Settings()
