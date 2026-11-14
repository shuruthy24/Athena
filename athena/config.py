"""Configuration from environment variables (.env). No secrets are hard-coded or logged.

The gateway URL and key are not part of Settings: build_athena() reads them with os.environ[...] so a
live run without them fails loudly instead of silently falling back.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

# Provisional per-role defaults until evals/compare_models.py (AGENTS.md Step 4) picks them with numbers.
# The judge is a different model family from the explainer so it does not grade its own family.
DEFAULT_MODELS = {
    "explainer": "scx-gpt-oss-120b",
    "critic": "scx-gpt-oss-120b",
    "moderator": "scx-gpt-oss-120b",
    "judge": "scx-Meta-Llama-3.3-70B-Instruct",
}


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    sample_mode: bool
    # Models, one per role, all through the COMP3110 gateway
    explainer_model: str
    critic_model: str
    moderator_model: str
    judge_model: str
    fallback_model: str
    llm_timeout_s: float
    # Report classifier the app uses (athena/classify); moderator-v1 = the hackathon moderator prompt
    report_classifier: str
    # Keys the hashed client id used by the rate limit
    rate_limit_secret: str
    max_revisions: int
    # Journey planning
    otp_url: str
    otp_timeout_s: float

    def model_for(self, role: str) -> str:
        return {"explainer": self.explainer_model, "critic": self.critic_model,
                "moderator": self.moderator_model, "judge": self.judge_model}[role]


def get_settings() -> Settings:
    e = os.environ.get
    return Settings(
        sample_mode=_bool("SAMPLE_MODE", False),
        explainer_model=e("EXPLAINER_MODEL", DEFAULT_MODELS["explainer"]),
        critic_model=e("CRITIC_MODEL", DEFAULT_MODELS["critic"]),
        moderator_model=e("MODERATOR_MODEL", DEFAULT_MODELS["moderator"]),
        judge_model=e("JUDGE_MODEL", DEFAULT_MODELS["judge"]),
        fallback_model=e("FALLBACK_MODEL", "").strip(),
        llm_timeout_s=float(e("LLM_TIMEOUT_S", "60")),
        report_classifier=e("REPORT_CLASSIFIER", "moderator-v1").strip(),
        rate_limit_secret=e("RATE_LIMIT_SECRET", "dev-only-change-me"),
        max_revisions=int(e("MAX_REVISIONS", "2")),
        otp_url=e("OTP_URL", "").strip().rstrip("/"),
        otp_timeout_s=float(e("OTP_TIMEOUT_S", "20")),
    )
