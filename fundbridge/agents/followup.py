"""FOLLOW-UP TRACKER: remembers every event, applies follow-up rules, drafts the next step.

Rules (deterministic):
  - nothing sent yet                         -> send_first_email
  - promise with a date, date not reached    -> wait until that date
  - promise with a date, date passed         -> promise_reminder (overdue)
  - reply received after the last send       -> reply_to_response
  - sent, no reply, < 7 days                 -> wait (due = sent + 7 days)
  - sent, no reply, 7-13 days                -> nudge (overdue)
  - sent, no reply, >= 14 days               -> new_angle_or_other_company (overdue)
The LLM only writes the message text; it must cite recalled event ids.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel, Field

from ..config import NEW_ANGLE_AFTER_DAYS, NUDGE_AFTER_DAYS
from ..models import FollowUpEvent, NextStep
from ..crosscheck import generate_checked, split_sentences
from ..verify import verify_claims
from .context import Context

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE_PATTERNS = [
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
    re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MON + r"\.?,?(?:\s+(\d{4}))?", re.I),
    re.compile(_MON + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?(?:\s+(\d{4}))?", re.I),
]
_PROMISE_HINT = re.compile(r"\b(will|shall|would|get back|revert|by|before|end of|next)\b", re.I)


def new_event(ngo_id: str, company_id: str, type_: str, at: datetime, **kw) -> FollowUpEvent:
    return FollowUpEvent(event_id=f"ev_{uuid.uuid4().hex[:10]}", ngo_id=ngo_id, company_id=company_id, type=type_,
                         at=at.astimezone(timezone.utc).isoformat(timespec="microseconds"), **kw)


def _month_num(tok: str) -> int:
    tok = tok.lower()
    return next(n for name, n in MONTHS.items() if name.startswith(tok[:3]))


def find_date(text: str, ref: date) -> tuple[date, str, bool] | None:
    """Return (date, matched_text, year_inferred). Relative phrases ("next week") return None: we don't guess."""
    for i, rx in enumerate(_DATE_PATTERNS):
        m = rx.search(text)
        if not m:
            continue
        if i == 0:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            year = y
        elif i == 1:
            d, mo, year = int(m.group(1)), _month_num(m.group(2)), m.group(3)
        else:
            mo, d, year = _month_num(m.group(1)), int(m.group(2)), m.group(3)
        inferred = year is None
        y = int(year) if year else ref.year
        try:
            dt = date(y, mo, d)
        except ValueError:
            continue
        if inferred and dt < ref:
            dt = date(y + 1, mo, d)
        return dt, m.group(0), inferred
    return None


def parse_promise(reply: str, ref: date) -> tuple[str, str | None, str] | None:
    """Rule-based promise extraction: (what, by_iso_or_None, matched_date_text)."""
    sentences = re.split(r"(?<=[.!?])\s+", reply.strip())
    for s in sentences:
        found = find_date(s, ref)
        if found and _PROMISE_HINT.search(s):
            return s.strip(), found[0].isoformat(), found[1]
    for s in sentences:
        if re.search(r"\b(will|shall)\b", s, re.I):
            return s.strip(), None, ""
    return None


class PromiseOut(BaseModel):
    has_promise: bool
    what: str = Field(description="What the company said they would do, quoted or closely paraphrased from the reply")
    date_text: str = Field(description="The exact words in the reply that state the deadline, or empty if none")


class DraftOut(BaseModel):
    message: str
    why: str = Field(description="1-2 sentences: why this next step, citing what happened in the recalled events")
    cited_event_ids: list[str]


DRAFT_SYSTEM = """You write a short follow-up email from a small NGO to a company's CSR Team.
Use ONLY the event history provided. Cite the event ids you relied on in cited_event_ids only,
never inside the message text. Use short paragraphs separated by blank lines.
Also explain in "why" (1-2 sentences) why this next step fits the recalled history.
Never invent names, contacts, amounts, dates or commitments. Address "CSR Team". Under 120 words."""


class FollowUpTracker:
    name = "followup"

    def __init__(self, now: datetime | None = None):
        self._now = now

    def now(self) -> datetime:
        return self._now or datetime.now(timezone.utc)

    # -- events --------------------------------------------------------------
    def record(self, ctx: Context, ev: FollowUpEvent) -> list[FollowUpEvent]:
        """Persist an event; for replies, also extract and persist any promise."""
        ctx.memory.remember(ev)
        out = [ev]
        if ev.type == "reply_received" and ev.text:
            p = self.extract_promise(ctx, ev)
            if p:
                ctx.memory.remember(p)
                out.append(p)
        return out

    def extract_promise(self, ctx: Context, reply: FollowUpEvent) -> FollowUpEvent | None:
        ref = datetime.fromisoformat(reply.at).date()
        what, by, how = None, None, "rules"
        res = ctx.llm.call(
            "parser",
            "Extract whether this reply from a company's CSR team contains a commitment and its deadline. "
            "Quote the deadline words exactly; leave empty if none. Do not infer dates from vague phrases.",
            reply.text, PromiseOut,
        ) if ctx.llm.enabled else None
        llm_out = res.output if res else None
        if llm_out and llm_out.has_promise:
            what, how = llm_out.what, "llm"
            if llm_out.date_text and llm_out.date_text in reply.text:
                found = find_date(llm_out.date_text, ref)
                by = found[0].isoformat() if found else None
        if what is None:
            parsed = parse_promise(reply.text, ref)
            if not parsed:
                return None
            what, by, _ = parsed
        # One second after the reply so history always orders reply -> promise.
        at = datetime.fromisoformat(reply.at) + timedelta(seconds=1)
        return new_event(reply.ngo_id, reply.company_id, "promise_made", at,
                         company_name=reply.company_name, text=what, promise_what=what, promise_by=by,
                         simulated=reply.simulated, meta={"reply_event_id": reply.event_id, "extracted_by": how})

    # -- rules ----------------------------------------------------------------
    def next_step(self, events: list[FollowUpEvent]) -> NextStep:
        now = self.now()
        sends = [e for e in events if e.type == "sent"]
        if not sends:
            drafted = [e.event_id for e in events if e.type == "drafted"]
            return NextStep(action="send_first_email", due=None, overdue=False, cited_event_ids=drafted,
                            reason="No email has been marked as sent to this company yet." +
                                   (" A draft exists." if drafted else ""))
        last_sent = sends[-1]
        sent_at = datetime.fromisoformat(last_sent.at)
        after = [e for e in events if datetime.fromisoformat(e.at) >= sent_at and e.event_id != last_sent.event_id]
        promises = [e for e in after if e.type == "promise_made"]
        # In-app company decisions count as replies; a decline closes the loop.
        replies = [e for e in after if e.type in ("reply_received", "interested", "info_requested")]
        declines = [e for e in after if e.type == "declined"]
        if declines and (not replies or declines[-1].at >= replies[-1].at):
            d = declines[-1]
            return NextStep(action="close_loop", due=None, overdue=False, cited_event_ids=[last_sent.event_id, d.event_id],
                            reason=f"The company marked this pitch 'Not a fit' on {d.at[:10]} (event {d.event_id}). "
                                   "Thank them and prioritise the next company on your ranked list.")

        newer_reply = bool(promises and replies and replies[-1].at > promises[-1].at)
        if promises and promises[-1].promise_by and not newer_reply:
            p = promises[-1]
            due = date.fromisoformat(p.promise_by)
            cites = [last_sent.event_id] + [e.event_id for e in replies[-1:]] + [p.event_id]
            if now.date() < due:
                return NextStep(action="wait", due=due.isoformat(), overdue=False, cited_event_ids=cites,
                                reason=f"They promised: \"{p.promise_what}\" (by {due.isoformat()}, event {p.event_id}). Wait until then.")
            return NextStep(action="promise_reminder", due=due.isoformat(), overdue=True, cited_event_ids=cites,
                            reason=f"The promised date {due.isoformat()} has passed with no newer reply (event {p.event_id}). Send a polite reminder.")
        if replies:
            r = replies[-1]
            cites = [last_sent.event_id, r.event_id] + [e.event_id for e in promises[-1:]]
            extra = f" They said: \"{promises[-1].promise_what}\" but gave no date, so the deadline is unknown." if promises else ""
            return NextStep(action="reply_to_response", due=now.date().isoformat(), overdue=False, cited_event_ids=cites,
                            reason=f"They replied on {r.at[:10]} (event {r.event_id}).{extra} Respond and agree a next step.")
        days = (now - sent_at).days
        if days < NUDGE_AFTER_DAYS:
            due = (sent_at + timedelta(days=NUDGE_AFTER_DAYS)).date().isoformat()
            return NextStep(action="wait", due=due, overdue=False, cited_event_ids=[last_sent.event_id],
                            reason=f"Sent {days} day(s) ago (event {last_sent.event_id}). Rule: wait {NUDGE_AFTER_DAYS} days before nudging.")
        if days < NEW_ANGLE_AFTER_DAYS:
            due = (sent_at + timedelta(days=NUDGE_AFTER_DAYS)).date().isoformat()
            return NextStep(action="nudge", due=due, overdue=True, cited_event_ids=[last_sent.event_id],
                            reason=f"No reply {days} days after sending (event {last_sent.event_id}). Rule: after {NUDGE_AFTER_DAYS} days, send a gentle nudge.")
        due = (sent_at + timedelta(days=NEW_ANGLE_AFTER_DAYS)).date().isoformat()
        return NextStep(action="new_angle_or_other_company", due=due, overdue=True, cited_event_ids=[e.event_id for e in sends],
                        reason=f"No reply {days} days after sending (event {last_sent.event_id}). Rule: after {NEW_ANGLE_AFTER_DAYS} days, "
                               "try a different angle or prioritise the next company on the ranked list.")

    # -- drafting ------------------------------------------------------------
    def plan(self, ctx: Context, company_id: str, draft: bool = False) -> tuple[list[FollowUpEvent], NextStep]:
        """RECALL first, then decide (and optionally draft) using that history."""
        history = ctx.memory.recall(ctx.ngo.ngo_id, company_id)
        return history, self.plan_history(ctx, history, draft)

    def plan_history(self, ctx: Context, history: list[FollowUpEvent], draft: bool = False) -> NextStep:
        """Decide from an already-recalled history (e.g. an in-app pitch thread from the SQLite log)."""
        step = self.next_step(history)
        if draft and step.action not in ("wait", "close_loop"):
            step = self.draft(ctx, history, step)
        return step

    def draft(self, ctx: Context, history: list[FollowUpEvent], step: NextStep) -> NextStep:
        hist_text = "\n".join(f"[{e.event_id}] {e.at[:10]} {e.type}: {e.text[:400]}" +
                              (f" (promise: {e.promise_what}; by {e.promise_by or 'unknown'})" if e.type == "promise_made" else "")
                              for e in history)
        company = next((e.company_name for e in history if e.company_name), "")
        if ctx.llm.enabled:
            evidence = f"{hist_text}\nNGO: {ctx.ngo.name}, cause: {ctx.ngo.cause}, state: {ctx.ngo.state}\nRule decision: {step.reason}"
            # role "followup_draft": Claude first, Gemini MAIN fallback; Gemini FAST verifies.
            checked = generate_checked(
                ctx.llm, "followup_draft", DRAFT_SYSTEM,
                f"NGO: {ctx.ngo.name}\nCompany: {company}\nNext step chosen by rules: {step.action}\nWhy: {step.reason}"
                f"\n\nEvent history recalled from memory (oldest first):\n{hist_text}",
                DraftOut,
                lambda o: verify_claims([o.message, o.why], evidence, allowed_companies=[company],
                                        cited_fact_ids=[o.cited_event_ids], valid_fact_ids={e.event_id for e in history}),
                lambda o: split_sentences(o.message) + split_sentences(o.why),
                evidence,
            )
            step.status, step.produced_by, step.fallback_from = checked.status, checked.produced_by, checked.fallback_from
            step.review_problems, step.rejected = checked.problems, checked.rejected
            if checked.output is not None:
                message = re.sub(r"\s*\(?\bev_[0-9a-f]{6,}\)?", "", checked.output.message)
                step.draft, step.why, step.mode = message, checked.output.why, "llm"
                return step
        else:
            step.status = "template"
        step.draft, step.mode = self._template_draft(ctx, history, step), "template"
        return step

    @staticmethod
    def _template_draft(ctx: Context, history: list[FollowUpEvent], step: NextStep) -> str:
        sent = next((e for e in reversed(history) if e.type == "sent"), None)
        when = sent.at[:10] if sent else "earlier"
        sign = f"\n\nWarm regards,\n{ctx.ngo.name}"
        if step.action == "nudge":
            body = (f"Dear CSR Team,\n\nI hope you are well. I am following up on my email of {when} about {ctx.ngo.name}'s "
                    f"work on {ctx.ngo.cause} in {ctx.ngo.state}. I would be grateful to know if this could fit your CSR plans, "
                    "or who the right person would be to speak with.")
        elif step.action == "new_angle_or_other_company":
            body = (f"Dear CSR Team,\n\nI wrote on {when} about {ctx.ngo.name}'s work on {ctx.ngo.cause}. If a full project "
                    "grant is not possible this year, we would welcome a smaller pilot, employee volunteering, or simply "
                    "guidance on your proposal cycle.")
        elif step.action == "promise_reminder":
            p = next((e for e in reversed(history) if e.type == "promise_made"), None)
            body = (f"Dear CSR Team,\n\nThank you again for your reply. You mentioned: \"{p.promise_what if p else ''}\". "
                    "I am writing to gently check whether there is any update, and whether we can share anything else to help.")
        elif step.action == "reply_to_response":
            body = ("Dear CSR Team,\n\nThank you for your reply. We are happy to share the proposal, budget and any documents "
                    "you need. Please let us know the best next step and timeline.")
        else:
            body = "Dear CSR Team,\n\n(First email: use the Outreach screen to prepare it.)"
        return body + sign
