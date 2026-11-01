"""Typed inputs/outputs shared by agents, stores and the API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class NGOProfile(BaseModel):
    ngo_id: str
    name: str
    cause: str
    state: str
    district: str = ""
    project_summary: str = ""
    funding_ask_inr: float = Field(gt=0)


class Evidence(BaseModel):
    """One filing row backing a claim."""
    source_ref: str
    fy: str
    amount_inr: float
    sector: str
    sector_raw: str
    state: str | None
    states_raw: str
    state_quality: str
    district: str | None
    kind: Literal["sector+state", "sector", "state", "multi_state"]


class Candidate(BaseModel):
    company_id: str
    company_name: str
    cin: str = ""
    sector_state_spend: float
    sector_spend_any_state: float
    state_spend_any_sector: float
    total_spend: float
    district_spend: float
    multi_state_sector_spend: float
    years_funded: int  # distinct FYs with sector+state spend
    years_window: int
    fys_funded: list[str]
    avg_ticket_sector: float | None  # avg row amount in the target sectors (any state)
    n_rows_sector_state: int
    trend: Literal["up", "flat", "down", "unknown"]
    per_year_sector_state: dict[str, float]
    evidence: list[Evidence]
    caveats: list[str]


class ScoreBreakdown(BaseModel):
    components: dict[str, float]  # each in [0, 1]
    weights: dict[str, float]
    weighted: dict[str, float]  # component * weight * 100
    total: float  # 0..100


class Sentence(BaseModel):
    text: str
    source_refs: list[str]


class RankedFunder(BaseModel):
    rank: int
    company_id: str
    company_name: str
    score: ScoreBreakdown
    candidate: Candidate
    short_why: str
    explanation: list[Sentence]
    explanation_mode: Literal["llm", "template"]
    sources: list[str]
    unknowns: list[str]
    # verified | rules_only | needs_human_review | unavailable | template
    explanation_status: str = "template"
    produced_by: str | None = None
    fallback_from: str | None = None
    review_problems: list[str] = []
    rejected_text: list[str] = []


class PairwiseComparison(BaseModel):
    higher: str
    lower: str
    higher_rank: int
    lower_rank: int
    score_gap: float
    component_deltas: dict[str, float]  # weighted points, higher minus lower
    summary: str


class DecisionTrace(BaseModel):
    trace_id: str
    run_id: str
    ngo_id: str
    created_at: str
    agent: str = "researcher"
    inputs: dict
    query: dict
    weights: dict[str, float]
    ranking: list[dict]
    pairwise: list[PairwiseComparison]
    data_quality_caveats: list[str]
    verification_log: list[dict] = []
    llm_calls: list[dict] = []
    # dataset_id, version, sha256 of the data read, every query (SQL, params, tables, row count, result hash), caveats
    data_provenance: dict = {}


class EmailDraft(BaseModel):
    tone: Literal["formal", "warm"]
    subject: str
    body: str
    mode: Literal["llm", "template"]
    produced_by: str | None = None


class OutreachPackage(BaseModel):
    ngo_id: str
    company_id: str
    company_name: str
    drafts: list[EmailDraft]
    facts_used: list[Sentence]
    contact_note: str
    verification_log: list[dict] = []
    status: str = "template"
    produced_by: str | None = None
    fallback_from: str | None = None
    review_problems: list[str] = []
    rejected: dict | None = None
    llm_calls: list[dict] = []


EventType = Literal["drafted", "sent", "reply_received", "promise_made", "no_reply", "note",
                    "viewed", "interested", "info_requested", "declined", "nudge_suggested"]


class FollowUpEvent(BaseModel):
    event_id: str
    ngo_id: str
    company_id: str
    company_name: str = ""
    type: EventType
    at: str  # ISO timestamp
    text: str = ""
    promise_what: str | None = None
    promise_by: str | None = None  # ISO date, or None when unknown
    simulated: bool = False
    meta: dict = {}


class NextStep(BaseModel):
    action: Literal["wait", "nudge", "new_angle_or_other_company", "reply_to_response", "promise_reminder", "send_first_email", "close_loop"]
    due: str | None
    overdue: bool
    reason: str
    cited_event_ids: list[str]
    draft: str = ""
    mode: Literal["llm", "template", "none"] = "none"
    why: str = ""  # model's explanation of the chosen step (rules choose the step)
    status: str = "none"
    produced_by: str | None = None
    fallback_from: str | None = None
    review_problems: list[str] = []
    rejected: dict | None = None
