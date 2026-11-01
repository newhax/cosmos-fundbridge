"""Agents through the router with fake providers: batching, cross-model check, fallback tags."""
import re

import pytest

from fundbridge.agents.context import Context
from fundbridge.agents.writer import Writer
from fundbridge.appstate import AppState
from fundbridge.config import DATASETS_DIR, Settings
from fundbridge.llm import ProviderError, RawResponse, Router
from fundbridge.models import NGOProfile, RankedFunder
from fundbridge.pipeline import run_pipeline
from fundbridge.stores import Backends
from fundbridge.stores.csr_dataset import DatasetCSRStore
from fundbridge.stores.memory_local import LocalMemoryStore

pytestmark = pytest.mark.skipif(not (DATASETS_DIR / "active.json").exists(), reason="run scripts/ingest.py first")
NGO = NGOProfile(ngo_id="t", name="Shiksha Setu", cause="education", state="Rajasthan", district="Barmer",
                 project_summary="Libraries in village schools.", funding_ask_inr=1_500_000)


class SmartFake:
    """Answers each schema with grounded content copied from the prompt's own facts."""

    def __init__(self, name, verifier_rejects=False, fail=False):
        self.name, self.verifier_rejects, self.fail, self.calls = name, verifier_rejects, fail, []

    def generate(self, model, system, user, schema, max_tokens):
        self.calls.append(schema.__name__)
        if self.fail:
            raise ProviderError("no_credit", "Your credit balance is too low")
        n = schema.__name__
        if n == "SectorChoice":
            out = {"sectors": ["education"], "rationale": "The NGO works on education."}
        elif n == "ExplanationsOut":
            items = []
            for cid, block in re.findall(r"### company_id: (\S+) \|[^\n]*\n(.*?)(?=\n### |\Z)", user, re.S):
                f1 = re.search(r"\[F1\] (.*)", block).group(1)
                items.append({"company_id": cid, "short_why": "Funds education in Rajasthan.",
                              "sentences": [{"text": f1, "fact_ids": ["F1"]}]})
            out = {"items": items}
        elif n == "VerifierOut":
            ids = re.findall(r"^\[([^\]]+)\]", user.split("CLAIMS:")[1], re.M)
            out = {"verdicts": [{"claim_id": i, "supported": not self.verifier_rejects,
                                 "reason": "" if not self.verifier_rejects else "amount not in evidence"} for i in ids]}
        elif n == "EmailsOut":
            f1 = re.search(r"\[F1\] (.*)", user).group(1)
            body = f"Dear CSR Team,\n\nWe are Shiksha Setu. Public filings show: {f1}\n\nWarm regards,\nShiksha Setu"
            out = {"formal": {"subject": "Partnership", "body": body, "fact_ids": ["F1"]},
                   "warm": {"subject": "Hello", "body": body, "fact_ids": ["F1"]}}
        else:
            raise AssertionError(n)
        return RawResponse(out, 100, 50)


def router_with(tmp_path, gemini, claude):
    s = Settings(gemini_api_key="g", anthropic_api_key="c", gemini_model_fast="g-fast", gemini_model_main="g-main",
                 claude_model="c-model", llm_mode="live", gemini_rpm=6000, claude_rpm=6000)
    return Router(s, providers={"gemini": gemini, "claude": claude}, db_path=tmp_path / "llm.sqlite", sleep=lambda _: None)


def run(tmp_path, router):
    backends = Backends(csr=DatasetCSRStore(), memory=LocalMemoryStore(tmp_path / "mem.sqlite"))
    return run_pipeline(NGO, backends, router, AppState(tmp_path / "app.sqlite"))


def test_top5_explained_in_one_batched_call_and_verified(tmp_path):
    g = SmartFake("gemini")
    r = run(tmp_path, router_with(tmp_path, g, SmartFake("claude")))
    assert g.calls == ["SectorChoice", "ExplanationsOut", "VerifierOut"]  # 3 calls for the whole ranking
    top, rest = r["ranked"][:5], r["ranked"][5:]
    assert all(x["explanation_status"] == "verified" and x["produced_by"] == "gemini:g-main" for x in top)
    assert all(x["explanation_status"] == "template" for x in rest)
    roles = [c["role"] for c in r["trace"]["llm_calls"]]
    assert roles == ["prospector", "researcher", "verifier"]
    assert all(c["latency_ms"] >= 0 and c["input_tokens"] == 100 for c in r["trace"]["llm_calls"])
    assert r["sector_mode"] == "llm (gemini:g-fast)"


def test_verifier_rejection_retries_once_then_needs_human_review(tmp_path):
    g = SmartFake("gemini", verifier_rejects=True)
    r = run(tmp_path, router_with(tmp_path, g, SmartFake("claude")))
    assert g.calls.count("ExplanationsOut") == 2  # one retry
    top = r["ranked"][0]
    assert top["explanation_status"] == "needs_human_review" and top["explanation_mode"] == "template"
    assert top["review_problems"] and top["rejected_text"]
    assert all(s["source_refs"] for s in top["explanation"])  # template shown instead, still sourced


def test_writer_claude_failure_falls_back_to_gemini_and_is_tagged(tmp_path):
    router = router_with(tmp_path, SmartFake("gemini"), SmartFake("claude", fail=True))
    csr = DatasetCSRStore()
    cands, _ = csr.top_funders(["education"], "Rajasthan", "Barmer", 5, 5)
    from fundbridge.scoring import rank
    c, s = rank(cands, NGO.funding_ask_inr)[0]
    rf = RankedFunder(rank=1, company_id=c.company_id, company_name=c.company_name, score=s, candidate=c,
                      short_why="", explanation=[], explanation_mode="template", sources=[], unknowns=[])
    ctx = Context(ngo=NGO, csr=csr, memory=LocalMemoryStore(tmp_path / "m.sqlite"), llm=router.session("w"))
    pkg = Writer().run(ctx, rf, ["education"], ["Other Co"])
    assert pkg.status == "verified"
    assert pkg.produced_by == "gemini:g-main" and pkg.drafts[0].produced_by == "gemini:g-main"
    assert pkg.fallback_from.startswith("claude:c-model (no_credit)")
    assert router.disabled["claude"] == "no_credit"


class LeakyFake(SmartFake):
    """Returns drafts that leak internal ids into the text."""

    def generate(self, model, system, user, schema, max_tokens):
        n = schema.__name__
        if n == "DraftOut":
            ev = re.search(r"\[(ev_[0-9a-f]+)\]", user).group(1)
            self.calls.append(n)
            return RawResponse({"message": f"Dear CSR Team,\n\nFollowing up on our email ({ev}).\n\nShiksha Setu",
                                "why": f"No reply since {ev}.", "cited_event_ids": [ev]}, 10, 5)
        res = super().generate(model, system, user, schema, max_tokens)
        if n == "EmailsOut":
            for tone in ("formal", "warm"):
                res.payload[tone]["body"] = res.payload[tone]["body"].replace("Public filings show:", "Public filings show [F1]:")
        return res


def test_internal_ids_are_stripped_from_drafts(tmp_path):
    from datetime import datetime, timedelta, timezone

    from fundbridge.agents.followup import FollowUpTracker, new_event

    router = router_with(tmp_path, LeakyFake("gemini"), SmartFake("claude", fail=True))
    mem = LocalMemoryStore(tmp_path / "m.sqlite")
    ctx = Context(ngo=NGO, csr=DatasetCSRStore(), memory=mem, llm=router.session("f"))
    mem.remember(new_event("t", "C1", "sent", datetime.now(timezone.utc) - timedelta(days=9), company_name="Co One"))
    _, step = FollowUpTracker().plan(ctx, "C1", draft=True)
    assert step.action == "nudge" and step.status == "verified"
    assert "ev_" not in step.draft and "ev_" in step.why  # ids stay in the reasoning, not in the email

    cands, _ = DatasetCSRStore().top_funders(["education"], "Rajasthan", "Barmer", 5, 5)
    from fundbridge.scoring import rank
    c, s = rank(cands, NGO.funding_ask_inr)[0]
    rf = RankedFunder(rank=1, company_id=c.company_id, company_name=c.company_name, score=s, candidate=c,
                      short_why="", explanation=[], explanation_mode="template", sources=[], unknowns=[])
    pkg = Writer().run(Context(ngo=NGO, csr=DatasetCSRStore(), memory=mem, llm=router.session("w")), rf, ["education"], [])
    assert all("[F1]" not in d.body for d in pkg.drafts) and pkg.drafts[0].mode == "llm"


def test_typed_fund_sectors_are_mapped_to_csr_sectors_and_shown(tmp_path):
    from fundbridge.agents.prospector import Prospector

    router = Router(Settings(gemini_api_key="", anthropic_api_key=""), providers={}, db_path=tmp_path / "llm.sqlite")
    ctx = Context(ngo=NGO, csr=DatasetCSRStore(), memory=LocalMemoryStore(tmp_path / "m.sqlite"), llm=router.session("p"))
    p = Prospector()
    sectors, why, mode = p.map_sectors(ctx, ["education", "Mental  Health"])
    assert sectors == ["education", "health"] and mode == "user"
    assert "'mental health' (typed by the NGO) is searched as health (keywords)" in why
    with pytest.raises(ValueError, match="Could not match 'quantum widgets'"):
        p.map_sectors(ctx, ["quantum widgets"])  # no keyword, no model available: never guessed
    with pytest.raises(ValueError, match="Sectors:"):
        p.map_sectors(ctx, ["<script>"])
    run = p.run(ctx, ["menstrual hygiene"])  # keyword "hygiene" -> water and sanitation
    assert run.sectors == ["water and sanitation"] and run.candidates


def test_model_may_decline_a_typed_sector_instead_of_guessing(tmp_path):
    from fundbridge.agents.prospector import Prospector

    class Typed:
        def __init__(self):
            self.prompts = []

        def generate(self, model, system, user, schema, max_tokens):
            assert schema.__name__ == "TypedSectorChoice" and "Never guess" in system
            self.prompts.append(user)
            return RawResponse({"sectors": ["environment"] if "animal" in user else []}, 10, 5)

    fake = Typed()
    ctx = Context(ngo=NGO, csr=DatasetCSRStore(), memory=LocalMemoryStore(tmp_path / "m.sqlite"),
                  llm=router_with(tmp_path, fake, fake).session("p"))
    with pytest.raises(ValueError, match="Could not match 'quantum widgets'"):
        Prospector().map_sectors(ctx, ["quantum widgets"])  # the model returned no sector: refused, not guessed
    sectors, why, _ = Prospector().map_sectors(ctx, ["animal welfare"])
    assert sectors == ["environment"] and "searched as environment (model gemini:" in why
