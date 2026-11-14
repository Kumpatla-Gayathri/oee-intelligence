"""Database layer for the OEE engine: load source rows, run the pure calculator, persist.

Reads ONLY source tables (never the ground-truth label table). Writes oee_aggregate / oee_aggregate_detail
(derived, always recomputable: existing rows for the recomputed machine-shifts are replaced).

CLI (PowerShell, from the project root):
    python -m backend.services.oee.engine --db data/tiny.db
    python -m backend.services.oee.engine --db data/oee.db --report data/oee_dq_report.json --no-persist
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, delete, event, select
from sqlalchemy.orm import Session

from backend import models as m
from backend.database import ROOT, _enable_fk
from backend.services.oee.calculator import compute_machine, rollup
from backend.services.oee.schemas import (
    BreakWindow, Cfg, Correction, Inspection, MachineData, OrderInfo, Reading, ShiftWindow, StateEvt,
)


def load_machine_data(session, machine_id) -> MachineData:
    d = MachineData()
    for e in session.execute(select(m.StateEvent).where(m.StateEvent.machine_id == machine_id)).scalars():
        d.events.append(StateEvt(e.event_id, e.state, e.start_ts, e.end_ts, e.order_id))
    for c in session.execute(select(m.ProductionCount).where(m.ProductionCount.machine_id == machine_id)).scalars():
        d.readings.setdefault(c.order_id, []).append(Reading(c.count_id, c.order_id, c.ts, c.counter_value))
    q = (select(m.CountCorrection).join(m.ProductionCount, m.ProductionCount.count_id == m.CountCorrection.count_id)
         .where(m.ProductionCount.machine_id == machine_id))
    for c in session.execute(q).scalars():
        d.corrections.setdefault(c.count_id, []).append(
            Correction(c.correction_id, c.count_id, c.corrected_counter_value, c.created_ts))
    for i in session.execute(select(m.QualityInspection).where(m.QualityInspection.machine_id == machine_id)).scalars():
        d.inspections.append(Inspection(i.inspection_id, i.order_id, i.ts, i.inspected_qty,
                                        i.good_qty, i.scrap_qty, i.rework_qty))
    for o in session.execute(select(m.ProductionOrder).where(m.ProductionOrder.machine_id == machine_id)).scalars():
        d.orders[o.order_id] = OrderInfo(o.order_id, o.product_id, o.actual_start, o.actual_end)
    for c in session.execute(select(m.MachineProductConfig).where(m.MachineProductConfig.machine_id == machine_id)).scalars():
        d.configs.append(Cfg(c.config_id, c.product_id, c.ideal_cycle_time_sec, c.effective_from, c.effective_to))
    return d


def compute(session, machine_ids=None, shift_instance_ids=None, computed_at=None) -> list:
    """Compute AggregateResult objects (nothing is written). Returns them ordered by machine, shift."""
    computed_at = computed_at or datetime.now().replace(microsecond=0)
    shifts = [s for s in session.execute(select(m.ShiftInstance).order_by(m.ShiftInstance.start_ts)).scalars()
              if shift_instance_ids is None or s.shift_instance_id in set(shift_instance_ids)]
    breaks = defaultdict(list)
    for b in session.execute(select(m.PlannedBreak)).scalars():
        breaks[b.shift_instance_id].append(BreakWindow(b.planned_break_id, b.start_ts, b.end_ts))
    machines = session.execute(select(m.Machine).order_by(m.Machine.machine_id)).scalars().all()
    results = []
    for mc in machines:
        if machine_ids is not None and mc.machine_id not in set(machine_ids):
            continue
        wins = [ShiftWindow(s.shift_instance_id, s.start_ts, s.end_ts) for s in shifts
                if (mc.effective_from is None or mc.effective_from <= s.shift_date)
                and (mc.effective_to is None or s.shift_date <= mc.effective_to)]
        if wins:
            results += compute_machine(mc.machine_id, wins, breaks, load_machine_data(session, mc.machine_id),
                                       computed_at)
    return results


def persist(session, results, chunk=500):
    """Replace the derived rows for these machine-shifts with the freshly computed ones."""
    keys = [r.aggregate_id for r in results]
    for i in range(0, len(keys), chunk):
        part = keys[i:i + chunk]
        session.execute(delete(m.OEEAggregateDetail).where(m.OEEAggregateDetail.aggregate_id.in_(part)))
        session.execute(delete(m.OEEAggregate).where(m.OEEAggregate.aggregate_id.in_(part)))
    session.add_all([m.OEEAggregate(
        aggregate_id=r.aggregate_id, machine_id=r.machine_id, shift_instance_id=r.shift_instance_id,
        calendar_seconds=r.calendar_seconds, planned_break_seconds=r.planned_break_seconds,
        planned_production_seconds=r.planned_production_seconds, operating_seconds=r.operating_seconds,
        downtime_seconds=r.downtime_seconds, unreconciled_seconds=r.unreconciled_seconds,
        total_count=r.total_count, good_count=r.good_count, scrap_count=r.scrap_count,
        rework_count=r.rework_count, ideal_time_seconds=r.ideal_time_seconds,
        availability=r.availability, performance=r.performance, quality=r.quality, oee=r.oee,
        status=r.status, flags=r.flags_text, computed_at=r.computed_at) for r in results])
    session.flush()  # parents before details (FK enforcement is ON)
    session.add_all([m.OEEAggregateDetail(
        aggregate_id=r.aggregate_id, order_id=d.order_id, product_id=d.product_id,
        ideal_cycle_time_sec=d.ideal_cycle_time_sec, total_count=d.total_count,
        good_count=d.good_count, ideal_time_seconds=d.ideal_time_seconds)
        for r in results for d in r.details])
    session.commit()


def run(session, machine_ids=None, shift_instance_ids=None, persist_rows=True, computed_at=None) -> list:
    results = compute(session, machine_ids, shift_instance_ids, computed_at)
    if persist_rows:
        persist(session, results)
    return results


# ------------------------------------------------------------------ reporting
def line_rollups(session, results) -> dict:
    """label -> RollupResult for every line plus 'PLANT' (sum components, then recompute ratios)."""
    line_of = {mc.machine_id: mc.line_id for mc in session.execute(select(m.Machine)).scalars()}
    groups = defaultdict(list)
    for r in results:
        groups[line_of[r.machine_id]].append(r)
    out = {k: rollup(k, v) for k, v in sorted(groups.items())}
    out["PLANT"] = rollup("PLANT", results)
    return out


def data_quality_report(results) -> dict:
    """Machine-readable traceability: every flagged machine-shift with its evidence ids."""
    items = [{"aggregate_id": r.aggregate_id, "machine_id": r.machine_id,
              "shift_instance_id": r.shift_instance_id, "status": r.status, "flags": r.flags,
              "evidence": r.evidence, "corrections_applied": r.corrections_applied}
             for r in results if r.flags or r.corrections_applied or r.status != "OK"]
    return {"machine_shifts": len(results),
            "status_counts": dict(Counter(r.status for r in results)),
            "flag_counts": dict(Counter(f for r in results for f in r.flags)),
            "items": items}


def main(argv=None):
    p = argparse.ArgumentParser(description="Compute OEE aggregates from source records.")
    p.add_argument("--db", default=str(ROOT / "data" / "oee.db"))
    p.add_argument("--report", default=None, help="write the data-quality report JSON here")
    p.add_argument("--no-persist", action="store_true", help="compute and print only")
    a = p.parse_args(argv)
    if not Path(a.db).exists():
        print(f"ERROR: {a.db} not found")
        return 1
    engine = create_engine(f"sqlite:///{Path(a.db).as_posix()}", connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _enable_fk)
    with Session(engine) as s:
        results = run(s, persist_rows=not a.no_persist)
        roll = line_rollups(s, results)
    rep = data_quality_report(results)
    print(f"db={a.db} machine_shifts={rep['machine_shifts']} persisted={not a.no_persist}")
    print("status:", rep["status_counts"])
    print("flags: ", rep["flag_counts"])
    for k, r in roll.items():
        f = lambda v: "n/a" if v is None else f"{v:.1%}"
        print(f"  {k:<8} A={f(r.availability)} P={f(r.performance)} Q={f(r.quality)} OEE={f(r.oee)} [{r.status}]")
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(json.dumps(rep, indent=2), encoding="utf-8")
        print("report written:", a.report)
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
