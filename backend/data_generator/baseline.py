"""Baseline (valid-only) data generation.

Order: master data -> shift calendar -> machine/product configs -> per machine, per shift:
orders -> state events -> count readings -> legitimate corrections -> quality inspections.

Everything derived (counter totals, planned quantity, good = inspected - scrap - rework)
is computed from the simulated source records, never drawn independently.
Baseline rule: state events are contiguous inside working time, never overlap, and
NEVER fall inside a planned break (breaks live only in the planned_break table).
"""
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import NamedTuple

from sqlalchemy import insert

from backend.data_generator import ids
from backend.data_generator.config import (
    BREAKDOWN_REASONS, BREAK_TEMPLATES, CHANGEOVER_REASON, IDLE_REASONS, MACHINE_TYPES,
    MICRO_REASONS, PLANT_ID, PLANT_NAME, REASONS, SHIFT_DEFS, START_DATE, Scale,
)
from backend import models as m

# Parent tables must be inserted before children. The buffer always flushes in this order.
INSERT_ORDER = (
    m.Plant, m.Line, m.Machine, m.Product, m.DowntimeReason,
    m.Shift, m.ShiftBreakTemplate, m.ShiftInstance, m.PlannedBreak,
    m.MachineProductConfig, m.ProductionOrder, m.StateEvent,
    m.ProductionCount, m.CountCorrection, m.QualityInspection, m.GroundTruth,
)


class MachineShift(NamedTuple):
    machine_id: str
    shift_instance_id: str
    start: datetime
    end: datetime


@dataclass
class GenState:
    """Counters and lookups shared with the injector so IDs keep increasing consistently."""
    seed: int
    po_seq: int = 0
    qc_seq: int = 0
    gt_seq: int = 0
    evt_seq: dict = field(default_factory=dict)    # machine_id -> last event number
    cnt_seq: dict = field(default_factory=dict)    # order_id -> last count number
    cfg_seq: dict = field(default_factory=dict)    # (machine, product) -> last config number
    ideal_ct: dict = field(default_factory=dict)   # (machine, product) -> baseline ideal cycle time (s)
    machine_shifts: list = field(default_factory=list)

    def next_event(self, machine: str) -> str:
        self.evt_seq[machine] = self.evt_seq.get(machine, 0) + 1
        return ids.event_id(machine, self.evt_seq[machine])

    def next_count(self, order: str) -> str:
        self.cnt_seq[order] = self.cnt_seq.get(order, 0) + 1
        return ids.count_id(order, self.cnt_seq[order])

    def next_qc(self) -> str:
        self.qc_seq += 1
        return ids.qc_id(self.qc_seq)

    def next_order(self) -> str:
        self.po_seq += 1
        return ids.order_id(self.po_seq)

    def next_truth(self) -> str:
        self.gt_seq += 1
        return ids.truth_id(self.gt_seq)


class Buffer:
    """Collects row dicts per table and bulk-inserts them in parent-before-child order."""

    def __init__(self, session, batch_size: int):
        self.session = session
        self.batch_size = batch_size
        self.rows = {model: [] for model in INSERT_ORDER}
        self.pending = 0

    def add(self, model, row: dict):
        self.rows[model].append(row)
        self.pending += 1

    def maybe_flush(self):
        if self.pending >= self.batch_size:
            self.flush()

    def flush(self):
        for model in INSERT_ORDER:
            rows = self.rows[model]
            if rows:
                self.session.execute(insert(model), rows)
                rows.clear()
        self.session.commit()
        self.pending = 0


def _overlap_seconds(a0, a1, b0, b1) -> float:
    lo, hi = max(a0, b0), min(a1, b1)
    return max(0.0, (hi - lo).total_seconds())


def _stochastic_round(rng, x: float) -> int:
    base = int(x)
    return base + (1 if rng.random() < x - base else 0)


def _segments(start, end, breaks):
    """Working segments of a shift = shift window minus planned breaks."""
    segs, cur = [], start
    for _, _, bs, be in sorted(breaks, key=lambda b: b[2]):
        if bs > cur:
            segs.append((cur, bs))
        cur = be
    if end > cur:
        segs.append((cur, end))
    return segs


def _piece_events(rng, start, end, order_idx, mean_run_s):
    """Contiguous, non-overlapping state intervals for one working piece."""
    if order_idx == -1:
        return [("CHANGEOVER", start, end, CHANGEOVER_REASON)]
    evs, cur = [], start
    while cur < end:
        run = max(60, int(rng.expovariate(1.0 / mean_run_s)))
        run_end = min(end, cur + timedelta(seconds=run))
        evs.append(("RUN", cur, run_end, None))
        cur = run_end
        if cur >= end:
            break
        r = rng.random()
        if r < 0.70:
            state, dur, reason = "MICRO_STOP", rng.randint(30, 120), rng.choice(MICRO_REASONS)
        elif r < 0.95:
            state, dur, reason = "BREAKDOWN", rng.randint(300, 1800), rng.choice(BREAKDOWN_REASONS)
        else:
            state, dur, reason = "IDLE", rng.randint(180, 600), rng.choice(IDLE_REASONS)
        d_end = min(end, cur + timedelta(seconds=dur))
        evs.append((state, cur, d_end, reason))
        cur = d_end
    return evs


def _reading_times(start, end, interval):
    times, t = [], start + interval
    while t < end:
        times.append(t)
        t += interval
    times.append(end)
    return times


def _build_machine_shift(rng, scale, state, buf, machine_id, mparams, si, mprods):
    si_id, si_start, si_end, breaks = si
    segs = _segments(si_start, si_end, breaks)

    # 1) optional second product -> changeover inside one long working segment
    changeover = None
    if rng.random() < scale.two_order_prob:
        eligible = [i for i, (s, e) in enumerate(segs) if (e - s).total_seconds() >= 75 * 60]
        if eligible:
            i = rng.choice(eligible)
            s, e = segs[i]
            seg_min = int((e - s).total_seconds() // 60)
            off = rng.randint(20, seg_min - 35)
            dur = rng.randint(10, 25)
            changeover = (i, s + timedelta(minutes=off), s + timedelta(minutes=off + dur))
    pieces = []  # (start, end, order_idx) ; order_idx -1 = changeover
    for i, (s, e) in enumerate(segs):
        if changeover and changeover[0] == i:
            pieces += [(s, changeover[1], 0), (changeover[1], changeover[2], -1), (changeover[2], e, 1)]
        else:
            pieces.append((s, e, 0 if (not changeover or i < changeover[0]) else 1))
    n_orders = 2 if changeover else 1

    p0 = rng.choice(mprods)
    products = [p0] + ([rng.choice([p for p in mprods if p != p0])] if n_orders == 2 else [])
    order_ids = [state.next_order() for _ in range(n_orders)]
    perf = min(0.98, max(0.55, rng.gauss(0.86 + mparams["perf_bias"], 0.07)))
    scrap_rate = rng.uniform(0.005, 0.04)
    rework_rate = rng.uniform(0.002, 0.02)
    mean_run_s = scale.mean_run_min * 60 * mparams["mtbf"]
    interval = timedelta(minutes=scale.count_interval_min)

    # 2) state events (chronological) and count readings per order
    events = []           # (state, start, end, reason, order_idx)
    readings = {0: [], 1: []}  # order_idx -> [(ts, true_cumulative, inspected_increment)]
    cum = {0: 0.0, 1: 0.0}
    last_true = {0: 0, 1: 0}
    for (s, e, oi) in pieces:
        piece_ev = _piece_events(rng, s, e, oi, mean_run_s)
        for (st, a, b, rsn) in piece_ev:
            events.append((st, a, b, rsn, oi))
        if oi == -1:
            continue
        ct = state.ideal_ct[(machine_id, products[oi])]
        run_ev = [(a, b) for (st, a, b, _) in piece_ev if st == "RUN"]
        prev = s
        for t in _reading_times(s, e, interval):
            run_sec = sum(_overlap_seconds(prev, t, a, b) for a, b in run_ev)
            cum[oi] += run_sec * perf / ct
            true_val = int(cum[oi])
            readings[oi].append((t, true_val, true_val - last_true[oi]))
            last_true[oi] = true_val
            prev = t

    # 3) rows: orders first (planned_qty derived from the simulated counter), then children
    for oi in range(n_orders):
        ow = [(s, e) for (s, e, k) in pieces if k == oi]
        o_start, o_end = ow[0][0], ow[-1][1]
        buf.add(m.ProductionOrder, dict(
            order_id=order_ids[oi], machine_id=machine_id, product_id=products[oi],
            planned_qty=max(1, int(last_true[oi] * 1.03)), status="COMPLETED",
            planned_start=o_start, planned_end=o_end, actual_start=o_start, actual_end=o_end))
    for (st, a, b, rsn, oi) in events:
        buf.add(m.StateEvent, dict(
            event_id=state.next_event(machine_id), machine_id=machine_id,
            order_id=order_ids[oi] if oi >= 0 else None, state=st, start_ts=a, end_ts=b, reason_id=rsn))

    # one legitimate counter glitch + append-only correction in some machine-shifts
    glitch_idx = None
    if rng.random() < scale.correction_prob:
        cands = [j for j in range(1, len(readings[0])) if readings[0][j - 1][1] >= 3]
        if cands:
            glitch_idx = rng.choice(cands)
    qc_rows = []
    for oi in range(n_orders):
        prev_raw = 0
        for j, (ts, true_val, inc_true) in enumerate(readings[oi]):
            raw = true_val
            cid = state.next_count(order_ids[oi])
            if oi == 0 and j == glitch_idx:
                raw = readings[oi][j - 1][1] - rng.randint(1, min(5, readings[oi][j - 1][1]))
                buf.add(m.CountCorrection, dict(
                    correction_id=ids.correction_id(cid), count_id=cid, order_id=order_ids[oi],
                    corrected_counter_value=true_val, reason="Counter glitch corrected from line PLC log",
                    created_ts=ts + timedelta(minutes=5), approved_by="supervisor"))
            buf.add(m.ProductionCount, dict(
                count_id=cid, order_id=order_ids[oi], machine_id=machine_id, ts=ts,
                counter_value=raw, increment_qty=raw - prev_raw))
            prev_raw = raw
            scrap = min(inc_true, _stochastic_round(rng, inc_true * scrap_rate))
            rework = min(inc_true - scrap, _stochastic_round(rng, inc_true * rework_rate))
            qc_rows.append(dict(
                inspection_id=state.next_qc(), order_id=order_ids[oi], machine_id=machine_id, ts=ts,
                inspected_qty=inc_true, good_qty=inc_true - scrap - rework,
                scrap_qty=scrap, rework_qty=rework))
    for row in qc_rows:
        buf.add(m.QualityInspection, row)
    state.machine_shifts.append(MachineShift(machine_id, si_id, si_start, si_end))


def generate_baseline(scale: Scale, seed: int, buf: Buffer) -> GenState:
    state = GenState(seed=seed)
    master_rng = random.Random(f"{seed}:master")

    # ---- master data ----
    buf.add(m.Plant, dict(plant_id=PLANT_ID, name=PLANT_NAME, timezone="Asia/Kolkata", status="ACTIVE"))
    per_line = scale.machines // scale.lines
    assert per_line * scale.lines == scale.machines, "machines must divide evenly across lines"
    machines = []
    for ln in range(1, scale.lines + 1):
        buf.add(m.Line, dict(line_id=ids.line_id(ln), plant_id=PLANT_ID, name=f"Line {ln}", status="ACTIVE"))
        for mn in range(1, per_line + 1):
            mid = ids.machine_id(ln, mn)
            machines.append(mid)
            buf.add(m.Machine, dict(
                machine_id=mid, line_id=ids.line_id(ln), name=f"Machine {ln}-{mn}",
                machine_type=MACHINE_TYPES[(mn - 1) % len(MACHINE_TYPES)], status="ACTIVE",
                effective_from=START_DATE.replace(year=START_DATE.year - 1), effective_to=None))
    base_ct = {}
    for pn in range(1, scale.products + 1):
        pid = ids.product_id(pn)
        base_ct[pid] = round(master_rng.uniform(6.0, 30.0), 2)
        buf.add(m.Product, dict(product_id=pid, name=f"Product {pn:03d}", unit="pcs", status="ACTIVE"))
    for rid, code, desc, cat in REASONS:
        buf.add(m.DowntimeReason, dict(reason_id=rid, code=code, description=desc, category=cat))

    # ---- shift definitions, instances (actual dated shifts) and planned breaks ----
    instances = []
    for code, st, en, cross in SHIFT_DEFS:
        sid = ids.shift_def_id(code)
        buf.add(m.Shift, dict(shift_id=sid, plant_id=PLANT_ID, code=code, name=f"Shift {code}",
                              start_time=st, end_time=en, crosses_midnight=cross))
        for n, (off, dur, name) in enumerate(BREAK_TEMPLATES, 1):
            buf.add(m.ShiftBreakTemplate, dict(
                template_id=ids.break_template_id(code, n), shift_id=sid, name=name,
                start_offset_min=off, duration_min=dur))
    for d in range(scale.days):
        day = START_DATE + timedelta(days=d)
        for code, st, en, cross in SHIFT_DEFS:
            si_id = ids.shift_instance_id(day, code)
            start = datetime.combine(day, st)
            end = start + timedelta(minutes=480)
            buf.add(m.ShiftInstance, dict(
                shift_instance_id=si_id, shift_id=ids.shift_def_id(code), plant_id=PLANT_ID,
                shift_date=day, start_ts=start, end_ts=end, status="COMPLETED"))
            brks = []
            for n, (off, dur, name) in enumerate(BREAK_TEMPLATES, 1):
                pb = ids.planned_break_id(si_id, n)
                bs = start + timedelta(minutes=off)
                be = bs + timedelta(minutes=dur)
                brks.append((pb, name, bs, be))
                buf.add(m.PlannedBreak, dict(planned_break_id=pb, shift_instance_id=si_id,
                                             name=name, start_ts=bs, end_ts=be))
            instances.append((si_id, start, end, brks))

    # ---- machine<->product config with ideal cycle time (effective-dated) ----
    cfg_from = datetime.combine(START_DATE, datetime.min.time()) - timedelta(days=1)
    mprod = {}
    for mid in machines:
        chosen = sorted(master_rng.sample(sorted(base_ct), scale.products_per_machine))
        mprod[mid] = chosen
        for pid in chosen:
            ct = round(base_ct[pid] * master_rng.uniform(0.9, 1.1), 2)
            state.ideal_ct[(mid, pid)] = ct
            state.cfg_seq[(mid, pid)] = 1
            buf.add(m.MachineProductConfig, dict(
                config_id=ids.config_id(mid, pid, 1), machine_id=mid, product_id=pid,
                ideal_cycle_time_sec=ct, effective_from=cfg_from, effective_to=None))
    buf.flush()

    # ---- transactions, one machine at a time (independent RNG per machine) ----
    for mid in machines:
        rng = random.Random(f"{seed}:{mid}")
        mparams = {"mtbf": rng.uniform(0.6, 1.4), "perf_bias": rng.gauss(0.0, 0.04)}
        for si in instances:
            _build_machine_shift(rng, scale, state, buf, mid, mparams, si, mprod[mid])
            buf.maybe_flush()
    buf.flush()
    return state
