"""Phase 4 pure-logic tests (no database, no pytest-only features: also runnable as plain functions)."""
from pathlib import Path

from backend.data_generator.config import EXPECTED_DECISION as GEN_DECISIONS
from backend.services.anomaly import config as C
from backend.services.anomaly import detectors as D
from backend.services.anomaly.schemas import ShiftKpi

M = "M-01-01"


def shift(i, avail=0.85, perf=0.90, qual=0.97, machine=M, status="OK", flags=(), evidence=None, **kw):
    sid = f"SHIFT-2026-01-{i:02d}-A"
    oee = None if None in (avail, perf, qual) else avail * perf * qual
    return ShiftKpi(aggregate_id=f"AGG-{machine}-{sid}", machine_id=machine, shift_instance_id=sid, status=status,
                    flags=tuple(flags), evidence=evidence or {}, availability=avail, performance=perf, quality=qual,
                    oee=oee, planned_production_seconds=kw.pop("planned", 26400.0), operating_seconds=22000.0,
                    total_count=1000, order_ids=kw.pop("order_ids", ("PO-2026-000001",)), **kw)


def normal(n=8, **kw):
    # small deterministic wobble around the target, well inside normal variation
    return [shift(i + 1, avail=0.85 + (0.01 if i % 2 else -0.01), perf=0.90 + (0.005 if i % 3 else -0.005), **kw)
            for i in range(n)]


def test_normal_data_has_no_alerts_and_no_warnings():
    res = D.detect(normal())
    assert res.alerts == [] and res.warnings == []


def test_availability_anomaly_is_detected_with_full_traceability():
    shifts = normal() + [shift(9, avail=0.55)]
    res = D.detect(shifts, support_fn=lambda s, k: ["EVT-M-01-01-000010", "EVT-M-01-01-000011"] if k == "availability" else [],
                   detected_at=None)
    assert len(res.alerts) == 1
    a = res.alerts[0]
    assert (a.anomaly_type, a.machine_id, a.shift_instance_id, a.kpi) == \
           ("AVAILABILITY_DROP", M, "SHIFT-2026-01-09-A", "availability")
    assert a.alert_id == f"ALT-AVAILABILITY_DROP-{M}-SHIFT-2026-01-09-A"
    assert a.measured_value == 0.55 and abs(a.baseline_value - 0.85) < 0.011 and a.baseline_n == 8
    assert a.deviation_score <= -C.Z_THRESHOLD
    assert a.evidence_ids[0] == f"AGG-{M}-SHIFT-2026-01-09-A" and "EVT-M-01-01-000010" in a.evidence_ids
    assert a.order_id == "PO-2026-000001"
    drop = a.baseline_value - a.measured_value
    assert abs(a.kpi_impact["estimated_oee_impact_pts"] - (-drop * 0.90 * 0.97 * 100)) < 0.05
    assert a.kpi_impact["lost_minutes"] > 0 and a.recommended_action and a.explanation


def test_performance_and_quality_drops_use_their_own_types():
    res = D.detect(normal() + [shift(9, perf=0.55), shift(10, qual=0.80)])
    assert sorted(a.anomaly_type for a in res.alerts) == ["PERFORMANCE_DROP", "QUALITY_DROP"]


def test_severity_thresholds():
    assert D.severity_for_drop(0.10, 0.10) == "LOW"
    assert D.severity_for_drop(0.16, 0.10) == "MEDIUM"
    assert D.severity_for_drop(0.20, 0.10) == "HIGH"
    assert D.severity_for_drop(0.31, 0.10) == "HIGH"
    res = D.detect(normal() + [shift(9, avail=0.55)])
    assert res.alerts[0].severity == "HIGH"
    res = D.detect(normal() + [shift(9, avail=0.715)])          # ~13.5 pts: past the gate, below 1.5x
    assert [a.severity for a in res.alerts] == ["LOW"]


def test_small_deviation_below_absolute_gate_is_not_alerted():
    assert D.detect(normal() + [shift(9, avail=0.78)]).alerts == []


def test_insufficient_data_never_alerts_and_is_reported():
    bad = [shift(i, avail=None, perf=None, qual=None, status="INSUFFICIENT_DATA") for i in range(1, 4)]
    res = D.detect(normal() + bad)
    assert res.alerts == []
    assert {s["reason"] for s in res.skipped} == {"INSUFFICIENT_DATA"}


def test_too_few_baseline_shifts_gives_no_alert():
    res = D.detect(normal(4) + [shift(9, avail=0.40)])          # only 4 other shifts
    assert res.alerts == []
    assert any(s["reason"] == "INSUFFICIENT_BASELINE" and s["kpi"] == "availability" for s in res.skipped)


def test_baselines_are_per_machine():
    other = [shift(i + 1, avail=0.55, machine="M-01-02") for i in range(8)]   # consistently low machine = its norm
    assert D.detect(normal() + other).alerts == []


def test_dq_flag_that_invalidates_a_kpi_skips_it_but_still_warns():
    s = shift(9, avail=0.40, flags=("OVERLAPPING_STATES",), evidence={"OVERLAPPING_STATES": ["EVT-A", "EVT-B"]},
              overlap_seconds=480.0)
    res = D.detect(normal() + [s])
    assert res.alerts == []                                        # availability not trusted -> no operational alert
    assert [(w.issue_type, w.severity) for w in res.warnings] == [("OVERLAPPING_STATES", "MEDIUM")]
    assert any(x["reason"].startswith("KPI_UNRELIABLE") for x in res.skipped)


def test_dq_flags_do_not_all_become_alerts_or_warnings():
    s = shift(9, flags=("UNRECONCILED_TIME", "OPERATING_IN_PLANNED_BREAK"),
              evidence={"UNRECONCILED_TIME": ["SHIFT-X"], "OPERATING_IN_PLANNED_BREAK": ["EVT-1"]})
    res = D.detect(normal() + [s])
    assert res.alerts == [] and res.warnings == []
    assert res.dq_flags_not_alerted == {"UNRECONCILED_TIME": 1, "OPERATING_IN_PLANNED_BREAK": 1}


def test_scrap_exceeds_total_warning_has_evidence_and_decision():
    s = shift(9, qual=None, flags=("SCRAP_EXCEEDS_TOTAL",), evidence={"SCRAP_EXCEEDS_TOTAL": ["QC-2026-000539"]},
              order_ids=())
    res = D.detect(normal() + [s])
    assert res.alerts == []
    w = res.warnings[0]
    assert (w.issue_type, w.severity, w.evidence_ids, w.expected_decision) == \
           ("SCRAP_EXCEEDS_TOTAL", "HIGH", ["QC-2026-000539"], "QUARANTINE_QUALITY_AND_REVIEW")
    assert w.machine_id == M and w.shift_instance_id == "SHIFT-2026-01-09-A" and w.aggregate_id.startswith("AGG-")


def test_wrong_cycle_time_gives_one_warning_and_takes_order_from_evidence():
    s = shift(9, perf=2.2, flags=("CONFIG_CONFLICT", "PERFORMANCE_OVER_100"),
              evidence={"CONFIG_CONFLICT": ["CFG-A-1", "CFG-A-2", "PO-2026-000014"], "PERFORMANCE_OVER_100": ["PO-2026-000014"]})
    res = D.detect(normal() + [s])
    assert [(w.issue_type, w.source_flag, w.severity, w.order_id) for w in res.warnings] == \
           [("WRONG_CYCLE_TIME", "CONFIG_CONFLICT", "HIGH", "PO-2026-000014")]
    assert res.alerts == []                                        # performance untrusted, not an operational drop


def test_downtime_in_break_and_count_decrease_severity():
    s = shift(9, flags=("DOWNTIME_IN_PLANNED_BREAK", "COUNT_DECREASE"), break_excluded_downtime_seconds=900.0,
              evidence={"DOWNTIME_IN_PLANNED_BREAK": ["EVT-9", "PBRK-1"], "COUNT_DECREASE": ["CNT-2", "CNT-1"]})
    sev = {w.issue_type: w.severity for w in D.detect(normal() + [s]).warnings}
    assert sev == {"DOWNTIME_IN_PLANNED_BREAK": "MEDIUM", "COUNT_DECREASE": "MEDIUM"}


def test_detection_is_deterministic():
    data = normal() + [shift(9, avail=0.55)]
    assert [a.to_dict() for a in D.detect(data).alerts] == [a.to_dict() for a in D.detect(data).alerts]


def test_expected_decisions_match_the_generator():
    assert C.EXPECTED_DECISION == dict(GEN_DECISIONS)


def test_detection_modules_never_touch_ground_truth_labels():
    base = Path(__file__).resolve().parent.parent / "backend" / "services" / "anomaly"
    for name in ("detectors.py", "store.py", "runner.py", "config.py", "schemas.py"):
        text = (base / name).read_text(encoding="utf-8")
        assert "GroundTruth" not in text, name
    assert "from backend.services.anomaly import ground_truth_ref" in (base / "runner.py").read_text(encoding="utf-8")
