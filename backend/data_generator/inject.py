"""Labelled edge-case injection, run AFTER the valid baseline exists.

Rules:
* Injection only ADDS new rows (events, counts, inspections, configs). It never edits or
  deletes a valid baseline record.
* Every injected anomaly gets one ground_truth row (issue_type, evidence ids, expected decision).
* ground_truth has no foreign keys and is never read by detectors (Phase 4 enforces this).
* Each anomaly lives in its own machine-shift so one injection cannot trigger another type.
"""
import json
from datetime import timedelta

from sqlalchemy import select

from backend import models as m
from backend.data_generator.baseline import Buffer, GenState
from backend.data_generator.config import (
    EXPECTED_DECISION, GENERATED_AT, INJECTED_BREAKDOWN_REASON, Scale,
)
from backend.data_generator import ids


def _truth(state: GenState, buf: Buffer, issue: str, machine: str, order, evidence: list):
    buf.add(m.GroundTruth, dict(
        truth_id=state.next_truth(), issue_type=issue, machine_id=machine, order_id=order,
        evidence_ids=json.dumps(evidence), expected_decision=EXPECTED_DECISION[issue],
        injected_at=GENERATED_AT))
    return True


def _breaks(session, si_id):
    return session.execute(select(m.PlannedBreak).where(
        m.PlannedBreak.shift_instance_id == si_id).order_by(m.PlannedBreak.start_ts)).scalars().all()


def _orders(session, cand):
    return session.execute(select(m.ProductionOrder).where(
        m.ProductionOrder.machine_id == cand.machine_id,
        m.ProductionOrder.actual_start >= cand.start,
        m.ProductionOrder.actual_start < cand.end).order_by(m.ProductionOrder.actual_start)).scalars().all()


def inject_overlap(session, buf, state, rng, cand):
    """Issue 1: a second state interval starts inside an existing RUN interval."""
    runs = session.execute(select(m.StateEvent).where(
        m.StateEvent.machine_id == cand.machine_id, m.StateEvent.state == "RUN",
        m.StateEvent.start_ts >= cand.start, m.StateEvent.end_ts <= cand.end)).scalars().all()
    runs = [r for r in runs if (r.end_ts - r.start_ts).total_seconds() >= 40 * 60]
    if not runs:
        return False
    run = rng.choice(runs)
    dur = (run.end_ts - run.start_ts).total_seconds()
    start = run.start_ts + timedelta(seconds=int(dur * 0.25))
    new_id = state.next_event(cand.machine_id)
    buf.add(m.StateEvent, dict(
        event_id=new_id, machine_id=cand.machine_id, order_id=run.order_id, state="BREAKDOWN",
        start_ts=start, end_ts=start + timedelta(minutes=8), reason_id=INJECTED_BREAKDOWN_REASON))
    return _truth(state, buf, "OVERLAPPING_STATES", cand.machine_id, run.order_id, [run.event_id, new_id])


def inject_count_decrease(session, buf, state, rng, cand):
    """Issue 2: an extra counter reading LOWER than the previous one, with no correction row."""
    brks = _breaks(session, cand.shift_instance_id)
    for order in rng.sample(_orders(session, cand), k=len(_orders(session, cand))):
        if session.execute(select(m.CountCorrection).where(
                m.CountCorrection.order_id == order.order_id)).first():
            continue
        rd = session.execute(select(m.ProductionCount).where(
            m.ProductionCount.order_id == order.order_id).order_by(m.ProductionCount.ts)).scalars().all()
        spots = []
        for j in range(1, len(rd) - 1):
            gap = (rd[j + 1].ts - rd[j].ts).total_seconds()
            mid = rd[j].ts + timedelta(seconds=int(gap // 2))
            if rd[j].counter_value >= 10 and gap >= 120 and not any(b.start_ts <= mid <= b.end_ts for b in brks):
                spots.append((rd[j], mid))
        if not spots:
            continue
        prev, mid = rng.choice(spots)
        drop = max(3, prev.counter_value // 20)
        new_id = state.next_count(order.order_id)
        buf.add(m.ProductionCount, dict(
            count_id=new_id, order_id=order.order_id, machine_id=cand.machine_id, ts=mid,
            counter_value=prev.counter_value - drop, increment_qty=-drop))
        return _truth(state, buf, "COUNT_DECREASE", cand.machine_id, order.order_id, [new_id, prev.count_id])
    return False


def inject_scrap_exceeds(session, buf, state, rng, cand):
    """Issue 3: an inspection where scrap is larger than the quantity inspected."""
    orders = _orders(session, cand)
    if not orders:
        return False
    order = rng.choice(orders)
    insp = session.execute(select(m.QualityInspection).where(
        m.QualityInspection.order_id == order.order_id).order_by(m.QualityInspection.ts).limit(1)).scalars().first()
    if insp is None:
        return False
    new_id = state.next_qc()
    buf.add(m.QualityInspection, dict(
        inspection_id=new_id, order_id=order.order_id, machine_id=cand.machine_id,
        ts=insp.ts + timedelta(seconds=1), inspected_qty=20, good_qty=0, scrap_qty=35, rework_qty=0))
    return _truth(state, buf, "SCRAP_EXCEEDS_TOTAL", cand.machine_id, order.order_id, [new_id])


def inject_wrong_cycle_time(session, buf, state, rng, cand):
    """Issue 4: an extra, short-lived config whose ideal cycle time is 2.5x too high (Performance > 100%)."""
    orders = _orders(session, cand)
    if not orders:
        return False
    order = rng.choice(orders)
    key = (cand.machine_id, order.product_id)
    state.cfg_seq[key] += 1
    cfg = ids.config_id(cand.machine_id, order.product_id, state.cfg_seq[key])
    buf.add(m.MachineProductConfig, dict(
        config_id=cfg, machine_id=cand.machine_id, product_id=order.product_id,
        ideal_cycle_time_sec=round(state.ideal_ct[key] * 2.5, 2),
        effective_from=cand.start, effective_to=cand.end + timedelta(seconds=1)))
    return _truth(state, buf, "WRONG_CYCLE_TIME", cand.machine_id, order.order_id, [cfg, order.order_id])


def inject_downtime_in_break(session, buf, state, rng, cand):
    """Issue 5: a BREAKDOWN interval placed inside the lunch break window."""
    brks = _breaks(session, cand.shift_instance_id)
    lunch = max(brks, key=lambda b: (b.end_ts - b.start_ts))
    start = lunch.start_ts + timedelta(minutes=5)
    order_id = None
    for o in _orders(session, cand):
        if o.actual_start <= lunch.start_ts <= o.actual_end:
            order_id = o.order_id
    new_id = state.next_event(cand.machine_id)
    buf.add(m.StateEvent, dict(
        event_id=new_id, machine_id=cand.machine_id, order_id=order_id, state="BREAKDOWN",
        start_ts=start, end_ts=start + timedelta(minutes=15), reason_id=INJECTED_BREAKDOWN_REASON))
    return _truth(state, buf, "DOWNTIME_IN_PLANNED_BREAK", cand.machine_id, order_id,
                  [new_id, lunch.planned_break_id])


INJECTORS = (
    ("OVERLAPPING_STATES", inject_overlap),
    ("COUNT_DECREASE", inject_count_decrease),
    ("SCRAP_EXCEEDS_TOTAL", inject_scrap_exceeds),
    ("WRONG_CYCLE_TIME", inject_wrong_cycle_time),
    ("DOWNTIME_IN_PLANNED_BREAK", inject_downtime_in_break),
)


def inject_all(session, buf: Buffer, state: GenState, scale: Scale, rng) -> int:
    """Inject scale.anomalies_per_type anomalies of each type. Returns the number injected."""
    candidates = list(state.machine_shifts)
    rng.shuffle(candidates)
    used, total = set(), 0
    for _, fn in INJECTORS:
        made = 0
        for cand in candidates:
            if made >= scale.anomalies_per_type:
                break
            key = (cand.machine_id, cand.shift_instance_id)
            if key in used:
                continue
            if fn(session, buf, state, rng, cand):
                used.add(key)
                made += 1
                total += 1
        buf.flush()  # later injectors must see rows added by earlier ones (e.g. new ids)
    return total
