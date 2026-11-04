"""Tests for the five use cases: scoped search, rule-based advisors, case log.  Run:  python -m pytest -q"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag import advisors  # noqa: E402
from rag.answer import answer  # noqa: E402
from rag.retrieve import Retriever  # noqa: E402
from rag.usecases import BY_ID, USE_CASES  # noqa: E402


@pytest.fixture(scope="module")
def retriever():
    from rag import library
    library.ensure_index()
    return Retriever.load()


def levels(findings):
    return [f.level for f in findings]


def test_every_rule_quote_is_in_the_documents(retriever):
    assert advisors.check_citations(retriever.chunks) == []


@pytest.mark.parametrize("uc", USE_CASES, ids=lambda u: u.id)
def test_scope_is_respected_and_not_empty(retriever, uc):
    in_scope = [c for c in retriever.chunks if uc.scope(c)]
    assert in_scope, f"{uc.id} has no passages"
    for q in uc.examples:
        hits, _ = retriever.search(q, k=5, mode="bm25", scope=uc.scope)
        assert hits, f"{uc.id}: no results for {q!r}"
        assert all(uc.scope(h.chunk) for h in hits)


@pytest.mark.parametrize("uc", USE_CASES, ids=lambda u: u.id)
def test_example_questions_get_an_answer(retriever, uc):
    for q in uc.examples:
        a = answer(q, retriever, use_llm=False, scope=uc.scope, role=uc.role)
        assert a.found, f"{uc.id}: example question refused: {q!r}"


def test_ticket_scope_is_parts_c_to_e(retriever):
    scope = BY_ID["tickets"].scope
    labels = {c["label"] for c in retriever.chunks if scope(c)}
    assert any("Condition 9" in l or "Conditions 9" in l for l in labels)
    assert not any("Condition 32" in l or "Conditions 32" in l for l in labels)   # Part F is out of scope


# ---- use case 2
def test_broken_machine_allows_boarding():
    f = advisors.ticket_advice("no_ticket", ["machine"])
    assert f[0].level == "ok" and "6.1.1" in f[0].cites[0].ref


def test_no_reason_means_condition_9():
    f = advisors.ticket_advice("no_ticket", [], penalty_scheme="yes")
    assert "stop" in levels(f)
    assert any(c.ref.startswith("Condition 10") for x in f for c in x.cites)


def test_missed_connection_no_penalty():
    f = advisors.ticket_advice("missed_connection")
    assert levels(f) == ["ok"] and any(c.ref == "Condition 9.4" for c in f[0].cites)


def test_split_ticket_must_call_unless_area_ticket():
    assert advisors.ticket_advice("split_ticket", split_calls=False)[0].level == "stop"
    assert advisors.ticket_advice("split_ticket", split_calls=True)[0].level == "ok"
    assert advisors.ticket_advice("split_ticket", split_calls=False, area_ticket=True)[0].level == "ok"


def test_children():
    assert advisors.ticket_advice("child", child_age="under5")[0].level == "ok"
    assert advisors.ticket_advice("child", child_age="5to15")[0].level == "warn"


# ---- use case 3
def test_mobility_aid_limits():
    assert advisors.assistance_check("wheelchair", 690, 1190, 290)[0].level == "ok"
    over = advisors.assistance_check("scooter", 710, 1100, 200)[0]
    assert over.level == "stop" and "700" in over.text


def test_short_notice_and_unstaffed():
    f = advisors.assistance_check(booked=True, notice_hours=1, station="unstaffed")
    titles = " ".join(x.title for x in f)
    assert "Less than the recommended two hours" in titles and "Unstaffed" in titles


# ---- use case 4
def test_compensation_60_minutes_single():
    f = advisors.claim_advice("late", "single", minutes_late=75, days_since=3, price=30.0)
    assert f[0].level == "ok" and "£15.00" in f[0].text


def test_compensation_return_one_leg_uses_leg_price():
    f = advisors.claim_advice("late", "return_one", minutes_late=60, days_since=3, leg_price=12.0)
    assert "£6.00" in f[0].text


def test_short_delay_points_to_passengers_charter():
    f = advisors.claim_advice("late", "single", minutes_late=20, days_since=3)
    assert f[0].level == "info" and "Passenger’s Charter" in f[0].title


def test_deadline_and_no_double_claim():
    late = advisors.claim_advice("late", "single", minutes_late=90, days_since=40)
    assert any(x.title == "Outside the 28-day deadline" for x in late)
    double = advisors.claim_advice("late", "single", minutes_late=90, refund_already=True)
    assert double[0].level == "stop"


def test_cancelled_not_travelled_full_refund():
    f = advisors.claim_advice("not_travelled", "single", days_since=2)
    assert f[0].level == "ok" and "no administration fee" in f[0].title


# ---- use case 5
def test_fleet_table():
    rows = {advisors.fleet_label(r): r for r in advisors.load_fleet()}
    assert rows["Great Northern Class 717"]["accessible_toilet"] == "No"
    assert rows["Thameslink Class 700"]["on_train_staff"] == "No"
