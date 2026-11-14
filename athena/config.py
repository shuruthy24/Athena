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
DATA_BACKENDS = ("files", "zetaris")


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    sample_mode: bool
    data_backend: str
    # Models, one per role, all through the COMP3110 gateway
    explainer_model: str
    critic_model: str
    moderator_model: str
    judge_model: str
    fallback_model: str
    llm_timeout_s: float
    # Report classifier the app uses (athena/classify); moderator-v1 = the hackathon moderator prompt
    report_classifier: str
    # Zetaris (optional data adapter, DATA_BACKEND=zetaris)
    zetaris_rest_url: str
    zetaris_base_url: str
    zetaris_org_id: str
    zetaris_api_key: str
    zetaris_username: str
    zetaris_password: str
    zetaris_schema: str
    zetaris_timeout_s: float
    # Scout Intent
    scout_secret: str
    max_revisions: int
    # Journey planning
    otp_url: str
    otp_timeout_s: float

    def model_for(self, role: str) -> str:
        return {"explainer": self.explainer_model, "critic": self.critic_model,
                "moderator": self.moderator_model, "judge": self.judge_model}[role]


def get_settings() -> Settings:
    e = os.environ.get
    backend = e("DATA_BACKEND", "files").strip().lower()
    if backend not in DATA_BACKENDS:
        raise ValueError(f"DATA_BACKEND must be one of {', '.join(DATA_BACKENDS)}")
    return Settings(
        sample_mode=_bool("SAMPLE_MODE", False),
        data_backend=backend,
        explainer_model=e("EXPLAINER_MODEL", DEFAULT_MODELS["explainer"]),
        critic_model=e("CRITIC_MODEL", DEFAULT_MODELS["critic"]),
        moderator_model=e("MODERATOR_MODEL", DEFAULT_MODELS["moderator"]),
        judge_model=e("JUDGE_MODEL", DEFAULT_MODELS["judge"]),
        fallback_model=e("FALLBACK_MODEL", "").strip(),
        llm_timeout_s=float(e("LLM_TIMEOUT_S", "60")),
        report_classifier=e("REPORT_CLASSIFIER", "moderator-v1").strip(),
        zetaris_rest_url=e("ZETARIS_REST_URL", "http://localhost:8888/api/v1.0").rstrip("/"),
        zetaris_base_url=e("ZETARIS_BASE_URL", "http://localhost:3000").rstrip("/"),
        zetaris_org_id=e("ZETARIS_ORG_ID", "1"),
        zetaris_api_key=e("ZETARIS_API_KEY", "").strip(),
        zetaris_username=e("ZETARIS_USERNAME", ""),
        zetaris_password=e("ZETARIS_PASSWORD", ""),
        zetaris_schema=e("ZETARIS_SCHEMA", "athena"),
        zetaris_timeout_s=float(e("ZETARIS_TIMEOUT_S", "30")),
        scout_secret=e("SCOUT_SECRET", "dev-only-change-me"),
        max_revisions=int(e("MAX_REVISIONS", "2")),
        otp_url=e("OTP_URL", "").strip().rstrip("/"),
        otp_timeout_s=float(e("OTP_TIMEOUT_S", "20")),
    )
