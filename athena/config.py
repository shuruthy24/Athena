"""Configuration from environment variables (.env). No secrets are hard-coded or logged."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    sample_mode: bool
    # Zetaris (data layer)
    zetaris_rest_url: str
    zetaris_base_url: str
    zetaris_org_id: str
    zetaris_api_key: str
    zetaris_username: str
    zetaris_password: str
    zetaris_schema: str
    zetaris_timeout_s: float
    # Meterless Nightshift (AI provider)
    meterless_base_url: str
    meterless_token: str
    fast_model: str
    critic_model: str
    llm_timeout_s: float
    # Scout Intent
    scout_secret: str
    max_revisions: int
    # Journey planning
    otp_url: str
    otp_timeout_s: float


def get_settings() -> Settings:
    e = os.environ.get
    return Settings(
        sample_mode=_bool("SAMPLE_MODE", False),
        zetaris_rest_url=e("ZETARIS_REST_URL", "http://localhost:8888/api/v1.0").rstrip("/"),
        zetaris_base_url=e("ZETARIS_BASE_URL", "http://localhost:3000").rstrip("/"),
        zetaris_org_id=e("ZETARIS_ORG_ID", "1"),
        zetaris_api_key=e("ZETARIS_API_KEY", "").strip(),
        zetaris_username=e("ZETARIS_USERNAME", ""),
        zetaris_password=e("ZETARIS_PASSWORD", ""),
        zetaris_schema=e("ZETARIS_SCHEMA", "athena"),
        zetaris_timeout_s=float(e("ZETARIS_TIMEOUT_S", "30")),
        meterless_base_url=e("METERLESS_BASE_URL", "http://127.0.0.1:8088/v1").rstrip("/"),
        meterless_token=e("METERLESS_RUNTIME_TOKEN", "").strip(),
        # Default to a Nemotron route rather than meterless/auto, so a missing .env still
        # sends work to NVIDIA Nemotron through Nightshift instead of an arbitrary model.
        fast_model=e("FAST_MODEL", e("METERLESS_MODEL", "nvidia/nemotron-3.5-bolt-e4b")),
        critic_model=e("CRITIC_MODEL", e("METERLESS_MODEL", "nvidia/nemotron-3.5-bolt-e4b")),
        llm_timeout_s=float(e("LLM_TIMEOUT_S", "60")),
        scout_secret=e("SCOUT_SECRET", "dev-only-change-me"),
        max_revisions=int(e("MAX_REVISIONS", "2")),
        otp_url=e("OTP_URL", "").strip().rstrip("/"),
        otp_timeout_s=float(e("OTP_TIMEOUT_S", "20")),
    )
