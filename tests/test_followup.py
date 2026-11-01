from datetime import date, datetime, timedelta, timezone

import pytest

from fundbridge.agents.context import Context
from fundbridge.agents.followup import FollowUpTracker, find_date, new_event, parse_promise
from fundbridge.config import Settings
from fundbridge.llm import Router
from tests_offline import OFFLINE_DB
from fundbridge.models import NGOProfile
from fundbridge.stores.memory_local import LocalMemoryStore

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
NGO = NGOProfile(ngo_id="n1", name="Shiksha Setu", cause="education", state="Rajasthan", funding_ask_inr=1e6)


def ev(type_, days_ago, **kw):
    return new_event("n1", "C1", type_, NOW - timedelta(days=days_ago), company_name="Co One", **kw)


def tracker():
    return FollowUpTracker(now=NOW)


def test_nothing_sent():
    assert tracker().next_step([ev("drafted", 3)]).action == "send_first_email"


@pytest.mark.parametrize("days,action,overdue", [(3, "wait", False), (6, "wait", False), (7, "nudge", True),
                                                  (13, "nudge", True), (14, "new_angle_or_other_company", True),
                                                  (30, "new_angle_or_other_company", True)])
def test_no_reply_rules(days, action, overdue):
    s = tracker().next_step([ev("sent", days)])
    assert (s.action, s.overdue) == (action, overdue)
    assert s.cited_event_ids


def test_reply_without_promise():
    s = tracker().next_step([ev("sent", 10), ev("reply_received", 2, text="Thanks, noted.")])
    assert s.action == "reply_to_response" and not s.overdue


def test_promise_with_future_date_waits_then_reminds():
    sent, reply = ev("sent", 10), ev("reply_received", 3, text="x")
    p = ev("promise_made", 3, promise_what="review proposal", promise_by=(NOW + timedelta(days=4)).date().isoformat())
    s = tracker().next_step([sent, reply, p])
    assert s.action == "wait" and s.due == p.promise_by and p.event_id in s.cited_event_ids
    p_past = ev("promise_made", 3, promise_what="review proposal", promise_by=(NOW - timedelta(days=1)).date().isoformat())
    s2 = tracker().next_step([sent, reply, p_past])
    assert s2.action == "promise_reminder" and s2.overdue


def test_find_date_and_promise_parsing():
    ref = date(2026, 10, 28)
    assert find_date("we will revert by 15 November", ref)[0] == date(2026, 11, 15)
    assert find_date("by Nov 5th, 2026", ref)[0] == date(2026, 11, 5)
    assert find_date("on 2026-12-01", ref)[0] == date(2026, 12, 1)
    assert find_date("we will get back next week", ref) is None  # no guessing
    what, by, _ = parse_promise("Thank you. Our committee will review your proposal by 12 November. Regards.", ref)
    assert "review your proposal" in what and by == "2026-11-12"
    what, by, _ = parse_promise("We will get back to you soon.", ref)
    assert by is None


def test_persistence_across_restart(tmp_path):
    """Simulates a server restart: a fresh store instance on the same file sees the history."""
    db = tmp_path / "memory.sqlite"
    ctx = Context(ngo=NGO, csr=None, memory=LocalMemoryStore(db), llm=Router(Settings(gemini_api_key="", anthropic_api_key=""), db_path=OFFLINE_DB).session("test"), now=NOW)
    t = tracker()
    t.record(ctx, ev("sent", 9))
    t.record(ctx, ev("reply_received", 2, text="We will share feedback by 20 November 2026."))
    del ctx

    ctx2 = Context(ngo=NGO, csr=None, memory=LocalMemoryStore(db), llm=Router(Settings(gemini_api_key="", anthropic_api_key=""), db_path=OFFLINE_DB).session("test"), now=NOW)
    history, step = t.plan(ctx2, "C1", draft=True)
    assert [e.type for e in history] == ["sent", "reply_received", "promise_made"]
    assert history[-1].promise_by == "2026-11-20"
    assert step.action == "wait" and step.due == "2026-11-20"
