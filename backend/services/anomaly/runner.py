"""Phase 4 orchestration. Reads SOURCE tables through the Phase 3 engine (compute only) and never reads ground_truth.

CLI (PowerShell, from the project root):
    python -m backend.services.anomaly.runner --db data/tiny.db
    python -m backend.services.anomaly.runner --db data/tiny.db --evaluate --report data/anomaly_report.json
"""
import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from backend import models as m
from backend.database import ROOT, _enable_fk
from backend.services.anomaly import detectors, store
from backend.services.anomaly.schemas import ShiftKpi
from backend.services.oee import engine as oee_engine

DOWNTIME = ("BREAKDOWN", "IDLE", "CHANGEOVER")


def to_shift_kpi(r) -> ShiftKpi:
    return ShiftKpi(
        aggregate_id=r.aggregate_id, machine_id=r.machine_id, shift_instance_id=r.shift_instance_id,
        status=r.status, flags=tuple(r.flags), evidence={k: list(v) for k, v in r.evidence.items()},
        availability=r.availability, performance=r.performance, quality=r.quality, oee=r.oee,
        planned_production_seconds=r.planned_production_seconds, operating_seconds=r.operating_seconds,
        downtime_seconds=r.downtime_seconds, unreconciled_seconds=r.unreconciled_seconds,
        overlap_seconds=r.overlap_seconds, break_excluded_downtime_seconds=r.break_excluded_downtime_seconds,
        total_count=r.total_count, order_ids=tuple(d.order_id for d in r.details),
        config_ids=tuple(d.config_id for d in r.details if d.config_id))


def _make_support(session):
    win = {s.shift_instance_id: (s.start_ts, s.end_ts) for s in session.execute(select(m.ShiftInstance)).scalars()}

    def support(s: ShiftKpi, kpi: str):
        lo, hi = win[s.shift_instance_id]
        if kpi == "availability":
            evs = session.execute(select(m.StateEvent).where(
                m.StateEvent.machine_id == s.machine_id, m.StateEvent.state.in_(DOWNTIME),
                m.StateEvent.start_ts < hi, m.StateEvent.end_ts > lo)).scalars().all()
            evs.sort(key=lambda e: (-(min(e.end_ts, hi) - max(e.start_ts, lo)).total_seconds(), e.event_id))
            return [e.event_id for e in evs[:5]]
        if kpi == "performance":
            return list(s.order_ids) + list(s.config_ids)
        qs = session.execute(select(m.QualityInspection).where(
            m.QualityInspection.machine_id == s.machine_id, m.QualityInspection.ts > lo,
            m.QualityInspection.ts <= hi)).scalars().all()
        qs = sorted((q for q in qs if q.scrap_qty + q.rework_qty > 0),
                    key=lambda q: (-(q.scrap_qty + q.rework_qty), q.inspection_id))
        return [q.inspection_id for q in qs[:5]] + list(s.order_ids)
    return support


def _make_order_resolver(session):
    def resolve(evidence_ids):
        for i in evidence_ids:
            for model, col, attr in ((m.StateEvent, m.StateEvent.event_id, "order_id"),
                                     (m.ProductionCount, m.ProductionCount.count_id, "order_id"),
                                     (m.QualityInspection, m.QualityInspection.inspection_id, "order_id")):
                if i.startswith(("EVT-", "CNT-", "QC-")):
                    row = session.execute(select(model).where(col == i)).scalars().first()
                    if row is not None and getattr(row, attr):
                        return getattr(row, attr)
        return None
    return resolve


def run_detection(session, machine_ids=None, shift_instance_ids=None, detected_at=None, persist=True,
                  evaluate_ground_truth=False) -> dict:
    detected_at = detected_at or datetime.now().replace(microsecond=0)
    # Baselines need every shift of a machine, so compute all shifts and only filter what is reported/persisted.
    results = oee_engine.compute(session, machine_ids=machine_ids, computed_at=detected_at)
    shifts = [to_shift_kpi(r) for r in results]
    res = detectors.detect(shifts, _make_support(session), _make_order_resolver(session), detected_at)
    if shift_instance_ids is not None:
        keep = set(shift_instance_ids)
        res.alerts = [a for a in res.alerts if a.shift_instance_id in keep]
        res.warnings = [w for w in res.warnings if w.shift_instance_id in keep]
    summary = {"machine_shifts": res.machine_shifts, "alerts": len(res.alerts), "warnings": len(res.warnings),
               "alerts_by_type": dict(Counter(a.anomaly_type for a in res.alerts)),
               "warnings_by_type": dict(Counter(w.issue_type for w in res.warnings)),
               "skipped_by_reason": dict(Counter(s["reason"] for s in res.skipped)),
               "dq_flags_not_alerted": res.dq_flags_not_alerted, "persisted": persist}
    if persist:
        summary["alert_store"] = store.persist_alerts(session, res.alerts, machine_ids, shift_instance_ids, detected_at)
        wstats = store.persist_warnings(session, res.warnings, machine_ids, shift_instance_ids, detected_at)
        wstats.pop("ids", None)
        summary["anomaly_store"] = wstats
        if evaluate_ground_truth:
            from backend.services.anomaly import ground_truth_ref   # post-hoc only, after detection is stored
            summary["ground_truth"] = ground_truth_ref.evaluate(session, evaluated_at=detected_at)
    summary["result"] = res
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description="Detect operational anomalies and data-quality warnings.")
    p.add_argument("--db", default=str(ROOT / "data" / "oee.db"))
    p.add_argument("--no-persist", action="store_true")
    p.add_argument("--evaluate", action="store_true", help="after detection, compare with ground truth (post-hoc)")
    p.add_argument("--report", default=None)
    a = p.parse_args(argv)
    if not Path(a.db).exists():
        print(f"ERROR: {a.db} not found")
        return 1
    eng = create_engine(f"sqlite:///{Path(a.db).as_posix()}", connect_args={"check_same_thread": False})
    event.listen(eng, "connect", _enable_fk)
    with Session(eng) as s:
        out = run_detection(s, persist=not a.no_persist, evaluate_ground_truth=a.evaluate and not a.no_persist)
    res = out.pop("result")
    print(json.dumps(out, indent=2, default=str))
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(json.dumps({"summary": out, "alerts": [x.to_dict() for x in res.alerts],
                                              "warnings": [x.to_dict() for x in res.warnings]},
                                             indent=2, default=str), encoding="utf-8")
    eng.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
