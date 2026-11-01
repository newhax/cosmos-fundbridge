"""Seed the demo NGO ("education in rural Rajasthan"): run the pipeline, draft
outreach for the top two companies, and add SIMULATED follow-up history:

  #1 company: email marked sent 9 days ago, no reply           -> overdue nudge
  #2 company: sent 12 days ago, replied 4 days ago with a dated
              promise ("committee will review ... by <date>")  -> wait / reminder

All seeded events carry simulated=true and are labelled in the UI.

Usage: python scripts/seed_demo.py [--reset]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fundbridge.agents.context import Context  # noqa: E402
from fundbridge.agents.followup import FollowUpTracker, new_event  # noqa: E402
from fundbridge.agents.writer import Writer  # noqa: E402
from fundbridge.appstate import AppState  # noqa: E402
from fundbridge.config import MEMORY_DB_PATH, settings  # noqa: E402
from fundbridge.llm import Router  # noqa: E402
from fundbridge.models import NGOProfile, RankedFunder  # noqa: E402
from fundbridge.pipeline import run_pipeline  # noqa: E402
from fundbridge.stores import build_backends  # noqa: E402

DEMO = NGOProfile(
    ngo_id="shiksha-setu-demo",
    name="Shiksha Setu Foundation",
    cause="Education for children in rural Rajasthan",
    state="Rajasthan",
    district="Barmer",
    project_summary="We run community libraries and after-school reading circles in 12 village government schools "
                    "in Barmer, with trained local women as reading mentors.",
    funding_ask_inr=1_500_000,
)


def reset_local() -> None:
    if MEMORY_DB_PATH.exists():
        with sqlite3.connect(MEMORY_DB_PATH) as con:
            con.execute("DELETE FROM events WHERE ngo_id = ?", (DEMO.ngo_id,))
            con.execute("DELETE FROM traces WHERE ngo_id = ?", (DEMO.ngo_id,))
    app = AppState()
    with app._con() as con:
        for t in ("ngos", "runs", "outreach"):
            con.execute(f"DELETE FROM {t} WHERE ngo_id = ?", (DEMO.ngo_id,))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="delete existing demo NGO state first (local stores)")
    args = ap.parse_args()
    if args.reset:
        reset_local()

    backends = build_backends()
    router, app = Router(settings), AppState()
    for w in backends.warnings + router.warnings():
        print("WARNING:", w)
    if backends.memory.recall(DEMO.ngo_id) and not args.reset:
        print("Demo NGO already seeded (use --reset to re-seed).")
        return

    run = run_pipeline(DEMO, backends, router, app)
    print(f"Pipeline: {len(run['ranked'])} ranked funders in {run['duration_s']}s (sectors: {run['sectors']})")
    ctx = Context(ngo=DEMO, csr=backends.csr, memory=backends.memory, llm=router.session("seed"))
    tracker = FollowUpTracker()
    now = datetime.now(timezone.utc)
    names = [r["company_name"] for r in run["ranked"]]

    for idx, (sent_days, reply) in enumerate([
        (9, None),
        (12, ("Thank you for reaching out. Our CSR committee will review your proposal and revert by "
              f"{(now + timedelta(days=6)).strftime('%d %B %Y')}. Please share your latest audited accounts.", 4)),
    ]):
        r = RankedFunder(**run["ranked"][idx])
        pkg = Writer().run(ctx, r, run["sectors"], [n for n in names if n != r.company_name]).model_dump()
        pkg["approved"] = True
        app.save_outreach(DEMO.ngo_id, r.company_id, pkg)
        draft = pkg["drafts"][0]
        common = dict(company_name=r.company_name, simulated=True)
        tracker.record(ctx, new_event(DEMO.ngo_id, r.company_id, "drafted", now - timedelta(days=sent_days, hours=2),
                                      text="Outreach email drafted (formal + warm).", **common))
        tracker.record(ctx, new_event(DEMO.ngo_id, r.company_id, "sent", now - timedelta(days=sent_days),
                                      text=f"Subject: {draft['subject']}\n\n{draft['body']}", **common))
        if reply:
            text, days_ago = reply
            tracker.record(ctx, new_event(DEMO.ngo_id, r.company_id, "reply_received", now - timedelta(days=days_ago),
                                          text=text, **common))
        _, step = tracker.plan(ctx, r.company_id)
        print(f"  {r.company_name}: next step = {step.action} (due {step.due}, overdue={step.overdue})")
    print("Seeded. Open the app and pick 'Shiksha Setu Foundation'.")


if __name__ == "__main__":
    main()
