"""Versioned datasets: build + data checks, atomic activation and rollback, provenance, replay on a stored version."""
import csv
import json
import shutil
import sqlite3

import pytest

from fundbridge import datasets
from fundbridge.config import SAMPLE_DIR, Settings
from fundbridge.datasets import DatasetInvalid, DatasetUnavailable
from fundbridge.ingest import validate_database
from fundbridge.stores.csr_base import rows_hash
from fundbridge.stores.csr_dataset import DatasetCSRStore

SAMPLE = sorted(SAMPLE_DIR.glob("*.csv"))
pytestmark = pytest.mark.skipif(not SAMPLE, reason="run scripts/generate_sample.py first")


@pytest.fixture()
def root(tmp_path):
    """A dataset root with the sample loaded as v1 and activated."""
    r = tmp_path / "datasets"
    m = datasets.build_version(SAMPLE, "SAMPLE", root=r)
    datasets.activate(m["version"], r)
    return r


def _smaller_sample(tmp_path, keep_every=2):
    """A different source file: every second data row of the sample (so v2 has different data)."""
    out = tmp_path / "smaller.csv"
    with SAMPLE[0].open(encoding="utf-8") as f, out.open("w", newline="", encoding="utf-8") as g:
        rows = list(csv.reader(f))
        w = csv.writer(g)
        w.writerow(rows[0])
        w.writerows(rows[1::keep_every])
    return out


def _run_pipeline(tmp_path, store):
    from seed_demo import DEMO

    from fundbridge.appstate import AppState
    from fundbridge.llm import Router
    from fundbridge.pipeline import run_pipeline
    from fundbridge.stores import Backends
    from fundbridge.stores.memory_local import LocalMemoryStore

    backends = Backends(csr=store, memory=LocalMemoryStore(tmp_path / "mem.sqlite"), warnings=[])
    router = Router(Settings(gemini_api_key="", anthropic_api_key=""), providers={}, db_path=tmp_path / "llm.sqlite")
    return run_pipeline(DEMO, backends, router, AppState(tmp_path / "app.sqlite")), backends


# -- building a version ------------------------------------------------------------------------------------
def test_build_writes_manifest_source_copy_and_passes_checks(root):
    m = datasets.active(root)
    assert m["dataset_id"] == datasets.DATASET_ID and m["version"] == 1 and m["data_source"] == "SAMPLE"
    assert m["sha256"] == datasets.files_sha256(SAMPLE) and len(m["sha256"]) == 64
    assert m["rows_loaded"] > 0 and len(m["fy_range"]) == 2 and m["checks_passed"]
    assert (root / "v1" / "source" / SAMPLE[0].name).read_bytes() == SAMPLE[0].read_bytes()
    with sqlite3.connect(datasets.db_path(1, root)) as con:
        assert validate_database(con) == []


def test_data_checks_catch_deliberately_broken_rows(root, tmp_path):
    db = tmp_path / "broken.sqlite"
    shutil.copy(datasets.db_path(1, root), db)
    with sqlite3.connect(db) as con:
        first = con.execute("SELECT * FROM clean_projects WHERE company_id NOT LIKE 'NAME:%' LIMIT 1").fetchone()
        cols = [c[1] for c in con.execute("PRAGMA table_info(clean_projects)")]
        row = dict(zip(cols, first))
        top = con.execute("SELECT MAX(row_id) FROM clean_projects").fetchone()[0]
        bad = [
            {**row, "row_id": top + 1, "source_ref": "x#L1|FY1", "amount_inr": -5.0},                 # negative spend
            {**row, "row_id": top + 2, "source_ref": row["source_ref"]},                             # duplicate source_ref
            {**row, "row_id": top + 3, "source_ref": "x#L3|FY1", "state": "Atlantis", "state_lgd": None},  # unknown state
            {**row, "row_id": top + 4, "source_ref": "x#L4|FY1", "cin": " "},                        # blank CIN
        ]
        con.executemany(f"INSERT INTO clean_projects ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [tuple(r[c] for c in cols) for r in bad])
        failed = {f["check"] for f in validate_database(con)}
    assert "clean_projects: amount missing or negative" in failed
    assert "clean_projects: duplicate source_ref" in failed
    assert "clean_projects: single-state row without a valid state" in failed
    assert "clean_projects: blank CIN on a CIN-identified company" in failed


def test_invalid_upload_creates_no_version_and_keeps_active(root, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("foo,bar\n1,2\n", encoding="utf-8")
    with pytest.raises(DatasetInvalid):
        datasets.build_version([bad], "REAL", root=root)
    assert [m["version"] for m in datasets.list_versions(root)] == [1]
    assert datasets.active(root)["version"] == 1
    assert not list(root.glob(".staging-*"))  # no half-written version left behind


# -- activation + rollback -----------------------------------------------------------------------------------
def test_activation_is_atomic_rolls_back_on_failure_and_keeps_old_versions(root, tmp_path):
    v2 = datasets.build_version([_smaller_sample(tmp_path)], "SAMPLE", root=root)
    assert v2["version"] == 2 and datasets.active(root)["version"] == 1  # built, not yet active

    def broken():
        raise RuntimeError("health check failed")

    with pytest.raises(RuntimeError):
        datasets.activate(2, root, health_check=broken)
    assert datasets.active(root)["version"] == 1  # pointer restored

    datasets.activate(2, root)
    assert datasets.active_pointer(root) == {**datasets.active_pointer(root), "version": 2, "previous": 1}
    assert datasets.db_path(1, root).exists()  # activation never deletes the old version
    assert not list(root.glob(".active.*.tmp"))

    assert datasets.rollback(root)["version"] == 1
    assert datasets.active(root)["version"] == 1


def test_store_follows_the_active_pointer(root, tmp_path):
    store = DatasetCSRStore(root)
    n1 = store.data_quality_report()["rows_loaded"]
    datasets.activate(datasets.build_version([_smaller_sample(tmp_path)], "SAMPLE", root=root)["version"], root)
    assert store.current_dataset()["version"] == 2
    assert store.data_quality_report()["rows_loaded"] < n1


# -- provenance + replay -------------------------------------------------------------------------------------
def test_trace_records_dataset_id_version_and_sha256(root, tmp_path):
    from fundbridge.stores.memory_meko import _provenance_meta

    run, backends = _run_pipeline(tmp_path, DatasetCSRStore(root))
    prov = run["trace"]["data_provenance"]
    m = datasets.active(root)
    assert (prov["dataset_id"], prov["version"], prov["sha256"]) == (m["dataset_id"], 1, m["sha256"])
    assert set(prov) == {"dataset_id", "version", "sha256", "tables", "queries", "row_counts", "data_quality_caveats"}
    q = prov["queries"][0]
    assert {"sql", "params", "tables", "row_count", "result_hash"} <= set(q)
    assert any("gold_funders_sector_state" in x["tables"] and x["row_count"] > 0 for x in prov["queries"])
    assert prov["data_quality_caveats"]
    assert run["trace"]["query"]["dataset"] == datasets.ref(m)
    stored = backends.memory.get_trace(run["trace"]["trace_id"])
    assert stored.data_provenance["sha256"] == m["sha256"]  # the memory trace carries it too
    meta = _provenance_meta(prov)  # what goes into Meko trace metadata
    assert (meta["dataset_id"], meta["version"], meta["sha256"]) == (m["dataset_id"], 1, m["sha256"])


def test_replay_reproduces_on_stored_version_after_a_newer_one_is_active(root, tmp_path):
    from fundbridge.replay import replay

    store = DatasetCSRStore(root)
    run, _ = _run_pipeline(tmp_path, store)
    datasets.activate(datasets.build_version([_smaller_sample(tmp_path)], "SAMPLE", root=root)["version"], root)
    res = replay(run, store)
    assert res["status"] == "reproduced", res
    assert res["version"] == 1 and res["queries_matching"] == res["queries_checked"] > 0 and res["ranking_matches"]
    (tmp_path / "second").mkdir()
    run2, _ = _run_pipeline(tmp_path / "second", store)
    assert run2["trace"]["data_provenance"]["version"] == 2  # new runs read the active version


def test_replay_detects_different_results(root, tmp_path):
    from fundbridge.replay import replay

    store = DatasetCSRStore(root)
    run, _ = _run_pipeline(tmp_path, store)
    top = run["ranked"][0]["company_id"]
    with sqlite3.connect(datasets.db_path(1, root)) as con:  # tamper with the stored version: replay must notice
        con.execute("UPDATE gold_funders_sector_state SET spend_inr = spend_inr * 0.01 WHERE company_id = ?", (top,))
    res = replay(run, store)
    assert res["status"] == "differs" and res["queries_matching"] < res["queries_checked"]


def test_replay_unavailable_when_version_missing_or_hash_differs(root, tmp_path):
    from fundbridge.replay import replay

    store = DatasetCSRStore(root)
    run, _ = _run_pipeline(tmp_path, store)
    wrong = json.loads(json.dumps(run))
    wrong["trace"]["data_provenance"]["sha256"] = "0" * 64
    res = replay(wrong, store)
    assert res["status"] == "unavailable" and "different sha256" in res["reason"]

    gone = json.loads(json.dumps(run))
    gone["trace"]["data_provenance"]["version"] = 99
    res = replay(gone, store)
    assert res["status"] == "unavailable" and "v99" in res["reason"]


def test_reading_a_missing_version_raises():
    store = DatasetCSRStore()
    with pytest.raises(DatasetUnavailable):
        with store.pinned({"dataset_id": datasets.DATASET_ID, "version": 999, "sha256": ""}):
            store.data_source()


def test_rows_hash_is_order_independent_and_float_tolerant():
    assert rows_hash([{"a": 1, "b": 0.1 + 0.2}, {"a": 2, "b": 1.0}]) == rows_hash([{"a": 2, "b": 1.0}, {"a": 1, "b": 0.3}])
    assert rows_hash([{"a": 1}]) != rows_hash([{"a": 2}])
