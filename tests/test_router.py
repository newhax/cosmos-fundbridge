"""Model router: routing, fallback tagging, cache, 429 backoff, Claude cap, modes. No network."""
import time

import pytest
from pydantic import BaseModel

from fundbridge.config import Settings
from fundbridge.llm import ProviderError, RateLimiter, RawResponse, Router


class Out(BaseModel):
    answer: str


class FakeProvider:
    def __init__(self, name, script=None):
        self.name, self.script, self.calls = name, list(script or []), []

    def generate(self, model, system, user, schema, max_tokens):
        self.calls.append((model, user))
        step = self.script.pop(0) if self.script else {"answer": f"{self.name}:{model}"}
        if isinstance(step, Exception):
            raise step
        return RawResponse(step if isinstance(step, (dict, str)) else step, 10, 5)


def make(tmp_path, gemini=None, claude=None, **kw):
    s = Settings(gemini_api_key="g", anthropic_api_key="c", gemini_model_fast="g-fast", gemini_model_main="g-main",
                 claude_model="c-model", llm_mode=kw.pop("mode", "live"), claude_max_calls_per_run=kw.pop("cap", 15),
                 gemini_rpm=6000, claude_rpm=6000, **kw)
    providers = {"gemini": gemini or FakeProvider("gemini"), "claude": claude or FakeProvider("claude")}
    sleeps = []
    return Router(s, providers=providers, db_path=tmp_path / "llm.sqlite", sleep=sleeps.append), providers, sleeps


@pytest.mark.parametrize("role,expected", [
    ("prospector", "gemini:g-fast"), ("verifier", "gemini:g-fast"), ("parser", "gemini:g-fast"),
    ("researcher", "gemini:g-main"), ("writer", "claude:c-model"), ("followup_draft", "claude:c-model"),
])
def test_router_picks_provider_per_role(tmp_path, role, expected):
    router, _, _ = make(tmp_path)
    res = router.session("r").call(role, "sys", f"user-{role}", Out)
    assert res.meta.produced_by == expected and res.output.answer == expected


def test_researcher_can_opt_into_claude(tmp_path):
    router, _, _ = make(tmp_path, researcher_provider="claude")
    assert router.session("r").call("researcher", "s", "u", Out).meta.produced_by == "claude:c-model"


@pytest.mark.parametrize("err", [ProviderError("error", "500"), ProviderError("no_credit", "credit balance too low"),
                                 ProviderError("refusal", "declined")])
def test_claude_failure_falls_back_to_gemini_and_is_tagged(tmp_path, err):
    router, prov, _ = make(tmp_path, claude=FakeProvider("claude", [err]))
    res = router.session("r").call("writer", "s", "u", Out)
    assert res.meta.produced_by == "gemini:g-main"
    assert res.meta.fallback_from.startswith("claude:c-model (")
    assert res.output.answer == "gemini:g-main"


def test_no_credit_disables_claude_for_later_calls(tmp_path):
    router, prov, _ = make(tmp_path, claude=FakeProvider("claude", [ProviderError("no_credit", "x")]))
    router.session("r").call("writer", "s", "u1", Out)
    res = router.session("r2").call("writer", "s", "u2", Out)
    assert len(prov["claude"].calls) == 1 and "no_credit" in res.meta.fallback_from


def test_cache_hit_makes_zero_api_calls(tmp_path):
    router, prov, _ = make(tmp_path)
    first = router.session("a").call("prospector", "s", "same", Out)
    second = router.session("b").call("prospector", "s", "same", Out)
    assert len(prov["gemini"].calls) == 1
    assert second.meta.cached and second.output == first.output
    assert router.store.used_today("gemini")["calls"] == 1


def test_cached_mode_replays_only(tmp_path):
    router, prov, _ = make(tmp_path)
    router.session("a").call("writer", "s", "u", Out)  # populate cache (live)
    replay, prov2, _ = make(tmp_path, mode="cached")
    hit = replay.session("b").call("writer", "s", "u", Out)
    miss = replay.session("b").call("writer", "s", "other", Out)
    assert hit.meta.cached and miss is None
    assert prov2["gemini"].calls == [] and prov2["claude"].calls == []


def test_429_triggers_backoff_not_crash(tmp_path):
    g = FakeProvider("gemini", [ProviderError("rate_limit", "429"), ProviderError("rate_limit", "429"), {"answer": "ok"}])
    router, _, sleeps = make(tmp_path, gemini=g)
    res = router.session("r").call("prospector", "s", "u", Out)
    assert res.output.answer == "ok"
    assert [x for x in sleeps if x >= 1] == [2.0, 4.0]  # exponential backoff (ignore rate-limiter spacing)


def test_429_exhausted_returns_none_without_raising(tmp_path):
    g = FakeProvider("gemini", [ProviderError("rate_limit", "429")] * 10)
    router, _, _ = make(tmp_path, gemini=g, max_429_retries=2)
    assert router.session("r").call("prospector", "s", "u", Out) is None
    assert len(g.calls) == 3


def test_claude_max_calls_per_run_is_enforced(tmp_path):
    router, prov, _ = make(tmp_path, cap=2)
    s = router.session("run1")
    tags = [s.call("writer", "s", f"u{i}", Out).meta.produced_by for i in range(4)]
    assert tags == ["claude:c-model", "claude:c-model", "gemini:g-main", "gemini:g-main"]
    assert len(prov["claude"].calls) == 2
    assert "CLAUDE_MAX_CALLS_PER_RUN=2" in s.calls[-1].fallback_from
    # a new run gets a fresh cap
    assert router.session("run2").call("writer", "s", "new", Out).meta.produced_by == "claude:c-model"


def test_gemini_only_never_touches_claude(tmp_path):
    router, prov, _ = make(tmp_path, mode="gemini_only")
    res = router.session("r").call("writer", "s", "u", Out)
    assert res.meta.produced_by == "gemini:g-main" and prov["claude"].calls == []


def test_invalid_json_retried_once_then_fallback(tmp_path):
    c = FakeProvider("claude", ["not json", "```json\n{\"answer\": \"fixed\"}\n```"])
    router, _, _ = make(tmp_path, claude=c)
    assert router.session("r").call("writer", "s", "u", Out).output.answer == "fixed"
    c2 = FakeProvider("claude", ["bad", "still bad"])
    router2, _, _ = make(tmp_path / "x", claude=c2)
    res = router2.session("r").call("writer", "s", "u", Out)
    assert res.meta.produced_by == "gemini:g-main" and "invalid_json" in res.meta.fallback_from


def test_daily_limit_counts_and_status(tmp_path):
    router, prov, _ = make(tmp_path, gemini_daily_limit=1)
    assert router.session("r").call("prospector", "s", "u1", Out) is not None
    assert router.session("r").call("prospector", "s", "u2", Out) is None
    st = router.status()["providers"]["gemini"]
    assert st["used_today"] == 1 and st["remaining_today"] == 0 and st["skipped_reason"] == "daily limit reached"


def test_503_with_fallback_switches_at_once_without_waiting(tmp_path):
    g = FakeProvider("gemini", [ProviderError("overloaded", "503"), {"answer": "from-fast"}])
    router, _, sleeps = make(tmp_path, gemini=g)
    res = router.session("r").call("researcher", "s", "u", Out)
    assert res.output.answer == "from-fast" and res.meta.produced_by == "gemini:g-fast"
    assert [m for m, _ in g.calls] == ["g-main", "g-fast"]
    assert [x for x in sleeps if x >= 1] == []  # no backoff when another model can answer


def test_503_on_last_model_backs_off_once_then_main_failure_falls_to_fast(tmp_path):
    g = FakeProvider("gemini", [ProviderError("overloaded", "503"), {"answer": "after-backoff"}])
    router, _, sleeps = make(tmp_path, gemini=g)
    assert router.session("r").call("prospector", "s", "u", Out).output.answer == "after-backoff"  # FAST only
    assert [x for x in sleeps if x >= 1] == [2.0]

    class MainDown(FakeProvider):
        def generate(self, model, *a):
            if model == "g-main":
                raise ProviderError("error", "500 boom")
            return super().generate(model, *a)

    router2, _, _ = make(tmp_path / "b", gemini=MainDown("gemini"), mode="gemini_only")
    res = router2.session("r").call("writer", "s", "u", Out)
    assert res.meta.produced_by == "gemini:g-fast" and "gemini:g-main (error)" in res.meta.fallback_from


def test_overloaded_model_cools_down_and_is_skipped(tmp_path):
    class MainDown(FakeProvider):
        def generate(self, model, *a):
            self.calls.append((model, a[1]))
            if model == "g-main":
                raise ProviderError("overloaded", "503")
            return RawResponse({"answer": model}, 1, 1)

    g = MainDown("gemini")
    router, _, sleeps = make(tmp_path, gemini=g)
    assert router.session("r").call("researcher", "s", "u1", Out).meta.produced_by == "gemini:g-fast"
    assert [x for x in sleeps if x >= 1] == []  # fell back at once, no backoff
    res = router.session("r").call("researcher", "s", "u2", Out)
    assert res.meta.produced_by == "gemini:g-fast" and "cooling down" in res.meta.fallback_from
    assert [m for m, _ in g.calls].count("g-main") == 1  # not retried while cooling down


def test_429_with_fallback_cools_down_for_retry_delay(tmp_path):
    g = FakeProvider("gemini", [ProviderError("rate_limit", "quota", retry_after=37.0)])
    router, _, sleeps = make(tmp_path, gemini=g)
    res = router.session("r").call("researcher", "s", "u", Out)
    assert res.meta.produced_by == "gemini:g-fast" and "rate_limit" in res.meta.fallback_from
    assert [x for x in sleeps if x >= 1] == []
    assert 30 < router.cooldown["gemini:g-main"] - time.monotonic() <= 37
    res2 = router.session("r").call("researcher", "s", "u2", Out)
    assert "cooling down" in res2.meta.fallback_from


def test_backup_models_are_tried_after_main_and_after_fast_for_fast_roles(tmp_path):
    router, _, _ = make(tmp_path, gemini_model_backups=["g-b1", "g-main", "g-b2"])
    s = router.settings
    assert [s.model_for(sl) for _, sl in s.routes("researcher")] == ["g-main", "g-b1", "g-b2", "g-fast"]
    assert [s.model_for(sl) for _, sl in s.routes("writer")] == ["c-model", "g-main", "g-b1", "g-b2", "g-fast"]
    assert [s.model_for(sl) for _, sl in s.routes("prospector")] == ["g-fast", "g-b1", "g-b2"]


def test_quota_exhausted_main_falls_to_backup_not_fast(tmp_path):
    class MainOut(FakeProvider):
        def generate(self, model, *a):
            if model == "g-main":
                raise ProviderError("rate_limit", "per day", retry_after=44947.0)
            return super().generate(model, *a)

    router, _, sleeps = make(tmp_path, gemini=MainOut("gemini"), gemini_model_backups=["g-b1"])
    res = router.session("r").call("researcher", "s", "u", Out)
    assert res.meta.produced_by == "gemini:g-b1" and sleeps == []
    assert router.status()["providers"]["gemini"]["cooling_down"]["g-main"] > 44000


def test_rate_limiter_allows_burst_then_waits_for_window():
    now = [0.0]
    sleeps = []
    lim = RateLimiter(3, sleep=sleeps.append, clock=lambda: now[0])
    for _ in range(3):
        lim.wait()
    assert sleeps == []  # a burst of rpm calls goes out immediately
    now[0] = 10.0
    lim.wait()
    assert sleeps == [50.0]  # the 4th waits until the first leaves the 60 s window


class SlowMain(FakeProvider):
    def generate(self, model, *a):
        if model == "g-main":
            time.sleep(1.0)
        return super().generate(model, *a)


def test_slow_model_is_hedged_and_first_answer_wins(tmp_path):
    g = SlowMain("gemini")
    router, _, _ = make(tmp_path, gemini=g, llm_hedge_after_s=0.2)
    t = time.monotonic()
    res = router.session("r").call("researcher", "s", "u", Out)
    assert time.monotonic() - t < 0.9  # did not wait for the slow model
    assert res.meta.produced_by == "gemini:g-fast" and "g-main (slower; hedged)" in res.meta.fallback_from


def test_hedging_off_waits_for_the_first_model(tmp_path):
    router, _, _ = make(tmp_path, gemini=SlowMain("gemini"), llm_hedge_after_s=0)
    assert router.session("r").call("researcher", "s", "u", Out).meta.produced_by == "gemini:g-main"


def test_late_hedged_answer_is_cached_and_marked_unused(tmp_path):
    g = SlowMain("gemini")
    router, _, _ = make(tmp_path, gemini=g, llm_hedge_after_s=0.2)
    session = router.session("r")
    session.call("researcher", "s", "u", Out)
    time.sleep(1.2)  # let the slow call finish in the background
    outcomes = [c["outcome"] for c in session.call_log() if c["model"] == "g-main"]
    assert outcomes == ["ok (hedged; answer not used)"]
