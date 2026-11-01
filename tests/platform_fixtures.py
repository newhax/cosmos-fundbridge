"""Shared fixtures: the real app with an isolated platform DB, memory store and offline router."""
import dataclasses

from fastapi.testclient import TestClient

from fundbridge.api import app
from fundbridge.appstate import AppState
from fundbridge.config import settings
from fundbridge.llm import Router
from fundbridge.platform.service import Platform
from fundbridge.state import STATE
from fundbridge.stores.memory_local import LocalMemoryStore

PW = "demo-pass-2026"
SHIKSHA = "team@shiksha-setu.demo.fundbridge.local"
HARA = "team@hara-bhara.demo.fundbridge.local"
SAKHI = "team@sakhi-collective.demo.fundbridge.local"
THAR = "csr@thar-minerals.demo.fundbridge.local"
MARUDHAR = "csr@marudhar-power.demo.fundbridge.local"
SAHYADRI = "csr@sahyadri-textiles.demo.fundbridge.local"

TEST_SETTINGS = dataclasses.replace(
    settings, session_secret="test-secret", demo_mode=True, login_max_failures=3, pitches_per_ngo_per_day=2,
    gemini_api_key="", anthropic_api_key="", llm_mode="live", app_base_url="http://testserver")


def install(tmp_path, seed: bool = True) -> Platform:
    """(Re)build the platform singletons on tmp_path, like a server (re)start."""
    from seed_platform import main as seed_main

    memory = LocalMemoryStore(tmp_path / "memory.sqlite")
    STATE["backends"].memory = memory
    STATE["llm"] = Router(TEST_SETTINGS, db_path=tmp_path / "llm.sqlite")
    STATE["app"] = AppState(tmp_path / "app.sqlite")
    platform = Platform(tmp_path / "platform.sqlite", memory, TEST_SETTINGS)
    STATE["platform"] = platform
    if seed:
        seed_main(platform, quiet=True)
    return platform


def login(email: str, password: str = PW) -> TestClient:
    c = TestClient(app)  # no context manager: shares the already-started app state
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return c

ANANYA_EMAIL = "ananya@donor.demo.fundbridge.local"
