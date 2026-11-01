"""Process-wide singletons (backends, router, app state, platform), set in the API lifespan."""
from __future__ import annotations

STATE: dict = {}
