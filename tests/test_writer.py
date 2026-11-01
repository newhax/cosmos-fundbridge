import pytest

from fundbridge.agents.context import Context
from fundbridge.agents.writer import MAX_WORDS, EmailOut, EmailsOut, Writer, check_emails
from fundbridge.config import DATASETS_DIR, Settings
from fundbridge.llm import Router
from tests_offline import OFFLINE_DB
from fundbridge.models import NGOProfile, RankedFunder
from fundbridge.scoring import rank
from fundbridge.stores.csr_dataset import DatasetCSRStore
from fundbridge.stores.memory_local import LocalMemoryStore

pytestmark = pytest.mark.skipif(not (DATASETS_DIR / "active.json").exists(), reason="run scripts/ingest.py first")
NGO = NGOProfile(ngo_id="w", name="Shiksha Setu Foundation", cause="education", state="Rajasthan", district="Barmer",
                 project_summary="Libraries for 40 village schools.", funding_ask_inr=1_500_000)


@pytest.fixture()
def top(tmp_path):
    csr = DatasetCSRStore()
    cands, _ = csr.top_funders(["education"], "Rajasthan", "Barmer", 5, 5)
    c, s = rank(cands, NGO.funding_ask_inr)[0]
    r = RankedFunder(rank=1, company_id=c.company_id, company_name=c.company_name, score=s, candidate=c, short_why="",
                     explanation=[], explanation_mode="template", sources=[], unknowns=[])
    ctx = Context(ngo=NGO, csr=csr, memory=LocalMemoryStore(tmp_path / "m.sqlite"), llm=Router(Settings(gemini_api_key="", anthropic_api_key=""), db_path=OFFLINE_DB).session("test"))
    return ctx, r


def test_template_emails_are_grounded_and_short(top):
    ctx, r = top
    pkg = Writer().run(ctx, r, ["education"], ["Some Other Co"])
    assert {d.tone for d in pkg.drafts} == {"formal", "warm"}
    for d in pkg.drafts:
        assert d.body.startswith("Dear CSR Team")
        assert len(d.body.split()) <= MAX_WORDS
        assert "@" not in d.body
    assert pkg.facts_used and all(f.source_refs for f in pkg.facts_used)
    assert "not in public CSR data" in pkg.contact_note


def test_check_emails_rejects_bad_llm_output():
    bad = EmailsOut(
        formal=EmailOut(subject="Hi", body="Dear Mr. Rao, you gave ₹99 crore. " + "word " * 200, fact_ids=["F1"]),
        warm=EmailOut(subject="Hi", body="Dear CSR Team, thanks.", fact_ids=[]),
    )
    res = check_emails(bad, "[F1] Co reported ₹1.2 crore.", "Co", [], {"F1"})
    joined = " ".join(res.problems)
    assert not res.ok
    assert "'99'" in joined and "Mr. Rao" in joined and "words" in joined and "CSR Team" in joined
    assert "sentence 2 cites no fact id" in joined
