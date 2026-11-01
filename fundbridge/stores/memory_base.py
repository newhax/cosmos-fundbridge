"""MemoryStore interface: shared, persistent agent memory.

Holds follow-up events (keyed by ngo_id + company_id) and decision traces.
App state such as NGO profiles and saved runs lives in appstate.py instead.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import DecisionTrace, FollowUpEvent


class MemoryStore(ABC):
    backend_name: str = "abstract"

    @abstractmethod
    def remember(self, event: FollowUpEvent) -> None:
        """Persist one follow-up event."""

    @abstractmethod
    def recall(self, ngo_id: str, company_id: str | None = None) -> list[FollowUpEvent]:
        """All events for an NGO (optionally one company), oldest first."""

    @abstractmethod
    def search(self, query: str, ngo_id: str | None = None, limit: int = 10) -> list[dict]:
        """Free-text search over remembered events and traces."""

    @abstractmethod
    def log_trace(self, trace: DecisionTrace) -> None: ...

    @abstractmethod
    def log_thread(self, scope: str, key: str, title: str, message: dict) -> None:
        """Append to a conversation. scope: "pitch" (shared thread, key = pitch id),
        "ngo" (private to one NGO) or "company" (private to one company)."""

    @abstractmethod
    def read_thread(self, scope: str, key: str) -> list[dict]: ...

    def health_check(self) -> None:
        return None
