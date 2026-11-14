"""Phase 4 integration tests: generated dataset -> OEE engine -> detectors -> persisted alerts/anomalies.
Same fixture pattern as tests/test_engine.py. Tests may read ground_truth; the detection path never does."""
import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from backend import models as m
from backend.data_generator.config import SEED
from backend.data_generator.generate import generate
from backend.database import _enable_fk
from backend.models.alerts import Alert, AnomalyTruthLink
from backend.services.anomaly import ground_truth_ref, runner

NOW = datetime(2026, 10, 22, 0, 0, 0)
DQ_TYPES = {"OVERLAPPING_STATES", "COUNT_DECREASE", "SCRAP_EXCEEDS_TOTAL", "WRONG_CYCLE_TIME",
            "DOWNTIME_IN_PLANNED_BREAK"}


def _session(db):
    eng = create_engine(f"sqlite:///{db.as_posix()}", connect_args={"check_same_thread": False})
    event.listen(eng, "connect", _enable_fk)
    return Session(eng)


def _n(s, model):
    return s.execute(select(func.count()).select_from(model)).scalar_one()


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    d = tmp_path_factory.mktemp("p4tiny")
    generate("tiny", SEED, d / "t.db")
    s = _session(d / "t.db")
    out = runner.run_detection(s, detected_at=NOW, evaluate_ground_truth=True)
    yield s, out
    s.close()


@pytest.fixture()
def fresh(tmp_path):
    generate("tiny", SEED, tmp_path / "f.db")
    s = _session(tmp_path / "f.db")
    yield s
    s.close()


def test_every_injected_issue_is_a_warning_in_the_anomaly_table(tiny):
    s, out = tiny
    truth = s.execute(select(m.GroundTruth)).scalars().all()
    rows = s.execute(select(m.Anomaly)).scalars().all()
    assert len(rows) == len(truth) == 5
    assert {r.issue_type for r in rows} == DQ_TYPES
    for gt in truth:
        hit = [r for r in rows if r.issue_type == gt.issue_type and r.machine_id == gt.machine_id
               and set(json.loads(gt.evidence_ids)) & set(json.loads(r.evidence_ids))]
        assert len(hit) == 1, gt.truth_id
        assert hit[0].expected_decision == gt.expected_decision


def test_warnings_are_traceable_to_machine_shift_and_source_rows(tiny):
    s, _ = tiny
    source_ids = set()
    for model, col in ((m.StateEvent, m.StateEvent.event_id), (m.ProductionCount, m.ProductionCount.count_id),
                       (m.QualityInspection, m.QualityInspection.inspection_id),
                       (m.MachineProductConfig, m.MachineProductConfig.config_id),
                       (m.ProductionOrder, m.ProductionOrder.order_id), (m.PlannedBreak, m.PlannedBreak.planned_break_id)):
        source_ids |= set(s.execute(select(col)).scalars())
    for r in s.execute(select(m.Anomaly)).scalars():
        assert r.machine_id and r.shift_instance_id and r.severity in ("LOW", "MEDIUM", "HIGH") and r.explanation
        ev = json.loads(r.evidence_ids)
        assert ev and set(ev) <= source_ids, (r.anomaly_id, ev)


def test_operational_alerts_are_separate_from_data_quality_warnings(tiny):
    s, out = tiny
    assert all(a.anomaly_type not in DQ_TYPES for a in s.execute(select(Alert)).scalars())
    assert _n(s, Alert) == out["alerts"]
    # only mapped flags became anomaly rows; unmapped ones stay flags
    assert _n(s, m.Anomaly) == out["warnings"]


def test_alert_rows_carry_all_traceability_fields(tiny):
    s, _ = tiny
    for a in s.execute(select(Alert)).scalars():
        assert a.alert_id.startswith("ALT-") and a.machine_id and a.shift_instance_id
        assert a.severity in ("LOW", "MEDIUM", "HIGH") and a.kpi and a.recommended_action and a.explanation
        assert a.baseline_n >= 5 and a.measured_value < a.baseline_value
        assert json.loads(a.evidence_ids)[0].startswith("AGG-") and "kpi_delta_pts" in json.loads(a.kpi_impact)


def test_rerun_creates_no_duplicates_and_keeps_ids(tiny):
    s, _ = tiny
    before_a = {r.anomaly_id: (r.machine_id, r.shift_instance_id, r.issue_type, r.detected_at)
                for r in s.execute(select(m.Anomaly)).scalars()}
    before_alerts = {r.alert_id: r.detected_at for r in s.execute(select(Alert)).scalars()}
    for _ in range(2):
        out = runner.run_detection(s, detected_at=datetime(2030, 1, 1))
        assert out["anomaly_store"]["inserted"] == 0 and out["alert_store"]["inserted"] == 0
        assert out["anomaly_store"]["unchanged"] == len(before_a)
    assert {r.anomaly_id: (r.machine_id, r.shift_instance_id, r.issue_type, r.detected_at)
            for r in s.execute(select(m.Anomaly)).scalars()} == before_a
    assert {r.alert_id: r.detected_at for r in s.execute(select(Alert)).scalars()} == before_alerts


def test_scoped_rerun_does_not_duplicate_or_delete_other_scopes(fresh):
    s = fresh
    runner.run_detection(s, detected_at=NOW)
    total = _n(s, m.Anomaly)
    runner.run_detection(s, machine_ids=["M-01-01"], detected_at=NOW)
    assert _n(s, m.Anomaly) == total
    shift = s.execute(select(m.ShiftInstance.shift_instance_id).limit(1)).scalar()
    runner.run_detection(s, shift_instance_ids=[shift], detected_at=NOW)
    assert _n(s, m.Anomaly) == total


def test_stale_rows_are_removed_when_the_cause_disappears(fresh):
    s = fresh
    runner.run_detection(s, detected_at=NOW)
    ev = s.execute(select(m.StateEvent).where(m.StateEvent.event_id.in_(
        json.loads(s.execute(select(m.GroundTruth.evidence_ids).where(
            m.GroundTruth.issue_type == "OVERLAPPING_STATES")).scalar())))).scalars().all()
    injected = max(ev, key=lambda e: e.event_id)             # the injected interval is the later-numbered one
    s.delete(injected)
    s.commit()
    out = runner.run_detection(s, detected_at=NOW)
    assert out["anomaly_store"]["deleted"] == 1
    assert "OVERLAPPING_STATES" not in {r.issue_type for r in s.execute(select(m.Anomaly)).scalars()}


def test_ground_truth_evaluation_and_links(tiny):
    s, out = tiny
    gt = out["ground_truth"]
    assert gt["labels"] == 5 and gt["detected"] == 5 and gt["missed"] == []
    assert gt["recall"] == 1.0 and gt["precision"] == 1.0 and gt["false_positive_anomalies"] == []
    assert gt["expected_decision_agreement"] == 5
    links = s.execute(select(AnomalyTruthLink).where(AnomalyTruthLink.source_kind == "ANOMALY")).scalars().all()
    assert len(links) == 5 and {l.match_type for l in links} == {"EXACT_TYPE"}
    again = ground_truth_ref.evaluate(s, evaluated_at=NOW)         # rewriting links is idempotent
    assert _n(s, AnomalyTruthLink) == again["links_written"]


def test_detection_does_not_modify_source_or_ground_truth(tiny):
    s, _ = tiny
    assert _n(s, m.GroundTruth) == 5
    assert _n(s, m.CountCorrection) == 4 and _n(s, m.ProductionCount) == 539 and _n(s, m.StateEvent) == 228
