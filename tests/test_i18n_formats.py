"""Translation safety (spec test 6), Indian formats (test 7), model caps (part of test 9)."""
from datetime import date, datetime, timezone

import pytest

from fundbridge.config import Settings
from fundbridge.formatting import fiscal_year, fmt_date, group_indian, inr_unit
from fundbridge.llm import RawResponse, Router
from fundbridge.translate import MACHINE_LABEL, protect, translate_draft, verify_translation

EN = ("Dear CSR Team, Shiksha Setu Foundation (CIN L17110MH1973PLC019786, Darpan RJ/2019/0234567, CSR-1 CSR00012345) "
      "asks for ₹15.0 lakh for 12 schools in Barmer by 31/03/2027 (FY 2026-27). Source: csr.csv#L42|FY2019. "
      "Thar Minerals spent ₹2.10 crore on education.")
NAMES = ["Shiksha Setu Foundation", "Thar Minerals"]


class FakeSession:
    """Plays the model: 'translates' by uppercasing words around the placeholders, or misbehaves on purpose."""

    def __init__(self, mode="ok"):
        self.mode, self.capacity_reached, self.calls = mode, False, 0

    def call(self, role, system, user, schema):
        self.calls += 1
        assert role == "translator" and "⟦0⟧" in user  # the model only ever sees placeholders
        assert "CSR00012345" not in user and "Thar Minerals" not in user and "15.0" not in user
        if self.mode == "none":
            return None
        text = "हिंदी: " + user
        if self.mode == "change_number":
            text = text.replace("⟦3⟧", "₹16.0 lakh")
        if self.mode == "add_claim":
            text += " हमने 500 बच्चों को पढ़ाया।"
        if self.mode == "drop":
            text = text.replace("⟦1⟧", "")

        class Meta:
            produced_by = "fake:model"

        class Res:
            output = schema(text=text)
            meta = Meta()
        return Res()


def test_translation_keeps_names_ids_numbers_dates_and_labels_it():
    out = translate_draft(FakeSession(), EN, "hi", NAMES)
    assert out["status"] == "translated" and out["label"] == MACHINE_LABEL
    for token in ("Shiksha Setu Foundation", "Thar Minerals", "L17110MH1973PLC019786", "RJ/2019/0234567", "CSR00012345",
                  "₹15.0 lakh", "₹2.10 crore", "31/03/2027", "csr.csv#L42|FY2019", "12"):
        assert token in out["text"], token
    assert out["check"]["ok"] and out["check"]["checked_tokens"] >= 10


@pytest.mark.parametrize("mode,why", [("change_number", "appears 0 times"), ("add_claim", "not in the English draft"),
                                      ("drop", "appears 0 times")])
def test_translation_that_changes_or_adds_facts_is_rejected(mode, why):
    out = translate_draft(FakeSession(mode), EN, "hi", NAMES)
    assert out["status"] == "rejected" and out["text"] == EN and out["label"] == ""
    assert any(why in p for p in out["check"]["problems"])


def test_unavailable_model_keeps_english_and_unsupported_language_is_refused():
    out = translate_draft(FakeSession("none"), EN, "ta", NAMES)
    assert out["status"] == "unavailable" and out["text"] == EN
    with pytest.raises(ValueError):
        translate_draft(FakeSession(), EN, "xx", NAMES)


def test_verifier_is_independent_of_the_model():
    masked, tokens = protect(EN, NAMES)
    assert "CSR00012345" in tokens and "Shiksha Setu Foundation" in tokens
    assert verify_translation(EN, EN, NAMES)["ok"]
    assert not verify_translation(EN, EN.replace("2027", "2028"), NAMES)["ok"]


# -- formats -------------------------------------------------------------------------------------------------
def test_lakh_crore_grouping_and_unit_toggle():
    assert group_indian(1234567) == "12,34,567" and group_indian(100000) == "1,00,000" and group_indian(999) == "999"
    assert group_indian(1234567890) == "1,23,45,67,890"
    assert inr_unit(1234567) == "₹12,34,567"
    assert inr_unit(1234567, "lakh") == "₹12.35 lakh"
    assert inr_unit(123456789, "crore") == "₹12.35 crore"
    assert inr_unit(12345678900, "lakh") == "₹1,23,456.79 lakh"  # the unit is always written out
    assert inr_unit(None) == "unknown"
    with pytest.raises(ValueError):
        inr_unit(1, "million")


def test_dates_ddmmyyyy_ist_and_fiscal_year_april_to_march():
    assert fmt_date("2026-03-05") == "05/03/2026"
    assert fmt_date(datetime(2026, 3, 31, 20, 0, tzinfo=timezone.utc)) == "01/04/2026"  # 01:30 IST next day
    assert fiscal_year(date(2026, 3, 31)) == "2025-26" and fiscal_year(date(2026, 4, 1)) == "2026-27"
    assert fmt_date(None) == "unknown"


# -- model caps -------------------------------------------------------------------------------------------
class OK:
    def generate(self, model, system, user, schema, max_tokens):
        return RawResponse({"text": "x"}, 5, 5)


def test_per_user_daily_cap_degrades_gracefully(tmp_path):
    from fundbridge.translate import TranslationOut

    s = Settings(gemini_api_key="g", anthropic_api_key="", llm_mode="live", gemini_rpm=6000, llm_user_daily_cap=2)
    router = Router(s, providers={"gemini": OK()}, db_path=tmp_path / "llm.sqlite", sleep=lambda _: None)
    sess = router.session("t", user_id="user-1")
    assert sess.call("translator", "a", "1", TranslationOut) is not None
    assert sess.call("translator", "a", "2", TranslationOut) is not None
    assert sess.call("translator", "a", "3", TranslationOut) is None and sess.capacity_reached
    other = router.session("t", user_id="user-2")
    assert other.call("translator", "a", "4", TranslationOut) is not None  # caps are per user
    out = translate_draft(router.session("t", "user-1"), EN, "hi", NAMES)
    assert out["status"] == "unavailable" and "capacity reached" in out["reason"].lower()


def test_global_daily_limit_sets_capacity_flag(tmp_path):
    from fundbridge.translate import TranslationOut

    s = Settings(gemini_api_key="g", anthropic_api_key="", llm_mode="live", gemini_daily_limit=1, gemini_rpm=6000)
    router = Router(s, providers={"gemini": OK()}, db_path=tmp_path / "llm.sqlite", sleep=lambda _: None)
    assert router.session("a").call("translator", "a", "1", TranslationOut) is not None
    sess = router.session("b")
    assert sess.call("translator", "a", "2", TranslationOut) is None and sess.capacity_reached
