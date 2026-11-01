"""Seed DEMO accounts for the two-sided platform (idempotent).

  companies:  3 DEMO companies (fictional names, CINs that join to the SYNTHETIC filings)
  NGOs:       3 DEMO NGOs (two opted in to the directory, one not)
  thread:     Shiksha Setu -> Thar Minerals, sent 10 days ago, viewed, screened, company replied
              "interested" with a promise (amount + due date)

Every demo account and profile is flagged is_demo and labelled DEMO in the UI.
Usage: python scripts/seed_platform.py [--reset]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fundbridge.agents.screener import screen  # noqa: E402
from fundbridge.config import PLATFORM_DB_PATH, settings  # noqa: E402
from fundbridge.platform.service import Platform  # noqa: E402
from fundbridge.stores import build_backends  # noqa: E402

DEMO_DOMAIN = "demo.fundbridge.local"
COMPANIES = [
    dict(email=f"csr@thar-minerals.{DEMO_DOMAIN}", name="Thar Minerals (DEMO)", cin="SYN0000000000001",
         csr_contact_email=f"csr@thar-minerals.{DEMO_DOMAIN}", focus_sectors=["education", "health", "rural development"],
         focus_states=["Rajasthan", "Gujarat"], min_ticket=500_000, max_ticket=6_000_000),
    dict(email=f"csr@marudhar-power.{DEMO_DOMAIN}", name="Marudhar Power (DEMO)", cin="SYN0000000000003",
         csr_contact_email=f"csr@marudhar-power.{DEMO_DOMAIN}", focus_sectors=["environment", "water and sanitation"],
         focus_states=["Rajasthan"], min_ticket=1_000_000, max_ticket=10_000_000),
    dict(email=f"csr@sahyadri-textiles.{DEMO_DOMAIN}", name="Sahyadri Textiles (DEMO)", cin="SYN0000000000004",
         csr_contact_email=f"csr@sahyadri-textiles.{DEMO_DOMAIN}", focus_sectors=["women empowerment", "education"],
         focus_states=["Maharashtra"], min_ticket=300_000, max_ticket=3_000_000),
]
NGOS = [
    dict(email=f"team@shiksha-setu.{DEMO_DOMAIN}", ngo_id="shiksha-setu-demo", name="Shiksha Setu Foundation (DEMO)",
         state="Rajasthan", district="Barmer", causes=["education"], crn="CSR00012345", darpan_id="RJ/2019/0234567",
         has_12a=True, has_80g=False, listed_publicly=True,
         summary="Community libraries and after-school reading circles in 12 village government schools in Barmer.",
         listed_fields=["state", "district", "causes", "summary", "credentials"],
         accepts_individual_donations=True, donation_url="https://example.org/donate/shiksha-setu-demo",
         impact_updates_enabled=True, founded_year=2016, suggested_gift_inr=2000),
    dict(email=f"team@hara-bhara.{DEMO_DOMAIN}", name="Hara Bhara Trust (DEMO)", state="Rajasthan", district="Jodhpur",
         causes=["environment", "water and sanitation"], crn="", darpan_id="RJ/2021/0456789", has_12a=True, has_80g=True,
         listed_publicly=True, summary="Village water harvesting and tree nurseries run by local youth.",
         listed_fields=["state", "district", "causes", "summary"],
         accepts_individual_donations=True, donation_url="https://example.org/donate/hara-bhara-demo",
         impact_updates_enabled=True, founded_year=2019, suggested_gift_inr=1000),
    dict(email=f"team@sakhi-collective.{DEMO_DOMAIN}", name="Sakhi Collective (DEMO)", state="Maharashtra",
         district="Pune", causes=["women empowerment", "livelihood"], crn="", darpan_id="", has_12a=False,
         has_80g=False, listed_publicly=False, summary="Tailoring and bookkeeping skills for women's self-help groups.",
         accepts_individual_donations=True, donation_url="https://example.org/donate/sakhi-demo"),
]

def _extra(slug, name, state, district, causes, summary, founded, gift=None, credentials=False):
    """A fictional DEMO NGO listed in the directory. No registration IDs are made up: only the 12A/80G
    yes/no flags (self-declared) are set for some, so nothing looks like a real record."""
    ngo = dict(email=f"team@{slug}.{DEMO_DOMAIN}", name=f"{name} (DEMO)", state=state, district=district, causes=causes,
               summary=summary, listed_publicly=True, founded_year=founded, crn="", darpan_id="",
               has_12a=credentials, has_80g=credentials,
               listed_fields=["state", "district", "causes", "summary"] + (["credentials"] if credentials else []))
    if gift:
        ngo.update(accepts_individual_donations=True, donation_url=f"https://example.org/donate/{slug}-demo",
                   impact_updates_enabled=True, suggested_gift_inr=gift)
    return ngo


# More listed DEMO NGOs across states and causes, so company and donor recommendations have real choices.
# Added by email on every seed run (existing ones are left alone).
EXTRA_NGOS = [
    _extra("vidya-jyoti", "Vidya Jyoti Trust", "Rajasthan", "Jaisalmer", ["education", "child welfare"],
           "Bridge classes and school-readiness camps for children of migrant herding families.", 2014, 1500, True),
    _extra("swasthya-saathi", "Swasthya Saathi", "Gujarat", "Kutch", ["health", "rural development"],
           "Mobile health camps and trained village health volunteers in remote talukas.", 2012, 2500, True),
    _extra("jal-mitra", "Jal Mitra Sangathan", "Gujarat", "Banaskantha", ["water and sanitation", "environment"],
           "Check dams, farm ponds and household toilets built with village water committees.", 2017, None, True),
    _extra("udaan-mahila", "Udaan Mahila Manch", "Maharashtra", "Nashik", ["women empowerment", "livelihood"],
           "Digital literacy and micro-enterprise training for rural women's self-help groups.", 2015, 1000),
    _extra("annapurna-rasoi", "Annapurna Rasoi", "Maharashtra", "Mumbai Suburban", ["hunger and poverty", "child welfare"],
           "Community kitchens serving nutritious midday meals near urban slums.", 2011, 500, True),
    _extra("hasta-kala", "Hasta Kala Kendra", "Rajasthan", "Udaipur", ["livelihood", "sports and culture"],
           "Craft cooperatives that help tribal artisans reach fair-price markets.", 2018, None),
    _extra("saksham-seva", "Saksham Seva Samiti", "Karnataka", "Mysuru", ["disability and elderly", "health"],
           "Physiotherapy and assistive devices for elderly people and children with disabilities.", 2013, 2000, True),
    _extra("green-ganga", "Green Ganga Foundation", "Uttar Pradesh", "Varanasi", ["environment", "water and sanitation"],
           "River-bank clean-ups, waste segregation and native tree planting with schools.", 2016, 1000),
    _extra("aapda-raksha", "Aapda Raksha Network", "Odisha", "Puri", ["disaster relief", "rural development"],
           "Cyclone preparedness drills and relief kits stocked with coastal village volunteers.", 2010, None, True),
]

DONORS = [
    dict(email=f"ananya@donor.{DEMO_DOMAIN}", display_name="Ananya (DEMO)", state="Rajasthan", city="Jaipur",
         causes=["education", "women empowerment"], giving_range="1000_5000", frequency="monthly",
         anonymous_by_default=False),
    dict(email=f"rohan@donor.{DEMO_DOMAIN}", display_name="Rohan (DEMO)", state="Maharashtra", city="Pune",
         causes=["environment", "water and sanitation"], giving_range="under_1000", frequency="one_time",
         anonymous_by_default=True),
]


def demo_password() -> str:
    pw = os.getenv("DEMO_PASSWORD", "").strip()
    if pw:
        return pw
    if not settings.demo_mode:
        sys.exit("Set DEMO_PASSWORD in .env to seed demo accounts outside DEMO_MODE.")
    return "demo-pass-2026"


def reset(platform: Platform) -> None:
    with platform.db.tx() as con:
        demo_users = [r["id"] for r in con.execute("SELECT id FROM users WHERE email LIKE ?", (f"%{DEMO_DOMAIN}",))]
        for uid in demo_users:
            ngo = con.execute("SELECT ngo_id FROM ngo_profiles WHERE user_id = ?", (uid,)).fetchone()
            comp = con.execute("SELECT company_id FROM company_profiles WHERE user_id = ?", (uid,)).fetchone()
            key, val = ("ngo_id", ngo["ngo_id"]) if ngo else ("company_id", comp["company_id"]) if comp else (None, None)
            if key:
                for rid in [r["id"] for r in con.execute(f"SELECT id FROM project_requests WHERE {key} = ?", (val,))]:
                    con.execute("DELETE FROM project_updates WHERE request_id = ?", (rid,))
                    con.execute("DELETE FROM project_requests WHERE id = ?", (rid,))
                if key == "ngo_id":
                    for pid in [r["id"] for r in con.execute("SELECT id FROM projects WHERE ngo_id = ?", (val,))]:
                        for rid in [r["id"] for r in con.execute("SELECT id FROM project_requests WHERE project_id = ?", (pid,))]:
                            con.execute("DELETE FROM project_updates WHERE request_id = ?", (rid,))
                        con.execute("DELETE FROM project_requests WHERE project_id = ?", (pid,))
                        con.execute("DELETE FROM areas WHERE entity_type = 'project' AND entity_id = ?", (pid,))
                    con.execute("DELETE FROM projects WHERE ngo_id = ?", (val,))
                else:
                    for cid in [r["id"] for r in con.execute("SELECT id FROM calls WHERE company_id = ?", (val,))]:
                        con.execute("DELETE FROM areas WHERE entity_type = 'call' AND entity_id = ?", (cid,))
                    con.execute("DELETE FROM calls WHERE company_id = ?", (val,))
                con.execute("DELETE FROM areas WHERE entity_type = ? AND entity_id = ?", (key.split("_")[0], val))
                for pid in [r["id"] for r in con.execute(f"SELECT id FROM pitches WHERE {key} = ?", (val,))]:
                    con.execute("DELETE FROM pitch_events WHERE pitch_id = ?", (pid,))
                    con.execute("DELETE FROM pitches WHERE id = ?", (pid,))
                con.execute(f"DELETE FROM blocks WHERE {key} = ?", (val,))
                con.execute(f"DELETE FROM reports WHERE {key} = ?", (val,))
            don = con.execute("SELECT donor_id FROM donor_profiles WHERE user_id = ?", (uid,)).fetchone()
            if don:
                for t in ("donor_shortlist", "donor_follows", "donor_gifts"):
                    con.execute(f"DELETE FROM {t} WHERE donor_id = ?", (don["donor_id"],))
                con.execute("DELETE FROM donor_profiles WHERE user_id = ?", (uid,))
            if ngo:
                for t in ("ngo_updates", "ngo_reports", "donor_follows", "donor_shortlist", "donor_gifts"):
                    con.execute(f"DELETE FROM {t} WHERE ngo_id = ?", (ngo["ngo_id"],))
            con.execute("DELETE FROM ngo_profiles WHERE user_id = ?", (uid,))
            con.execute("DELETE FROM company_profiles WHERE user_id = ?", (uid,))
            con.execute("DELETE FROM users WHERE id = ?", (uid,))


def ensure_extra_ngos(platform: Platform, pw: str) -> int:
    added = 0
    for n in EXTRA_NGOS:
        if platform.db.one("SELECT id FROM users WHERE email = ?", (n["email"],)):
            continue
        n = dict(n)
        user = platform.auth.create_user(n.pop("email"), pw, "ngo", is_demo=True, verified=True)
        platform.create_ngo(user["id"], n.pop("name"), is_demo=True, **n)
        added += 1
    return added


ADMINS = [  # scoped regional admins (DEMO only); real admins are created with scripts/admin.py
    dict(email=f"admin@{DEMO_DOMAIN}", states=[], languages=[]),
    dict(email=f"admin-rj@{DEMO_DOMAIN}", states=["Rajasthan"], languages=["hi"]),
]


def ensure_admins(platform: Platform, pw: str) -> int:
    if not settings.demo_mode:
        return 0
    n = 0
    for a in ADMINS:
        if platform.db.one("SELECT id FROM users WHERE email = ?", (a["email"],)):
            continue
        codes = [platform.geo.resolve_state(x).code for x in a["states"]]
        platform.admin.create(a["email"], pw, states=codes, languages=a["languages"], is_demo=True)
        n += 1
    return n


def ensure_projects(platform: Platform) -> int:
    """DEMO projects, one call for proposals and one accepted (simulated) project with milestones."""
    if platform.db.one("SELECT 1 AS x FROM projects WHERE title LIKE '%(DEMO)%'"):
        return 0
    g, svc = platform.geo, platform.projects
    def ngo(email):
        r = platform.db.one("SELECT n.ngo_id FROM ngo_profiles n JOIN users u ON u.id = n.user_id WHERE u.email = ?", (email,))
        return r["ngo_id"] if r else None
    def comp(email):
        r = platform.db.one("SELECT c.company_id FROM company_profiles c JOIN users u ON u.id = c.user_id WHERE u.email = ?", (email,))
        return r["company_id"] if r else None
    raj, guj, od = (g.resolve_state(x).code for x in ("Rajasthan", "Gujarat", "Odisha"))
    barmer = g.resolve_district("Barmer", raj).code
    shiksha, jal, aapda = ngo(f"team@shiksha-setu.{DEMO_DOMAIN}"), ngo(f"team@jal-mitra.{DEMO_DOMAIN}"), ngo(f"team@aapda-raksha.{DEMO_DOMAIN}")
    thar = comp(f"csr@thar-minerals.{DEMO_DOMAIN}")
    if not (shiksha and thar):
        return 0
    p1 = svc.save_project(shiksha, {
        "title": "Library corners in 12 Barmer schools (DEMO)", "sectors": ["education"], "budget_inr": 1_500_000,
        "summary": "Books, shelves and trained volunteer readers for 12 village government schools in Barmer.",
        "duration_months": 12, "beneficiaries": "About 2,400 children (self-declared)",
        "areas": [{"level": "district", "state": raj, "district": barmer}]})
    n = 1
    if jal:
        svc.save_project(jal, {"title": "Farm ponds for 20 villages (DEMO)", "sectors": ["water and sanitation", "environment"],
                               "budget_inr": 2_400_000, "duration_months": 18,
                               "summary": "Farm ponds and check dams built with village water committees in Banaskantha.",
                               "areas": [{"level": "state", "state": guj}]})
        n += 1
    if aapda:
        svc.save_project(aapda, {"title": "Cyclone-ready coastal villages (DEMO)", "sectors": ["disaster relief"], "budget_inr": 900_000,
                                 "duration_months": 6, "summary": "Drills, early-warning volunteers and stocked relief kits in coastal Odisha.",
                                 "areas": [{"level": "state", "state": od}]})
        n += 1
    svc.create_call(thar, {"title": "School reading programmes in western India (DEMO)", "sectors": ["education"],
                           "description": "We invite proposals for reading and library programmes in government schools.",
                           "budget_min": 500_000, "budget_max": 3_000_000, "deadline": "2099-03-31",
                           "areas": [{"level": "state", "state": raj}, {"level": "state", "state": guj}]})
    req = svc.request_funds(shiksha, p1["id"], thar, 800_000, "Phase 1: six schools. (DEMO, simulated)")
    svc.decide(thar, req["id"], True, "Approved for phase 1 (DEMO, simulated)")
    svc.add_update(req["id"], "company", thar, {"kind": "milestone", "title": "Six library corners set up", "due_date": "2099-01-31"})
    svc.add_update(req["id"], "ngo", shiksha, {"kind": "progress", "title": "Three schools done (simulated)", "percent": 40})
    svc.add_update(req["id"], "ngo", shiksha, {"kind": "utilisation", "title": "Books and shelves (simulated)", "amount_inr": 210_000})
    return n


def main(platform: Platform | None = None, quiet: bool = False) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    args, _ = ap.parse_known_args()
    platform = platform or Platform(PLATFORM_DB_PATH, build_backends().memory, settings)
    if args.reset:
        reset(platform)
    pw = demo_password()
    added = ensure_extra_ngos(platform, pw)
    if added and not quiet:
        print(f"Added {added} more listed DEMO NGOs.")
    if ensure_admins(platform, pw) and not quiet:
        print("Added DEMO admins: " + ", ".join(a["email"] for a in ADMINS))
    if platform.db.one("SELECT id FROM users WHERE email = ?", (COMPANIES[0]["email"],)):
        if not quiet:
            print("Platform demo accounts already seeded (use --reset to re-seed).")
        if ensure_projects(platform) and not quiet:
            print("Added DEMO projects, a call for proposals and one accepted (simulated) project.")
        return

    company_ids = {}
    for c in COMPANIES:
        c = dict(c)
        user = platform.auth.create_user(c.pop("email"), pw, "company", is_demo=True, verified=True)
        cid = platform.create_company(user["id"], c.pop("name"), is_demo=True, **c)
        company_ids[c["cin"]] = cid
    ngo_ids = []
    for n in NGOS:
        n = dict(n)
        user = platform.auth.create_user(n.pop("email"), pw, "ngo", is_demo=True, verified=True)
        ngo_ids.append(platform.create_ngo(user["id"], n.pop("name"), is_demo=True, **n))

    # Donors (minimal data), follows, a self-reported gift and an NGO update
    donor_ids = []
    for d in DONORS:
        d = dict(d)
        user = platform.auth.create_user(d.pop("email"), pw, "donor", is_demo=True, verified=True)
        donor_ids.append(platform.donors.create(user["id"], d.pop("display_name"), is_demo=True, **d))
    platform.donors.post_update(ngo_ids[0], "Two new reading circles started",
                                "This month we started reading circles in two more village schools in Barmer.")
    platform.donors.follow(donor_ids[0], ngo_ids[0], True)            # Ananya: name shared (not anonymous by default)
    platform.donors.shortlist(donor_ids[0], ngo_ids[1], True)
    platform.donors.follow(donor_ids[1], ngo_ids[1], True)            # Rohan: anonymous by default
    gift_day = (datetime.now(timezone.utc) - timedelta(days=5)).date().isoformat()
    platform.donors.report_gift(donor_ids[0], ngo_ids[0], 2000, gift_day, None)

    # One pre-run thread with a dated promise
    now = datetime.now(timezone.utc)
    ts = lambda days: (now - timedelta(days=days)).isoformat(timespec="microseconds")  # noqa: E731
    thar = company_ids["SYN0000000000001"]
    pitch = platform.pitches.create_and_send(
        ngo_ids[0], thar, "Reading circles for 12 village schools in Barmer",
        "Dear CSR Team,\n\nShiksha Setu Foundation runs community libraries and after-school reading circles in 12 "
        "village government schools in Barmer, with trained local women as reading mentors. Public CSR filings show "
        "your company's education work in Rajasthan, including spend in Barmer district. We are seeking ₹15.0 lakh "
        "to run the programme for one school year and would welcome a conversation.\n\nShiksha Setu Foundation",
        [], ask_inr=1_500_000, sectors=["education"], state="Rajasthan", district="Barmer", at=ts(10))
    p = platform.pitches.get_for_company(pitch["id"], thar)
    platform.pitches.add_event(p, "viewed", "company", {"summary": "Opened by the company."}, at=ts(9))
    platform.db.run("UPDATE pitches SET status = 'viewed' WHERE id = ?", (p["id"],))
    result = screen(None, p, platform.ngo_profile(ngo_ids[0]), platform.company_profile(thar))
    platform.pitches.add_event(p, "screened", "system", result | {"summary": f"Fit {result['fit']['total']}/100 (advisory)"}, at=ts(9))
    due = (now + timedelta(days=12)).date().isoformat()
    for ev in platform.pitches.respond(p, "interested",
                                       "Thank you for the proposal. Our CSR committee would like to visit two of the "
                                       "schools and will share a decision on funding after the visit.",
                                       {"what": "Site visit to two schools and a funding decision by the CSR committee",
                                        "amount": 1_000_000, "due_date": due}):
        platform.db.run("UPDATE pitch_events SET created_at = ? WHERE id = ?", (ts(3), ev["id"]))
    if not quiet:
        print("Seeded DEMO platform accounts (password for all demo accounts: "
              f"{'from DEMO_PASSWORD' if os.getenv('DEMO_PASSWORD') else pw}):")
        for c in COMPANIES:
            print(f"  company  {c['email']}")
        for n in NGOS:
            print(f"  ngo      {n['email']}{'' if n['listed_publicly'] else '  (not listed in directory)'}")
        for d in DONORS:
            print(f"  donor    {d['email']}{'  (anonymous by default)' if d['anonymous_by_default'] else ''}")
        print(f"  thread   {pitch['id']}: Shiksha Setu -> Thar Minerals, promise of ₹10.0 lakh decision by {due}")
    if ensure_projects(platform) and not quiet:
        print("  projects DEMO projects, one call for proposals and one accepted (simulated) project")


if __name__ == "__main__":
    main()
