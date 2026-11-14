"""Read-side helpers for the API. Reuses the Phase 3 engine (rollup / line_rollups / compute) and Phase 4 tables.
Nothing here writes to the database."""
import json
from collections import defaultdict

from sqlalchemy import select

from backend import models as m
from backend.services.oee import engine as oee_engine
from backend.services.oee.calculator import rollup
from backend.services.oee.schemas import AggregateResult

SEV_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _row_to_result(r) -> AggregateResult:
    return AggregateResult(
        aggregate_id=r.aggregate_id, machine_id=r.machine_id, shift_instance_id=r.shift_instance_id,
        calendar_seconds=r.calendar_seconds, planned_break_seconds=r.planned_break_seconds,
        planned_production_seconds=r.planned_production_seconds, operating_seconds=r.operating_seconds,
        downtime_seconds=r.downtime_seconds, unreconciled_seconds=r.unreconciled_seconds,
        total_count=r.total_count, good_count=r.good_count, scrap_count=r.scrap_count,
        rework_count=r.rework_count, ideal_time_seconds=r.ideal_time_seconds, availability=r.availability,
        performance=r.performance, quality=r.quality, oee=r.oee, status=r.status,
        flags=r.flags.split(",") if r.flags else [], computed_at=r.computed_at)


def load_results(db, date_from=None, date_to=None, machine_id=None):
    """(results, source, shift_map). Uses persisted oee_aggregate rows when present, else computes (not persisted)."""
    shifts = {s.shift_instance_id: s for s in db.execute(select(m.ShiftInstance)).scalars()}
    rows = db.execute(select(m.OEEAggregate)).scalars().all()
    if rows:
        results, source = [_row_to_result(r) for r in rows], "oee_aggregate"
    else:
        results, source = oee_engine.compute(db), "computed"

    def keep(r):
        s = shifts.get(r.shift_instance_id)
        if machine_id and r.machine_id != machine_id:
            return False
        if s is not None and ((date_from and s.shift_date < date_from) or (date_to and s.shift_date > date_to)):
            return False
        return True
    results = [r for r in results if keep(r)]
    results.sort(key=lambda r: (r.machine_id, shifts[r.shift_instance_id].start_ts if r.shift_instance_id in shifts
                                else None, r.shift_instance_id))
    return results, source, shifts


def kpi_dict(roll, source):
    d = roll.to_dict()
    d["source"] = source
    d["machine_shifts"] = d.pop("members")
    return d


def plant_kpi(results, source):
    return kpi_dict(rollup("PLANT", results), source)


def line_kpis(db, results, source):
    rolls = oee_engine.line_rollups(db, results) if results else {}
    return [kpi_dict(v, source) | {"line_id": k} for k, v in rolls.items() if k != "PLANT"]


def summarize(r, shifts):
    s = shifts.get(r.shift_instance_id)
    return {"aggregate_id": r.aggregate_id, "machine_id": r.machine_id, "shift_instance_id": r.shift_instance_id,
            "shift_date": s.shift_date.isoformat() if s else None, "availability": r.availability,
            "performance": r.performance, "quality": r.quality, "oee": r.oee, "status": r.status,
            "flags": list(r.flags), "total_count": r.total_count, "good_count": r.good_count,
            "scrap_count": r.scrap_count, "rework_count": r.rework_count,
            "planned_production_seconds": r.planned_production_seconds,
            "operating_seconds": r.operating_seconds, "downtime_seconds": r.downtime_seconds}


def alert_dict(a) -> dict:
    return {"alert_id": a.alert_id, "anomaly_type": a.anomaly_type, "machine_id": a.machine_id,
            "shift_instance_id": a.shift_instance_id, "aggregate_id": f"AGG-{a.machine_id}-{a.shift_instance_id}",
            "order_id": a.order_id, "severity": a.severity, "kpi": a.kpi, "measured_value": a.measured_value,
            "baseline_value": a.baseline_value, "baseline_n": a.baseline_n, "deviation_score": a.deviation_score,
            "kpi_impact": json.loads(a.kpi_impact), "evidence_ids": json.loads(a.evidence_ids),
            "explanation": a.explanation, "recommended_action": a.recommended_action,
            "ground_truth_ref": a.ground_truth_ref, "detected_at": a.detected_at}


def warning_dict(a) -> dict:
    return {"anomaly_id": a.anomaly_id, "issue_type": a.issue_type, "machine_id": a.machine_id,
            "shift_instance_id": a.shift_instance_id, "order_id": a.order_id, "severity": a.severity,
            "evidence_ids": json.loads(a.evidence_ids), "explanation": a.explanation,
            "expected_decision": a.expected_decision, "detected_at": a.detected_at}
