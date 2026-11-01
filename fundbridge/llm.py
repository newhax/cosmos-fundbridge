"""Provider-agnostic model router: Gemini (free tier, workhorse) + Claude (trial credit, sparingly).

Single entry point:  session.call(role, system, user, schema) -> LLMResult | None
  - role -> provider/model via config.ROLE_ROUTES (first available wins, rest are fallbacks)
  - Gemini: structured output (response_schema). Claude: strict JSON instruction.
    Both validated with pydantic; invalid JSON is retried once.
  - Fallback chain: Claude error / 429 / no credit / per-run cap / daily cap -> next route (Gemini).
    Every result is tagged with the provider+model that actually produced it.
  - Free-tier survival: per-provider rate limiter, exponential backoff on 429, SQLite response
    cache (identical calls never hit the API twice), persisted daily call counters.
  - LLM_MODE=cached replays the cache only (zero API calls); gemini_only never touches Claude.
None means no model output is available; callers fall back to evidence-only templates.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from .config import MEMORY_DIR, Settings

log = logging.getLogger("fundbridge.llm")
T = TypeVar("T", bound=BaseModel)
MAX_OUTPUT_TOKENS = 8192  # Gemini counts thinking tokens against this, so keep it generous.
_HEDGE_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="llm")  # shared by all sessions
OVERLOAD_COOLDOWN_S = 300  # skip a model that returned 5xx / timed out for this long (a fallback answers meanwhile)
RATE_LIMIT_COOLDOWN_S = 60  # skip a model that returned 429 for this long, unless the API says how long to wait


# -- errors / results ----------------------------------------------------------------
class ProviderError(Exception):
    """kind: rate_limit | overloaded | no_credit | auth | refusal | error | invalid_json | daily_limit | cap"""

    def __init__(self, kind: str, message: str = "", retry_after: float | None = None):
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message
        self.retry_after = retry_after  # seconds, when the API says how long to wait


@dataclass
class RawResponse:
    payload: Any  # pydantic instance, dict, or JSON text
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class CallMeta:
    role: str
    provider: str
    model: str
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False
    fallback_from: str | None = None  # e.g. "claude:claude-haiku-5-5 (rate_limit)"
    outcome: str = "ok"
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    @property
    def produced_by(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass
class LLMResult:
    output: Any
    meta: CallMeta


# -- providers -----------------------------------------------------------------------
class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, timeout_s: float = 30.0):
        from google import genai
        from google.genai import types

        self.client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))
        self._no_thinking_level: set[str] = set()

    def generate(self, model: str, system: str, user: str, schema: type[BaseModel], max_tokens: int) -> RawResponse:
        from google.genai import errors, types

        cfg = dict(system_instruction=system, response_mime_type="application/json", response_schema=schema,
                   max_output_tokens=max_tokens)
        if model not in self._no_thinking_level:
            cfg["thinking_config"] = types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)
        try:
            resp = self.client.models.generate_content(model=model, contents=user,
                                                       config=types.GenerateContentConfig(**cfg))
        except errors.APIError as e:
            if e.code == 429:
                m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', json.dumps(getattr(e, "details", None) or {}))
                raise ProviderError("rate_limit", e.message or "", float(m.group(1)) if m else None) from e
            if e.code in (500, 502, 503, 504):
                raise ProviderError("overloaded", f"{e.code} {e.message}") from e
            if e.code == 400 and "thinking" in (e.message or "").lower() and model not in self._no_thinking_level:
                self._no_thinking_level.add(model)  # model doesn't support thinking_level: retry without it
                return self.generate(model, system, user, schema, max_tokens)
            if e.code in (401, 403):
                raise ProviderError("auth", e.message or "") from e
            raise ProviderError("error", f"{e.code} {e.message}") from e
        except Exception as e:  # noqa: BLE001 - transport errors
            if "timed out" in str(e).lower() or "timeout" in type(e).__name__.lower():
                raise ProviderError("overloaded", f"timeout: {e}") from e
            raise ProviderError("error", str(e)) from e
        if resp.prompt_feedback and resp.prompt_feedback.block_reason:
            raise ProviderError("refusal", str(resp.prompt_feedback.block_reason))
        um = resp.usage_metadata
        tokens_in = (um.prompt_token_count or 0) if um else 0
        tokens_out = ((um.candidates_token_count or 0) + (um.thoughts_token_count or 0)) if um else 0
        payload = resp.parsed if isinstance(resp.parsed, schema) else (resp.text or "")
        return RawResponse(payload, tokens_in, tokens_out)


class ClaudeProvider:
    name = "claude"

    def __init__(self, api_key: str, timeout_s: float = 30.0):
        import anthropic

        # Retries are handled by the router (backoff, then fall back to Gemini).
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=0)

    def generate(self, model: str, system: str, user: str, schema: type[BaseModel], max_tokens: int) -> RawResponse:
        import anthropic

        system_json = (
            f"{system}\n\nOUTPUT FORMAT: respond with ONLY one JSON object that validates against this JSON Schema. "
            f"No prose, no markdown fences.\n{json.dumps(schema.model_json_schema())}"
        )
        try:
            resp = self.client.messages.create(
                model=model, max_tokens=max_tokens, system=system_json,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "low"},  # trial credit: keep thinking/output spend small
            )
        except anthropic.RateLimitError as e:
            raise ProviderError("rate_limit", e.message) from e
        except anthropic.InternalServerError as e:
            raise ProviderError("overloaded", f"{e.status_code} {e.message}") from e
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise ProviderError("auth", e.message) from e
        except anthropic.BadRequestError as e:
            kind = "no_credit" if "credit" in (e.message or "").lower() else "error"
            raise ProviderError(kind, e.message) from e
        except anthropic.APIStatusError as e:
            raise ProviderError("error", f"{e.status_code} {e.message}") from e
        except anthropic.APITimeoutError as e:
            raise ProviderError("overloaded", f"timeout: {e}") from e
        except anthropic.APIConnectionError as e:
            raise ProviderError("error", str(e)) from e
        if resp.stop_reason == "refusal":
            raise ProviderError("refusal", "model declined")
        if resp.stop_reason == "max_tokens":
            raise ProviderError("invalid_json", "output truncated at max_tokens")
        text = "".join(b.text for b in resp.content if b.type == "text")
        return RawResponse(text, resp.usage.input_tokens, resp.usage.output_tokens)


# -- free-tier survival helpers --------------------------------------------------------
class RateLimiter:
    """At most `rpm` requests in any 60 s window (per provider:model). Bursts up to `rpm` go out immediately."""

    def __init__(self, rpm: int, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.rpm = max(rpm, 1)
        self.sleep, self.clock = sleep, clock
        self._sent: deque[float] = deque()  # reserved send times, oldest first
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self.clock()
            while self._sent and self._sent[0] <= now - 60.0:
                self._sent.popleft()
            at = now if len(self._sent) < self.rpm else self._sent[-self.rpm] + 60.0
            self._sent.append(at)
        if at > now:
            self.sleep(at - now)


def _quota_day() -> str:
    """Gemini daily quotas reset at midnight Pacific time."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
    except Exception:  # noqa: BLE001 - tz database unavailable
        return datetime.now(timezone.utc).date().isoformat()


class LLMStore:
    """SQLite: response cache + daily per-provider call counters."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._con() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, provider TEXT, model TEXT, role TEXT,
                    created_at TEXT, output TEXT, input_tokens INTEGER, output_tokens INTEGER);
                CREATE TABLE IF NOT EXISTS usage (day TEXT, provider TEXT, calls INTEGER,
                    input_tokens INTEGER, output_tokens INTEGER, PRIMARY KEY (day, provider));
                CREATE TABLE IF NOT EXISTS user_usage (day TEXT, user_id TEXT, calls INTEGER, PRIMARY KEY (day, user_id));
                """
            )

    def _con(self):
        return sqlite3.connect(self.path)

    @staticmethod
    def key(provider: str, model: str, system: str, user: str, schema: type[BaseModel]) -> str:
        # schema name included so two schemas with the same prompt cannot collide
        raw = json.dumps([provider, model, system, user, schema.__name__])
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key: str) -> tuple[str, int, int] | None:
        with self._lock, self._con() as con:
            r = con.execute("SELECT output, input_tokens, output_tokens FROM cache WHERE key = ?", (key,)).fetchone()
        return r

    def put(self, key: str, provider: str, model: str, role: str, output: str, tin: int, tout: int) -> None:
        with self._lock, self._con() as con:
            con.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?,?,?,?)",
                        (key, provider, model, role, datetime.now(timezone.utc).isoformat(), output, tin, tout))

    def count(self, provider: str, tin: int = 0, tout: int = 0) -> None:
        with self._lock, self._con() as con:
            con.execute(
                "INSERT INTO usage VALUES (?,?,1,?,?) ON CONFLICT(day, provider) DO UPDATE SET "
                "calls = calls + 1, input_tokens = input_tokens + ?, output_tokens = output_tokens + ?",
                (_quota_day(), provider, tin, tout, tin, tout))

    def used_today(self, provider: str) -> dict:
        with self._lock, self._con() as con:
            r = con.execute("SELECT calls, input_tokens, output_tokens FROM usage WHERE day = ? AND provider = ?",
                            (_quota_day(), provider)).fetchone()
        return {"calls": r[0], "input_tokens": r[1], "output_tokens": r[2]} if r else {"calls": 0, "input_tokens": 0, "output_tokens": 0}

    def count_user(self, user_id: str) -> None:
        with self._lock, self._con() as con:
            con.execute("INSERT INTO user_usage VALUES (?,?,1) ON CONFLICT(day, user_id) DO UPDATE SET calls = calls + 1",
                        (_quota_day(), user_id))

    def user_today(self, user_id: str) -> int:
        with self._lock, self._con() as con:
            r = con.execute("SELECT calls FROM user_usage WHERE day = ? AND user_id = ?", (_quota_day(), user_id)).fetchone()
        return r[0] if r else 0

    def cache_size(self) -> int:
        with self._lock, self._con() as con:
            return con.execute("SELECT COUNT(*) FROM cache").fetchone()[0]


def _validate(schema: type[T], payload: Any) -> T:
    if isinstance(payload, schema):
        return payload
    if isinstance(payload, dict):
        return schema.model_validate(payload)
    text = str(payload).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)  # tolerate fences despite the instruction
    if not text.startswith("{"):
        m = re.search(r"\{.*\}", text, re.S)
        text = m.group(0) if m else text
    return schema.model_validate_json(text)


# -- router ----------------------------------------------------------------------------
class Router:
    def __init__(self, settings: Settings, providers: dict | None = None, db_path: Path | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self.mode = settings.llm_mode if settings.llm_mode in ("live", "cached", "gemini_only") else "live"
        self.store = LLMStore(db_path or MEMORY_DIR / "llm_cache.sqlite")
        self.sleep = sleep
        self.disabled: dict[str, str] = {}  # provider -> reason (circuit breaker for auth / no credit)
        if providers is not None:
            self.providers = providers
        else:
            self.providers = {}
            if settings.gemini_api_key:
                self.providers["gemini"] = GeminiProvider(settings.gemini_api_key, settings.llm_timeout_s)
            if settings.anthropic_api_key:
                self.providers["claude"] = ClaudeProvider(settings.anthropic_api_key, settings.llm_timeout_s)
        # Free-tier limits are per model, so each provider:model gets its own limiter.
        self.rpm = {"gemini": settings.gemini_rpm, "claude": settings.claude_rpm}
        self.limiters: dict[str, RateLimiter] = {}
        self._limiter_lock = threading.Lock()
        self.cooldown: dict[str, float] = {}  # "provider:model" -> monotonic time until which it is skipped
        self.daily_limits = {"gemini": settings.gemini_daily_limit, "claude": settings.claude_daily_limit}

    @property
    def enabled(self) -> bool:
        return bool(self.providers) or self.mode == "cached"

    def limiter(self, provider: str, model: str) -> RateLimiter:
        with self._limiter_lock:
            key = f"{provider}:{model}"
            if key not in self.limiters:
                self.limiters[key] = RateLimiter(self.rpm[provider], self.sleep)
            return self.limiters[key]

    def session(self, run_id: str = "", user_id: str | None = None) -> "LLMSession":
        return LLMSession(self, run_id, user_id)

    def _provider_usable(self, provider: str) -> str | None:
        """Return None if usable, else the reason it is skipped."""
        if provider == "claude" and self.mode == "gemini_only":
            return "LLM_MODE=gemini_only"
        if self.mode == "cached":
            return None  # cache lookups only; no key needed
        if provider not in self.providers:
            return "no API key"
        if provider in self.disabled:
            return self.disabled[provider]
        if self.store.used_today(provider)["calls"] >= self.daily_limits[provider]:
            return "daily limit reached"
        return None

    def status(self) -> dict:
        prov = {}
        for p in ("gemini", "claude"):
            used = self.store.used_today(p)
            prov[p] = {
                "configured": p in self.providers, "skipped_reason": self._provider_usable(p),
                "models": ([self.settings.gemini_model_fast, self.settings.gemini_model_main,
                            *self.settings.gemini_model_backups] if p == "gemini" else [self.settings.claude_model]),
                "cooling_down": {k.split(":", 1)[1]: int(t - time.monotonic()) for k, t in self.cooldown.items()
                                 if k.startswith(f"{p}:") and t > time.monotonic()},
                "used_today": used["calls"], "daily_limit": self.daily_limits[p],
                "remaining_today": max(0, self.daily_limits[p] - used["calls"]),
                "tokens_today": used["input_tokens"] + used["output_tokens"],
            }
        return {"mode": self.mode, "providers": prov, "claude_max_calls_per_run": self.settings.claude_max_calls_per_run,
                "cache_entries": self.store.cache_size(), "enabled": self.enabled}

    def warnings(self) -> list[str]:
        out = []
        if not self.providers and self.mode != "cached":
            out.append("No GEMINI_API_KEY / ANTHROPIC_API_KEY: explanations and emails use evidence-only templates (offline mode).")
        elif "gemini" not in self.providers and self.mode != "cached":
            out.append("GEMINI_API_KEY not set: Gemini roles (prospector, researcher, verifier) are offline.")
        if self.mode == "cached":
            out.append("LLM_MODE=cached: replaying cached model responses only (zero API calls); cache misses use templates.")
        return out


class LLMSession:
    """One unit of work (pipeline run / request): Claude per-run cap + call log."""

    def __init__(self, router: Router, run_id: str, user_id: str | None = None):
        self.router, self.run_id, self.user_id = router, run_id, user_id
        self.claude_calls = 0
        self.calls: list[CallMeta] = []
        self.capacity_reached = False  # a daily cap (per user or per provider) stopped a model call

    @property
    def enabled(self) -> bool:
        return self.router.enabled

    def call_log(self) -> list[dict]:
        return [asdict(c) for c in self.calls]

    def call(self, role: str, system: str, user: str, schema: type[T], max_tokens: int = MAX_OUTPUT_TOKENS) -> LLMResult | None:
        """Try the role's models in order. A failure moves on at once; a model that has not answered after
        LLM_HEDGE_AFTER_S gets the next model started alongside it (hedged request) and the first valid
        answer wins. The slower call is left to finish in the background (its answer is cached, not used)."""
        r, s = self.router, self.router.settings
        if self.user_id and r.mode != "cached" and r.enabled:
            if r.store.user_today(self.user_id) >= s.llm_user_daily_cap:
                self.capacity_reached = True
                self._log(CallMeta(role, "none", "none", outcome=f"capacity: per-user daily cap {s.llm_user_daily_cap} reached"))
                return None
            r.store.count_user(self.user_id)
        skipped: list[str] = []
        routes = list(enumerate(s.routes(role)))
        n_routes = len(routes)
        won = threading.Event()

        def next_candidate():
            """('cached', LLMResult) | ('live', (i, provider, model, key)) | None when the routes are exhausted."""
            while routes:
                i, (provider, slot) = routes.pop(0)
                model = s.model_for(slot)
                reason = r._provider_usable(provider)
                if reason is None and provider == "claude" and self.claude_calls >= s.claude_max_calls_per_run:
                    reason = f"CLAUDE_MAX_CALLS_PER_RUN={s.claude_max_calls_per_run} reached"
                if reason is None and r.mode != "cached" and r.cooldown.get(f"{provider}:{model}", 0) > time.monotonic():
                    reason = "overloaded, cooling down"
                if reason:
                    skipped.append(f"{provider}:{model} ({reason})")
                    if "daily limit" in reason:
                        self.capacity_reached = True
                    continue
                key = LLMStore.key(provider, model, system, user, schema)
                hit = r.store.get(key)
                if hit:
                    meta = CallMeta(role, provider, model, 0, hit[1], hit[2], cached=True,
                                    fallback_from="; ".join(skipped) or None)
                    self._log(meta)
                    return "cached", LLMResult(schema.model_validate_json(hit[0]), meta)
                if r.mode == "cached":
                    skipped.append(f"{provider}:{model} (cache miss)")
                    continue
                return "live", (i, provider, model, key)
            return None

        def attempt(i, provider, model, key):
            try:
                out, meta = self._live(role, provider, model, system, user, schema, max_tokens,
                                       can_fallback=i < n_routes - 1)
            except ProviderError as e:
                if e.kind in ("auth", "no_credit"):
                    r.disabled[provider] = e.kind
                if e.kind == "overloaded":
                    r.cooldown[f"{provider}:{model}"] = time.monotonic() + OVERLOAD_COOLDOWN_S
                elif e.kind == "rate_limit":
                    r.cooldown[f"{provider}:{model}"] = time.monotonic() + (e.retry_after or RATE_LIMIT_COOLDOWN_S)
                log.warning("role=%s %s:%s failed: %s", role, provider, model, e)
                self._log(CallMeta(role, provider, model, outcome=f"error:{e.kind}: {e.message[:160]}"))
                raise
            r.store.put(key, provider, model, role, out.model_dump_json(), meta.input_tokens, meta.output_tokens)
            if won.is_set():  # another model answered first: keep the record honest
                meta.outcome = "ok (hedged; answer not used)"
                self._log(meta)
            return out, meta

        running: dict = {}  # future -> "provider:model"

        def launch() -> LLMResult | None:
            nxt = next_candidate()
            if nxt is None:
                return None
            kind, val = nxt
            if kind == "cached":
                return val
            running[_HEDGE_POOL.submit(attempt, *val)] = f"{val[1]}:{val[2]}"
            return None

        hit = launch()
        if hit is not None:
            return hit
        hedge = s.llm_hedge_after_s
        while running:
            done, _ = wait(list(running), timeout=hedge if hedge > 0 and routes else None, return_when=FIRST_COMPLETED)
            if not done:  # slow model: start the next one alongside it
                hedged = list(running.values())
                hit = launch()
                if hit is not None:
                    won.set()
                    return hit
                log.info("role=%s: %s slower than %.0fs, hedging with the next model", role, ", ".join(hedged), hedge)
                continue
            for f in done:
                label = running.pop(f)
                try:
                    out, meta = f.result()
                except ProviderError as e:
                    skipped.append(f"{label} ({e.kind})")
                    if not running:  # nothing else in flight: try the next model now
                        hit = launch()
                        if hit is not None:
                            return hit
                    continue
                won.set()
                still = [f"{x} (slower; hedged)" for x in running.values()]
                meta.fallback_from = "; ".join(skipped + still) or None
                self._log(meta)
                return LLMResult(out, meta)
        log.info("role=%s: no model output (%s)", role, "; ".join(skipped))
        return None

    def _live(self, role, provider, model, system, user, schema, max_tokens, can_fallback=False):
        r = self.router
        p = r.providers[provider]
        prompt = user
        for json_attempt in (1, 2):
            raw, latency = self._send(provider, p, model, system, prompt, schema, max_tokens, can_fallback)
            try:
                out = _validate(schema, raw.payload)
                return out, CallMeta(role, provider, model, latency, raw.input_tokens, raw.output_tokens)
            except (ValidationError, ValueError) as e:
                if json_attempt == 2:
                    raise ProviderError("invalid_json", str(e)[:200]) from e
                prompt = f"{user}\n\nYour previous reply was not valid JSON for the schema ({str(e)[:300]}). Reply with JSON only."
        raise ProviderError("invalid_json", "unreachable")

    def _send(self, provider, p, model, system, prompt, schema, max_tokens, can_fallback=False) -> tuple[RawResponse, int]:
        """Rate-limit, count, call. On 429/5xx/timeout: fall back at once if another model is routed,
        else exponential backoff (429) or one backoff (5xx) on this model."""
        r = self.router
        for attempt in range(r.settings.max_429_retries + 1):
            if r.store.used_today(provider)["calls"] >= r.daily_limits[provider]:
                raise ProviderError("daily_limit", "daily call budget used")
            if provider == "claude":
                if self.claude_calls >= r.settings.claude_max_calls_per_run:
                    raise ProviderError("cap", "per-run Claude cap reached")
                self.claude_calls += 1
            r.limiter(provider, model).wait()
            t0 = time.perf_counter()
            try:
                raw = p.generate(model, system, prompt, schema, max_tokens)
            except ProviderError as e:
                r.store.count(provider)
                # 429: up to LLM_MAX_429_RETRIES backoffs. 5xx overload: one backoff, then fall back.
                limit = r.settings.max_429_retries if e.kind == "rate_limit" else min(1, r.settings.max_429_retries)
                if e.kind in ("rate_limit", "overloaded") and attempt < limit and not can_fallback:
                    delay = min(2.0 * (2 ** attempt), 30.0)
                    log.warning("%s:%s %s; backing off %.0fs", provider, model, e.message[:80] or e.kind, delay)
                    r.sleep(delay)
                    continue
                raise
            latency = int((time.perf_counter() - t0) * 1000)
            r.store.count(provider, raw.input_tokens, raw.output_tokens)
            log.info("llm provider=%s model=%s latency_ms=%d tokens_in=%d tokens_out=%d",
                     provider, model, latency, raw.input_tokens, raw.output_tokens)
            return raw, latency
        raise ProviderError("rate_limit", "retries exhausted")

    def _log(self, meta: CallMeta) -> None:
        self.calls.append(meta)
