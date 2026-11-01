"""End-to-end Prospector + Researcher on the sample DB, offline (no LLM)."""
import pytest

from fundbridge.appstate import AppState
from fundbridge.config import DATASETS_DIR, Settings
from fundbridge.llm import Router
from tests_offline import OFFLINE_DB
from fundbridge.models import NGOProfile
from fundbridge.pipeline import run_pipeline
from fundbridge.stores import Backends
from fundbridge.stores.csr_dataset import DatasetCSRStore
from fundbridge.stores.memory_local import LocalMemoryStore

pytestmark = pytest.mark.skipif(not (DATASETS_DIR / "active.json").exists(), reason="run scripts/ingest.py first")

NGO = NGOProfile(ngo_id="t-ngo", name="Test NGO", cause="education for rural children", state="Rajasthan",
                 district="Barmer", project_summary="School libraries in villages", funding_ask_inr=1_000_000)


@pytest.fixture()
def run(tmp_path):
    mem = LocalMemoryStore(tmp_path / "mem.sqlite")
    backends = Backends(csr=DatasetCSRStore(), memory=mem)
    offline = Router(Settings(gemini_api_key="", anthropic_api_key=""), db_path=OFFLINE_DB)
    return run_pipeline(NGO, backends, offline, AppState(tmp_path / "app.sqlite"), ["education"]), mem


def test_ranked_rows_have_sources_and_unknowns(run):
    r, _ = run
    assert 0 < len(r["ranked"]) <= 20
    for row in r["ranked"]:
        assert row["sources"], row["company_name"]
        assert row["unknowns"] and all("unknown" in u for u in row["unknowns"])
        assert all(s["source_refs"] for s in row["explanation"])
    scores = [row["score"]["total"] for row in r["ranked"]]
    assert scores == sorted(scores, reverse=True)


def test_trace_has_pairwise_and_is_logged(run):
    r, mem = run
    t = r["trace"]
    assert len(t["pairwise"]) == 4
    assert t["query"]["sql"].startswith("SELECT")
    assert any("SAMPLE DATA" in c for c in t["data_quality_caveats"])
    assert mem.get_trace(t["trace_id"]) is not None


def test_template_explanations_pass_verify(run):
    from fundbridge.evidence import build_facts, facts_text
    from fundbridge.models import Candidate
    from fundbridge.verify import verify_claims

    r, _ = run
    for row in r["ranked"][:5]:
        c = Candidate(**row["candidate"])
        ev = facts_text(build_facts(c, NGO, ["education"]))
        res = verify_claims([row["short_why"]] + [s["text"] for s in row["explanation"]], ev,
                            allowed_companies=[c.company_name])
        assert res.ok, res.problems


class _SlowFailingLLM:
    """Router provider that is 'available' but never returns usable text (no network)."""

    def generate(self, model, system, user, schema, max_tokens):
        import time

        from fundbridge.llm import ProviderError

        time.sleep(0.3)
        raise ProviderError("error", "offline test provider")


def test_background_mode_returns_ranking_first_then_fills_explanations(tmp_path):
    import threading

    mem = LocalMemoryStore(tmp_path / "mem.sqlite")
    backends = Backends(csr=DatasetCSRStore(), memory=mem)
    router = Router(Settings(gemini_api_key="g", anthropic_api_key="", llm_mode="gemini_only"),
                    providers={"gemini": _SlowFailingLLM()}, db_path=tmp_path / "llm.sqlite")
    app = AppState(tmp_path / "app.sqlite")
    fast = run_pipeline(NGO, backends, router, app, ["education"], background=True)
    assert fast["explanations"] == "pending" and fast["ranked"]
    assert mem.get_trace(fast["trace"]["trace_id"]) is None  # final trace is logged once, after explaining
    for t in threading.enumerate():
        if t.name == f"explain-{fast['run_id']}":
            t.join(timeout=60)
    done = app.latest_run(NGO.ngo_id)
    assert done["run_id"] == fast["run_id"] and done["explanations"] == "done"
    same = lambda r: [(x["company_id"], x["score"]["total"]) for x in r["ranked"]]  # noqa: E731
    assert same(done) == same(fast)  # explanations never change the ranking
    assert done["trace"]["data_provenance"] == fast["trace"]["data_provenance"]
    assert mem.get_trace(fast["trace"]["trace_id"]) is not None


def test_background_mode_is_synchronous_without_a_model(tmp_path):
    backends = Backends(csr=DatasetCSRStore(), memory=LocalMemoryStore(tmp_path / "mem.sqlite"))
    offline = Router(Settings(gemini_api_key="", anthropic_api_key=""), db_path=OFFLINE_DB)
    r = run_pipeline(NGO, backends, offline, AppState(tmp_path / "app.sqlite"), ["education"], background=True)
    assert r["explanations"] == "done"
