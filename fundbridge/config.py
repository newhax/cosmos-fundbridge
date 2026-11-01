"""Runtime settings, read once from the environment (.env supported)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
SAMPLE_DIR = DATA_DIR / "sample"
# Overridable so the test suite can use its own SAMPLE database while the app runs on REAL data.
PROCESSED_DIR = Path(os.getenv("FUNDBRIDGE_PROCESSED_DIR") or DATA_DIR / "processed")
MEMORY_DIR = DATA_DIR / "memory"

# One folder per dataset version plus the active pointer (see fundbridge/datasets.py).
DATASETS_DIR = PROCESSED_DIR / "datasets"
QUALITY_REPORT_PATH = PROCESSED_DIR / "data_quality_report.json"
MEMORY_DB_PATH = MEMORY_DIR / "memory.sqlite"
PLATFORM_DB_PATH = DATA_DIR / "app" / "platform.sqlite"

# Researcher scoring weights (must sum to 1.0). Kept in config so they are
# visible, versioned and identical for every run.
SCORE_WEIGHTS: dict[str, float] = {
    "sector_match": 0.35,
    "state_match": 0.25,
    "district_overlap": 0.10,
    "consistency_over_years": 0.15,
    "ticket_size_fit": 0.15,
}

# Location overlap between two area lists (NGO, company, project, call): best level wins.
# Missing location scores nothing and is shown as "unknown".
GEO_MATCH_WEIGHTS: dict[str, float] = {"district": 1.0, "state": 0.6, "all_india": 0.3}

# Coverage of the public filings per (state, sector) and (district, sector), counted on the active dataset:
# GOOD needs at least this many distinct companies AND financial years; anything above zero is THIN.
COVERAGE_GOOD: dict[str, int] = {"companies": 10, "years": 3}
COVERAGE_GOOD_DISTRICT: dict[str, int] = {"companies": 3, "years": 2}

# Fallback evidence for thin areas: shown separately, never mixed into local matches, and weighted down.
FALLBACK_WEIGHTS: dict[str, float] = {"similar_states": 0.5, "pan_india": 0.35}
FALLBACK_SIMILAR_STATES = 3

# Languages: English and Hindi have UI translations; the rest are targets (add frontend/src/i18n/<code>.json).
I18N_LANGUAGES: dict[str, str] = {
    "en": "English", "hi": "हिन्दी", "ta": "தமிழ்", "te": "తెలుగు", "bn": "বাংলা", "mr": "मराठी", "gu": "ગુજરાતી",
    "kn": "ಕನ್ನಡ", "ml": "മലയാളം", "or": "ଓଡ଼ିଆ", "pa": "ਪੰਜਾਬੀ", "as": "অসমীয়া",
}

# Follow-up rules (days)
NUDGE_AFTER_DAYS = 7
NEW_ANGLE_AFTER_DAYS = 14


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# Model routing: role -> ordered list of (provider, model_slot). The first
# available entry is used; later entries are fallbacks.
ROLE_ROUTES: dict[str, list[tuple[str, str]]] = {
    "prospector": [("gemini", "fast")],
    "verifier": [("gemini", "fast")],
    "parser": [("gemini", "fast")],
    # Gemini FAST is the last resort when MAIN is overloaded/rate-limited.
    "researcher": [("gemini", "main"), ("gemini", "fast")],  # RESEARCHER_PROVIDER=claude prepends Claude
    "writer": [("claude", "claude"), ("gemini", "main"), ("gemini", "fast")],
    "followup_draft": [("claude", "claude"), ("gemini", "main"), ("gemini", "fast")],
    "screener": [("gemini", "fast")],
    # Donor-side features use Gemini only (no Claude credit).
    "matcher": [("gemini", "fast")],
    "update_draft": [("gemini", "main"), ("gemini", "fast")],
    "reply_draft": [("claude", "claude"), ("gemini", "main"), ("gemini", "fast")],
    # Draft translation: Gemini only (Claude is reserved for the Writer under its own cap).
    "translator": [("gemini", "main"), ("gemini", "fast")],
}


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)) or default)
    except ValueError:
        return default


@dataclass
class Settings:
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    # Free-tier Gemini models (https://ai.google.dev/gemini-api/docs/pricing); check limits in AI Studio.
    gemini_model_fast: str = field(default_factory=lambda: _env("GEMINI_MODEL_FAST", "gemini-3.5-flash-lite"))
    gemini_model_main: str = field(default_factory=lambda: _env("GEMINI_MODEL_MAIN", "gemini-3.8-flash"))
    # Comma-separated extra Gemini models (same key, separate free quotas), tried in order after MAIN.
    gemini_model_backups: list[str] = field(default_factory=lambda: [
        m.strip() for m in _env("GEMINI_MODEL_BACKUPS").split(",") if m.strip()])
    claude_model: str = field(default_factory=lambda: _env("CLAUDE_MODEL", "claude-haiku-5-5"))
    llm_mode: str = field(default_factory=lambda: _env("LLM_MODE", "live").lower())  # live | cached | gemini_only
    researcher_provider: str = field(default_factory=lambda: _env("RESEARCHER_PROVIDER", "gemini").lower())
    claude_max_calls_per_run: int = field(default_factory=lambda: _int("CLAUDE_MAX_CALLS_PER_RUN", 15))
    gemini_rpm: int = field(default_factory=lambda: _int("GEMINI_RPM", 10))
    claude_rpm: int = field(default_factory=lambda: _int("CLAUDE_RPM", 20))
    gemini_daily_limit: int = field(default_factory=lambda: _int("GEMINI_DAILY_LIMIT", 250))
    claude_daily_limit: int = field(default_factory=lambda: _int("CLAUDE_DAILY_LIMIT", 100))
    # Model requests one signed-in user may trigger per day (all features together); beyond it, templates.
    llm_user_daily_cap: int = field(default_factory=lambda: _int("LLM_USER_DAILY_CAP", 60))
    max_429_retries: int = field(default_factory=lambda: _int("LLM_MAX_429_RETRIES", 3))
    # Per-request timeout; a timeout counts as overload, so the router falls back to the next model.
    llm_timeout_s: float = field(default_factory=lambda: float(_env("LLM_TIMEOUT_S", "30") or 30))
    # If a model has not answered after this many seconds, start the next model in parallel (0 = off).
    llm_hedge_after_s: float = field(default_factory=lambda: float(_env("LLM_HEDGE_AFTER_S", "8") or 0))
    memory_backend: str = field(default_factory=lambda: _env("MEMORY_BACKEND", "local").lower())
    lookback_years: int = field(default_factory=lambda: int(_env("LOOKBACK_YEARS", "5") or 5))

    meko_url: str = field(default_factory=lambda: _env("MEKO_MCP_URL", "https://mcp.mekodata.ai/mcp"))
    meko_api_key: str = field(default_factory=lambda: _env("MEKO_API_KEY"))
    meko_datapack_id: str = field(default_factory=lambda: _env("MEKO_DATAPACK_ID"))

    # Two-sided platform
    demo_mode: bool = field(default_factory=lambda: _env("DEMO_MODE", "true").lower() in ("1", "true", "yes"))
    # Off for this prototype: accounts can pitch and reply without clicking a verification link.
    # Turn on for a real deployment (needs an email provider to deliver the links).
    require_email_verification: bool = field(
        default_factory=lambda: _env("REQUIRE_EMAIL_VERIFICATION", "false").lower() in ("1", "true", "yes"))
    session_secret: str = field(default_factory=lambda: _env("SESSION_SECRET"))
    cookie_secure: bool = field(default_factory=lambda: _env("COOKIE_SECURE", "false").lower() in ("1", "true", "yes"))
    session_hours: int = field(default_factory=lambda: _int("SESSION_HOURS", 12))
    login_max_failures: int = field(default_factory=lambda: _int("LOGIN_MAX_FAILURES", 5))
    login_window_minutes: int = field(default_factory=lambda: _int("LOGIN_WINDOW_MINUTES", 15))
    pitches_per_ngo_per_day: int = field(default_factory=lambda: _int("PITCHES_PER_NGO_PER_DAY", 5))
    app_base_url: str = field(default_factory=lambda: _env("APP_BASE_URL", "http://localhost:5173"))

    def routes(self, role: str) -> list[tuple[str, str]]:
        routes = list(ROLE_ROUTES[role])
        if role == "researcher" and self.researcher_provider == "claude":
            routes.insert(0, ("claude", "claude"))
        # Each Gemini model has its own free-tier quota, so backups on the same key keep the app answering
        # when MAIN (or, for FAST-only roles, FAST) is out of quota or timing out.
        backups = [("gemini", f"backup:{i}") for i, m in enumerate(self.gemini_model_backups)
                   if m not in (self.gemini_model_fast, self.gemini_model_main)]
        if backups:
            anchor = ("gemini", "main") if ("gemini", "main") in routes else ("gemini", "fast")
            if anchor in routes:
                at = routes.index(anchor) + 1
                routes[at:at] = backups
        return routes

    def model_for(self, slot: str) -> str:
        if slot.startswith("backup:"):
            return self.gemini_model_backups[int(slot.split(":")[1])]
        return {"fast": self.gemini_model_fast, "main": self.gemini_model_main, "claude": self.claude_model}[slot]

settings = Settings()
