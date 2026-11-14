"""Phase 3 integration tests: generated dataset -> engine -> oee_aggregate tables."""
import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from backend import models as m
from backend.data_generator.config import SEED
from backend.data_generator.generate import generate
from backend.database import _enable_fk
from backend.services.oee import engine as oee_engine

NOW = datetime(2026, 10, 22, 0, 0, 0)
# ground-truth issue type -> the engine flag that must point at the same evidence
ISSUE_TO_FLAG = {
    "OVERLAPPING_STATES": "OVERLAPPING_STATES",
    "COUNT_DECREASE": "COUNT_DECREASE",
    "SCRAP_EXCEEDS_TOTAL": "SCRAP_EXCEEDS_TOTAL",
    "WRONG_CYCLE_TIME": "CONFIG_CONFLICT",
    "DOWNTIME_IN_PLANNED_BREAK": "DOWNTIME_IN_PLANNED_BREAK",
}


def _session(db):
    eng = create_engine(f"sqlite:///{db.as_posix()}", connect_args={"check_same_thread": False})
    event.listen(eng, "connect", _enable_fk)
    return Session(eng)


def _count(s, model):
    return s.execute(select(func.count()).select_from(model)).scalar_one()


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    d = tmp_path_factory.mktemp("p3tiny")
    generate("tiny", SEED, d / "t.db")
    s = _session(d / "t.db")
    results = oee_engine.run(s, computed_at=NOW)
    yield s, results
    s.close()


@pytest.fixture(scope="module")
def small_baseline(tmp_path_factory):
    d = tmp_path_factory.mktemp("p3base")
    generate("small", SEED, d / "b.db", inject=False)
    s = _session(d / "b.db")
    results = oee_engine.run(s, computed_at=NOW)
    yield s, results
    s.close()


def test_every_machine_shift_is_computed_and_persisted(tiny):
    s, results = tiny
    expected = _count(s, m.Machine) * _count(s, m.ShiftInstance)
    assert len(results) == expected == _count(s, m.OEEAggregate)
    assert _count(s, m.OEEAggregateDetail) == sum(len(r.details) for r in results)


def test_persisted_row_matches_result(tiny):
    s, results = tiny
    r = next(x for x in results if x.flags)
    row = s.get(m.OEEAggregate, r.aggregate_id)
    assert row.availability == r.availability and row.performance == r.performance
    assert row.quality == r.quality and row.oee == r.oee
    assert row.status == r.status and row.flags == r.flags_text and row.computed_at == NOW


def test_flags_match_ground_truth_one_to_one(tiny):
    s, results = tiny
    truth = s.execute(select(m.GroundTruth)).scalars().all()    # tests may read labels; the engine never does
    assert truth
    for gt in truth:
        flag, ev = ISSUE_TO_FLAG[gt.issue_type], set(json.loads(gt.evidence_ids))
        hits = [r for r in results if flag in r.evidence and ev <= set(r.evidence[flag])]
        assert len(hits) == 1, (gt.truth_id, gt.issue_type)
    flagged = [r for r in results if set(ISSUE_TO_FLAG.values()) & set(r.flags)]
    assert len(flagged) == len(truth)          # no false positives on clean machine-shifts


def test_quality_exception_gives_null_quality_and_oee(tiny):
    _, results = tiny
    bad = [r for r in results if "SCRAP_EXCEEDS_TOTAL" in r.flags]
    assert bad and all(r.quality is None and r.oee is None for r in bad)


def test_wrong_cycle_time_keeps_performance_over_100(tiny):
    _, results = tiny
    conflict = [r for r in results if "CONFIG_CONFLICT" in r.flags]
    assert conflict and any(r.performance is not None and r.performance > 1.0 for r in conflict)
    assert all(("PERFORMANCE_OVER_100" in r.flags) == (r.performance > 1.0) for r in conflict)


def test_rerun_is_idempotent(tiny):
    s, results = tiny
    before = (_count(s, m.OEEAggregate), _count(s, m.OEEAggregateDetail))
    again = oee_engine.run(s, computed_at=NOW)
    assert (_count(s, m.OEEAggregate), _count(s, m.OEEAggregateDetail)) == before
    assert [r.to_dict() for r in again] == [r.to_dict() for r in results]


def test_engine_leaves_source_and_ground_truth_untouched(tiny):
    s, _ = tiny
    assert _count(s, m.GroundTruth) == 5 and _count(s, m.Anomaly) == 0


def test_line_and_plant_rollups_sum_components(tiny):
    s, results = tiny
    roll = oee_engine.line_rollups(s, results)
    plant = roll["PLANT"]
    assert plant.operating_seconds == pytest.approx(sum(r.operating_seconds for r in results))
    assert plant.availability == pytest.approx(
        sum(r.operating_seconds for r in results) / sum(r.planned_production_seconds for r in results))
    assert sum(roll[k].total_count for k in roll if k != "PLANT") == plant.total_count


def test_data_quality_report_lists_evidence(tiny):
    _, results = tiny
    rep = oee_engine.data_quality_report(results)
    assert rep["machine_shifts"] == len(results)
    assert rep["flag_counts"].get("OVERLAPPING_STATES") == 1
    assert all(item["evidence"] for item in rep["items"] if item["flags"])


def test_clean_baseline_has_no_flags_and_valid_ratios(small_baseline):
    _, results = small_baseline
    assert results
    for r in results:
        assert r.status == "OK" and r.flags == [], (r.aggregate_id, r.flags)
        assert r.unreconciled_seconds == 0
        assert 0 < r.availability <= 1 and 0 < r.performance <= 1 and 0 < r.quality <= 1
        assert r.oee == pytest.approx(r.availability * r.performance * r.quality)
        assert sum(d.total_count for d in r.details) == r.total_count
