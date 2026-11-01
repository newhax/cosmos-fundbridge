"""Shared Context passed to every agent."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..llm import LLMSession
from ..models import NGOProfile
from ..stores.csr_base import CSRStore
from ..stores.memory_base import MemoryStore


@dataclass
class Context:
    ngo: NGOProfile
    csr: CSRStore
    memory: MemoryStore
    llm: LLMSession  # one session per run: Claude per-run cap + call log
    run_id: str = field(default_factory=lambda: f"run_{uuid.uuid4().hex[:10]}")
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    lookback_years: int = 5
