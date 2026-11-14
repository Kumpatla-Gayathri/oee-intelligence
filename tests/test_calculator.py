"""Phase 3 unit tests: pure calculator, hand-computed expectations (no database needed)."""
from datetime import datetime, timedelta
from pathlib import Path

from backend.services.oee.calculator import (
    calculate_machine_shift, compute_machine, process_order_counter, rollup, select_config,
)
from backend.services.oee.schemas import (
    BreakWindow, CountDelta, Cfg, Correction, Inspection, MachineData, OrderInfo, Reading,
    ShiftWindow, StateEvt,
)

T0 = datetime(2026, 1, 1, 0, 0)
WIN = ShiftWindow("SI", T0, T0 + timedelta(hours=1))      # 3600 s


def mt(x):
    return T0 + timedelta(minutes=x)


def ev(eid, state, a, b, order="O1"):
    return StateEvt(eid, state, mt(a), mt(b), order)


def close(a, b, tol=1e-9):
    return a is not None and abs(a - b) < tol


ORD = {"O1": OrderInfo("O1", "P1", mt(0), mt(60))}
CFG = [Cfg("C1", "P1", 20.0, T0 - timedelta(days=1), None)]


def calc(events, breaks=(), deltas=(), rejects=(), insp=(), orders=ORD, configs=CFG, win=WIN):
    return calculate_machine_shift("M1", win, list(breaks), list(events), list(deltas), list(rejects),
                                   list(insp), orders, configs)


def test_clean_shift_hand_computed():
    r = calc(
        [ev("E1", "RUN", 0, 20), ev("E2", "BREAKDOWN", 20, 30), ev("E3", "RUN", 40, 55), ev("E4", "IDLE", 55, 60)],
        breaks=[BreakWindow("B1", mt(30), mt(40))],
        deltas=[CountDelta("O1", mt(30), 50, "C1"), CountDelta("O1", mt(60), 50, "C2")],
        insp=[Inspection("Q1", "O1", mt(30), 50, 48, 2, 0), Inspection("Q2", "O1", mt(60), 50, 47, 1, 2)])
    assert r.planned_break_seconds == 600 and r.planned_production_seconds == 3000
    assert r.operating_seconds == 2100 and r.downtime_seconds == 900 and r.unreconciled_seconds == 0
    assert r.total_count == 100 and r.scrap_count == 3 and r.rework_count == 2 and r.good_count == 95
    assert close(r.ideal_time_seconds, 2000.0)
    assert close(r.availability, 0.7) and close(r.performance, 2000 / 2100) and close(r.quality, 0.95)
    assert close(r.oee, 0.7 * (2000 / 2100) * 0.95)
    assert r.status == "OK" and r.flags == [] and r.aggregate_id == "AGG-M1-SI"
    assert len(r.details) == 1 and r.details[0].order_id == "O1" and r.details[0].good_count == 95


def test_micro_stop_is_operating_and_reduces_performance_not_availability():
    r = calc([ev("E1", "RUN", 0, 30), ev("E2", "MICRO_STOP", 30, 31), ev("E3", "RUN", 31, 60)],
             deltas=[CountDelta("O1", mt(60), 100, "C1")], insp=[Inspection("Q1", "O1", mt(60), 100, 100, 0, 0)])
    assert r.operating_seconds == 3600 and r.micro_stop_seconds == 60 and r.downtime_seconds == 0
    assert close(r.availability, 1.0) and close(r.performance, 2000 / 3600)


def test_overlap_counted_once_downtime_wins_and_evidence_kept():
    r = calc([ev("E1", "RUN", 0, 40), ev("E2", "BREAKDOWN", 20, 30), ev("E3", "RUN", 40, 60)])
    assert r.downtime_seconds == 600 and r.operating_seconds == 3000 and r.overlap_seconds == 600
    assert r.operating_seconds + r.downtime_seconds + r.unreconciled_seconds == r.planned_production_seconds
    assert "OVERLAPPING_STATES" in r.flags and r.evidence["OVERLAPPING_STATES"] == ["E1", "E2"]
    assert r.status == "DATA_QUALITY_WARNING"


def test_events_are_clipped_to_shift_and_half_open():
    nxt = ShiftWindow("SI2", mt(60), mt(120))
    data = MachineData(events=[ev("E0", "RUN", -30, 0), ev("E1", "RUN", -10, 30), ev("E2", "BREAKDOWN", 30, 70),
                               ev("E3", "RUN", 70, 130)], orders=ORD, configs=CFG)
    first, second = compute_machine("M1", [WIN, nxt], {}, data)
    # E0 ends exactly at shift start -> excluded; E1 clipped to [0,30); E2 split across shifts
    assert first.operating_seconds == 1800 and first.downtime_seconds == 1800 and first.unreconciled_seconds == 0
    assert second.downtime_seconds == 600 and second.operating_seconds == 3000 and second.unreconciled_seconds == 0


def test_downtime_in_planned_break_excluded_but_evidenced():
    r = calc([ev("E1", "RUN", 0, 30), ev("E2", "BREAKDOWN", 35, 38), ev("E3", "RUN", 40, 60), ev("E4", "IDLE", 30, 35),
              ev("E5", "IDLE", 38, 40)], breaks=[BreakWindow("B1", mt(30), mt(40))])
    # BREAKDOWN/IDLE inside the break are not downtime; the break is not double counted
    assert r.planned_production_seconds == 3000 and r.downtime_seconds == 0 and r.operating_seconds == 3000
    assert r.break_excluded_downtime_seconds == 600
    assert "DOWNTIME_IN_PLANNED_BREAK" in r.flags
    assert {"E2", "E4", "E5", "B1"} <= set(r.evidence["DOWNTIME_IN_PLANNED_BREAK"])


def test_uncovered_time_is_unreconciled_never_run():
    r = calc([ev("E1", "RUN", 0, 30)])
    assert r.operating_seconds == 1800 and r.unreconciled_seconds == 1800
    assert close(r.availability, 0.5) and "UNRECONCILED_TIME" in r.flags


def test_changeover_is_downtime_in_planned_production_time():
    r = calc([ev("E1", "RUN", 0, 50), ev("E2", "CHANGEOVER", 50, 60, None)])
    assert r.downtime_seconds == 600 and close(r.availability, 3000 / 3600)


def test_insufficient_data_when_no_state_events():
    r = calc([])
    assert r.status == "INSUFFICIENT_DATA" and r.availability is None and r.oee is None
    assert "NO_STATE_DATA" in r.flags and r.unreconciled_seconds == 3600


def test_counter_correction_replaces_raw_value():
    reads = [Reading("C1", "O1", mt(10), 10), Reading("C2", "O1", mt(20), 17), Reading("C3", "O1", mt(30), 30)]
    corr = {"C2": [Correction("COR-C2-1", "C2", 20, mt(35))]}
    deltas, rejects = process_order_counter("O1", reads, corr)
    assert [d.delta for d in deltas] == [10, 10, 10] and rejects == []
    assert deltas[1].correction_id == "COR-C2-1"


def test_uncorrected_decrease_is_excluded_and_flagged():
    reads = [Reading("C1", "O1", mt(10), 10), Reading("C2", "O1", mt(20), 20), Reading("C3", "O1", mt(25), 15),
             Reading("C4", "O1", mt(30), 30)]
    deltas, rejects = process_order_counter("O1", reads, {})
    assert [d.delta for d in deltas] == [10, 10, 10] and sum(d.delta for d in deltas) == 30
    assert len(rejects) == 1 and rejects[0].count_id == "C3" and rejects[0].prev_count_id == "C2" and rejects[0].drop == 5
    r = calc([ev("E1", "RUN", 0, 60)], deltas=deltas, rejects=rejects)
    assert r.total_count == 30 and r.evidence["COUNT_DECREASE"] == ["C3", "C2"]


def test_multi_product_ideal_time_is_not_an_average():
    orders = {"O1": OrderInfo("O1", "P1", mt(0), mt(30)), "O2": OrderInfo("O2", "P2", mt(30), mt(60))}
    cfgs = [Cfg("C1", "P1", 10.0, T0 - timedelta(days=1)), Cfg("C2", "P2", 30.0, T0 - timedelta(days=1))]
    r = calc([ev("E1", "RUN", 0, 30), ev("E2", "RUN", 30, 60, "O2")],
             deltas=[CountDelta("O1", mt(30), 100, "A1"), CountDelta("O2", mt(60), 300, "B1")],
             orders=orders, configs=cfgs)
    assert close(r.ideal_time_seconds, 100 * 10 + 300 * 30)       # 10000, not 400 * avg(20) = 8000
    assert {d.order_id: d.ideal_cycle_time_sec for d in r.details} == {"O1": 10.0, "O2": 30.0}


def test_overlapping_configs_latest_effective_from_wins():
    cfgs = [Cfg("OLD", "P1", 20.0, T0 - timedelta(days=1), None), Cfg("NEW", "P1", 50.0, T0, mt(61))]
    chosen, applicable = select_config(cfgs, "P1", mt(30))
    assert chosen.config_id == "NEW" and applicable == ["OLD", "NEW"]
    r = calc([ev("E1", "RUN", 0, 60)], deltas=[CountDelta("O1", mt(60), 100, "C1")], configs=cfgs)
    assert r.details[0].config_id == "NEW" and close(r.ideal_time_seconds, 5000.0)
    assert r.evidence["CONFIG_CONFLICT"] == ["OLD", "NEW", "O1"]


def test_config_touching_only_the_shift_boundary_is_not_applied():
    cfgs = [Cfg("OLD", "P1", 20.0, T0 - timedelta(days=1), None), Cfg("EDGE", "P1", 50.0, T0 - timedelta(hours=8), T0 + timedelta(seconds=1))]
    r = calc([ev("E1", "RUN", 0, 60)], deltas=[CountDelta("O1", mt(60), 100, "C1")], configs=cfgs)
    assert r.details[0].config_id == "OLD" and "CONFIG_CONFLICT" not in r.flags


def test_performance_over_100_is_kept_and_flagged_not_capped():
    r = calc([ev("E1", "RUN", 0, 60)], deltas=[CountDelta("O1", mt(60), 400, "C1")],
             insp=[Inspection("Q1", "O1", mt(60), 400, 400, 0, 0)])
    assert close(r.performance, 8000 / 3600) and r.performance > 1.0
    assert "PERFORMANCE_OVER_100" in r.flags and r.status == "DATA_QUALITY_WARNING"
    assert close(r.oee, 1.0 * (8000 / 3600) * 1.0)


def test_scrap_plus_rework_over_total_makes_quality_null_without_repair():
    r = calc([ev("E1", "RUN", 0, 60)], deltas=[CountDelta("O1", mt(60), 10, "C1")],
             insp=[Inspection("Q1", "O1", mt(60), 10, 0, 8, 5)])
    assert r.quality is None and r.oee is None and "SCRAP_EXCEEDS_TOTAL" in r.flags
    assert r.scrap_count == 8 and r.rework_count == 5 and r.good_count == -3   # reported as-is
    assert r.availability is not None and r.performance is not None


def test_inspection_row_with_scrap_over_inspected_quarantines_quality():
    r = calc([ev("E1", "RUN", 0, 60)], deltas=[CountDelta("O1", mt(60), 1000, "C1")],
             insp=[Inspection("Q1", "O1", mt(30), 500, 490, 10, 0), Inspection("Q2", "O1", mt(31), 20, 0, 35, 0)])
    assert r.total_count == 1000 and r.scrap_count == 45     # scrap+rework < total, row itself is bad
    assert r.quality is None and r.evidence["SCRAP_EXCEEDS_TOTAL"] == ["Q2"]


def test_missing_cycle_time_gives_null_performance_and_flag():
    r = calc([ev("E1", "RUN", 0, 60)], deltas=[CountDelta("O1", mt(60), 10, "C1")], configs=[])
    assert r.performance is None and r.oee is None and r.evidence["MISSING_CYCLE_TIME"] == ["O1"]


def test_snapshot_window_is_open_closed():
    nxt = ShiftWindow("SI2", mt(60), mt(120))
    reads = [Reading("C1", "O1", mt(30), 10), Reading("C2", "O1", mt(60), 20), Reading("C3", "O1", mt(90), 35)]
    data = MachineData(events=[ev("E1", "RUN", 0, 120)], readings={"O1": reads},
                       inspections=[Inspection("Q1", "O1", mt(60), 10, 10, 0, 0)], orders=ORD, configs=CFG)
    a, b = compute_machine("M1", [WIN, nxt], {}, data)
    assert a.total_count == 20 and b.total_count == 15      # reading exactly at shift end closes that shift
    assert a.scrap_count == 0 and sum(d.good_count for d in a.details) == a.good_count


def test_rollup_sums_components_instead_of_averaging_percentages():
    big = calc([ev("E1", "RUN", 0, 60)], deltas=[CountDelta("O1", mt(60), 100, "C1")],
               insp=[Inspection("Q1", "O1", mt(60), 100, 100, 0, 0)])
    small_win = ShiftWindow("SI9", T0, mt(10))
    small = calculate_machine_shift("M2", small_win, [], [ev("E9", "BREAKDOWN", 0, 10)], [], [], [], ORD, CFG)
    r = rollup("PLANT", [big, small])
    assert close(r.availability, 3600 / 4200)                      # not mean(1.0, 0.0) = 0.5
    assert close(r.performance, 2000 / 3600) and close(r.quality, 1.0)
    assert close(r.oee, (3600 / 4200) * (2000 / 3600) * 1.0)
    assert r.excluded_from_quality == ["AGG-M2-SI9"]


def test_engine_code_never_touches_ground_truth():
    base = Path(__file__).resolve().parent.parent / "backend" / "services" / "oee"
    for f in base.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert "GroundTruth" not in text and "ground_truth" not in text, f.name
