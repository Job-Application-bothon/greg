"""Runtime configuration loaded dynamically from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
DEFAULT_PLANNER_MODEL_DIR = PACKAGE_DIR / "forecasting" / "models"


def _resolve_default_bank_mock_dir() -> Path:
    candidates = (
        PACKAGE_DIR.parent.parent / "bank_mock",
        Path.cwd() / "bank_mock",
        Path("/app/bank_mock"),
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


DEFAULT_BANK_MOCK_DIR = _resolve_default_bank_mock_dir()


@dataclass(frozen=True)
class Settings:
    """Resolved runtime configuration for Greg."""

    host: str
    port: int
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    planner_model_dir: Path
    bank_mock_dir: Path
    static_dir: Path
    session_max: int
    session_ttl: int


def load_settings() -> Settings:
    """Read runtime settings from environment variables."""
    host = os.environ.get("HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.environ.get("PORT", "8000").strip() or "8000")
    llm_base_url = os.environ.get("LLM_BASE_URL", "").strip().rstrip("/")
    llm_model = os.environ.get("LLM_MODEL", "").strip()
    llm_api_key = os.environ.get("LLM_API_KEY", "").strip()
    planner_dir_raw = os.environ.get("GREG_PLANNER_DIR", "").strip()
    planner_model_dir = Path(planner_dir_raw) if planner_dir_raw else DEFAULT_PLANNER_MODEL_DIR
    bank_mock_dir_raw = os.environ.get("GREG_BANK_MOCK_DIR", "").strip()
    bank_mock_dir = Path(bank_mock_dir_raw) if bank_mock_dir_raw else _resolve_default_bank_mock_dir()
    session_max = max(1, int(os.environ.get("GREG_SESSION_MAX", "50").strip() or "50"))
    session_ttl = max(1, int(os.environ.get("GREG_SESSION_TTL", "7200").strip() or "7200"))
    return Settings(
        host=host,
        port=port,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
        planner_model_dir=planner_model_dir,
        bank_mock_dir=bank_mock_dir,
        static_dir=STATIC_DIR,
        session_max=session_max,
        session_ttl=session_ttl,
    )
