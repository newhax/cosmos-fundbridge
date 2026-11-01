"""Geography backbone: LGD loading, versioning, aliases, buckets, queue (spec tests 1-3)."""
import csv

import pytest

from fundbridge import geo
from fundbridge.geo import ALL_INDIA, MULTIPLE, NOT_SPECIFIED, Geo, key

RAW = geo.GEO_RAW_DIR
pytestmark = pytest.mark.skipif(not list(RAW.glob("*district*.csv")), reason="run scripts/geo.py fetch first")


@pytest.fixture()
def g(tmp_path):
    geo.load(db_path=tmp_path / "geo.sqlite")
    return Geo(tmp_path / "geo.sqlite")


def test_every_state_and_ut_comes_from_the_file(g):
    rows = list(csv.DictReader((sorted(RAW.glob("*state*.csv"))[0]).open(encoding="utf-8-sig")))
    loaded = {s["lgd_code"]: s for s in g.states(include_inactive=True)}
    assert len(loaded) == len(rows)  # nothing hardcoded: one entry per row in the file
    assert {geo.code(r["state_code"]) for r in rows} == set(loaded)
    districts = list(csv.DictReader((sorted(p for p in RAW.glob("*district*.csv") if "sub" not in p.name)[0]).open(encoding="utf-8-sig")))
    assert len(g.districts()) == len(districts)
    for s in g.states():  # every active state/UT has districts and a kind
        assert g.districts(s["lgd_code"]) and s["kind"] in ("state", "ut")
    info = g.info()
    assert info["source"] and info["files"][0]["sha256"] and info["files"][0].get("url")


def test_merged_legacy_entries_are_flagged_and_followed(g):
    merged = [s for s in g.states(include_inactive=True) if s["status"] == "merged"]
    assert merged and all(s["merged_into"] for s in merged)
    m = g.resolve_state("Daman and Diu")
    assert m.quality == "legacy_mapped" and m.code == merged[0]["merged_into"] and "merged into" in m.note


def test_aliases_variants_old_names_and_abbreviations(g):
    assert g.resolve_state("Orissa").name == "Odisha"
    assert g.resolve_state("J&K").name == "Jammu and Kashmir"
    assert g.resolve_state("  tamil   NADU ").name == "Tamil Nadu"
    up = g.resolve_state("Uttar Pradesh").code
    m = g.resolve_district("Allahabad", up)
    assert m.name == "Prayagraj" and m.quality == "legacy_mapped"  # renamed: mapped AND flagged
    assert g.resolve_district("Prayagraj", up).quality == "ok"


def test_district_split_keeps_state_and_flags(g):
    wb = g.resolve_state("West Bengal").code
    m = g.resolve_district("Bardhaman", wb)
    assert m.code is None and m.quality == "split" and "split" in m.note


def test_districts_resolve_only_inside_their_state(g):
    hp, cg = g.resolve_state("Himachal Pradesh").code, g.resolve_state("Chhattisgarh").code
    a, b = g.resolve_district("Bilaspur", hp), g.resolve_district("Bilaspur", cg)
    assert a.code and b.code and a.code != b.code  # same name, two states
    raj = g.resolve_state("Rajasthan").code
    assert g.resolve_district("Bilaspur", raj).quality == "unmapped"  # never borrowed from another state


def test_buckets_are_never_reassigned(g):
    assert g.resolve_state("PAN India").code == ALL_INDIA
    assert g.resolve_state("All India", "00").code == ALL_INDIA
    assert g.resolve_state("Not Mentioned", "99").code == NOT_SPECIFIED
    assert g.resolve_state("").code == NOT_SPECIFIED
    assert g.resolve_state("Rajasthan, Gujarat").code == MULTIPLE


def test_low_confidence_names_go_to_the_queue_not_a_guess(g):
    m = g.resolve_state("Rajastan")  # a typo is NOT fuzzy-matched
    assert m.code is None and m.quality == "unmapped"
    g.record_unmapped("Rajastan", "state", context="test")
    g.record_unmapped("rajastan", "state", context="test")
    q = g.unmapped()
    assert len(q) == 1 and q[0]["count"] == 2
    raj = g.resolve_state("Rajasthan").code
    g.resolve_unmapped(q[0]["id"], raj, by="admin@test")
    assert Geo(g.db_path).resolve_state("Rajastan").code == raj  # an admin decision becomes an alias
    assert g.unmapped() == []


def test_code_and_name_conflict_is_flagged_not_reassigned(g):
    guj = g.resolve_state("Gujarat").code
    ahd = g.resolve_district("Ahmadabad", guj).code
    m = g.resolve_district("Prayagraj", guj, lgd_code=ahd)  # a real pattern in the MCA export
    assert m.code is None and m.quality == "conflict"
    assert g.resolve_state("Kerala", lgd_code=g.resolve_state("Goa").code).quality == "conflict"


def test_new_load_is_a_new_version_and_old_one_is_kept(g, tmp_path):
    v1 = g.version
    geo.load(db_path=g.db_path)
    assert Geo(g.db_path).version == v1 + 1 and Geo(g.db_path, version=v1).states()
    assert key("The Dadra & Nagar-Haveli District") == "dadra and nagar haveli"
