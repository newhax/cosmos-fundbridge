"""Shared offline LLM cache location for tests (never the real data/memory cache)."""
import tempfile
from pathlib import Path

OFFLINE_DB = Path(tempfile.mkdtemp(prefix="fundbridge-test-")) / "llm.sqlite"
