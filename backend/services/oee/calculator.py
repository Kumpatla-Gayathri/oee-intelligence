"""Pure OEE calculation for one machine in one ShiftInstance (no database access).

Rules implemented (approved Phase 3 business rules):
* RUN and MICRO_STOP = operating time. BREAKDOWN, IDLE, CHANGEOVER = downtime.
* Planned breaks are removed from planned production time; event time inside a break is
  excluded (never double counted as downtime) but reported as a flag with evidence.
* Time with no event is UNRECONCILED, never assumed RUN.
* Events are clipped to the shift and treated as half-open [start, end). Overlapping events
  are counted once; downtime takes precedence over operating; conflicting ids are kept.
* Counter readings and inspections are point-in-time snapshots, so they belong to the shift
  window (start, end]. (A reading at exactly shift end closes THAT shift.)
* Counters: append-only corrections replace the raw value; a reading that is still lower
  than the last accepted one is excluded (EXCLUDE_DELTA_REQUEST_CORRECTION) and flagged.
* Ideal cycle time: config applicable at the midpoint of the order's activity in the shift; latest
  effective_from wins when configs overlap (flagged CONFIG_CONFLICT). A config change in the
  middle of a shift is not split (documented limitation).
  Ideal time is summed per order, never from an averaged cycle time.
* Performance > 100% is kept and flagged, never capped.
* scrap + rework > total (or an inspection row with scrap + rework > inspected) -> Quality is
  NULL, flagged SCRAP_EXCEEDS_TOTAL. Nothing is repaired.
* Ratios of lines/plants are recomputed from summed components (see rollup()).
This module never reads ground-truth labels.
"""
from bisect import bisect_left, bisect_right
from collections import defaultdict

from backend.services.oee.intervals import clip, merge, overlap_seconds, seconds, subtract
from backend.services.oee.schemas import (
    AggregateResult, CountDelta, CountReject, DetailResult, RollupResult,
)

OPERATING_STATES = ("RUN", "MICRO_STOP")
DOWNTIME_STATES = ("BREAKDOWN", "IDLE", "CHANGEOVER")
EPS = 1e-6

# data-quality flags (names follow the Phase 2 issue types where one exists)
F_OVERLAP = "OVERLAPPING_STATES"
F_DT_IN_BREAK = "DOWNTIME_IN_PLANNED_BREAK"
F_OP_IN_BREAK = "OPERATING_IN_PLANNED_BREAK"
F_UNRECONCILED = "UNRECONCILED_TIME"
F_COUNT_DECREASE = "COUNT_DECREASE"
F_SCRAP = "SCRAP_EXCEEDS_TOTAL"
F_CONFIG = "CONFIG_CONFLICT"
F_NO_CONFIG = "MISSING_CYCLE_TIME"
F_PERF_OVER = "PERFORMANCE_OVER_100"
F_NO_COUNTS = "NO_COUNT_DATA"
F_NO_STATE = "NO_STATE_DATA"


def _ratio(num, den):
    return None if den is None or den <= EPS else num / den


# ------------------------------------------------------------------ counters
def process_order_counter(order_id, readings, corrections_by_count):
    """Turn one order's cumulative readings into accepted deltas and rejected readings."""
    deltas, rejects = [], []
    prev, prev_id = 0, None
    for r in sorted(readings, key=lambda x: (x.ts, x.count_id)):
        cs = corrections_by_count.get(r.count_id)
        corr = max(cs, key=lambda c: (c.created_ts, c.correction_id)) if cs else None
        value = corr.corrected_value if corr else r.counter_value
        cid = corr.correction_id if corr else None
        if value < prev:
            rejects.append(CountReject(order_id, r.ts, r.count_id, prev_id, prev - value, cid))
            continue
        deltas.append(CountDelta(order_id, r.ts, value - prev, r.count_id, cid))
        prev, prev_id = value, r.count_id
    return deltas, rejects


def select_config(configs, product_id, ref):
    """Applicable config with the latest effective_from. Returns (config|None, all_applicable_ids)."""
    app = [c for c in configs if c.product_id == product_id and c.effective_from <= ref
           and (c.effective_to is None or ref < c.effective_to)]
    if not app:
        return None, []
    app.sort(key=lambda c: (c.effective_from, c.config_id))
    return app[-1], [c.config_id for c in app]


# ------------------------------------------------------------------ time buckets
def _time_buckets(win, breaks, events, add_ev):
    lo, hi = win.start, win.end
    brk = []
    for b in breaks:
        c = clip(b.start, b.end, lo, hi)
        if c:
            brk.append((b.break_id, c[0], c[1]))
    union = merge([(s, e) for _, s, e in brk])
    working = subtract(lo, hi, union)
    t = {"calendar": seconds(lo, hi), "breaks": sum(seconds(s, e) for s, e in union)}
    t["planned"] = t["calendar"] - t["breaks"]
    t.update(operating=0.0, micro=0.0, downtime=0.0, unreconciled=0.0, overlap=0.0, break_dt=0.0,
             any_events=False)

    pieces = []
    for ev in events:
        c = clip(ev.start, ev.end, lo, hi)
        if c:
            pieces.append((ev, c[0], c[1]))
    for ev, a, b in pieces:                       # events falling inside planned breaks
        in_break = sum(overlap_seconds(a, b, s, e) for s, e in union)
        if in_break <= EPS:
            continue
        if ev.state in DOWNTIME_STATES:
            t["break_dt"] += in_break
            flag = F_DT_IN_BREAK
        elif ev.state in OPERATING_STATES:
            flag = F_OP_IN_BREAK
        else:
            continue
        ids = [ev.event_id] + [bid for bid, s, e in brk if overlap_seconds(a, b, s, e) > EPS]
        add_ev(flag, ids)

    groups = defaultdict(float)
    for ws, we in working:                        # sweep elementary segments of working time
        inner = [(ev, max(a, ws), min(b, we)) for ev, a, b in pieces if min(b, we) > max(a, ws)]
        if inner:
            t["any_events"] = True
        pts = sorted({ws, we, *[x for _, a, b in inner for x in (a, b)]})
        for x, y in zip(pts, pts[1:]):
            active = [ev for ev, a, b in inner if a <= x and b >= y]
            sec = (y - x).total_seconds()
            if not active:
                t["unreconciled"] += sec
                continue
            if len(active) > 1:
                t["overlap"] += sec
                groups[tuple(sorted(e.event_id for e in active))] += sec
            states = {e.state for e in active}
            if states & set(DOWNTIME_STATES):
                t["downtime"] += sec
            elif states & set(OPERATING_STATES):
                t["operating"] += sec
                if "RUN" not in states:
                    t["micro"] += sec
            else:                                  # e.g. PLANNED_BREAK state outside a break window
                t["unreconciled"] += sec
    if groups:
        add_ev(F_OVERLAP, sorted({i for k in groups for i in k}))
    return t


# ------------------------------------------------------------------ one machine-shift
def calculate_machine_shift(machine_id, win, breaks, events, deltas, rejects, inspections,
                            orders, configs, computed_at=None) -> AggregateResult:
    """deltas/rejects/inspections must already be restricted to the window (start, end]."""
    res = AggregateResult(aggregate_id=f"AGG-{machine_id}-{win.shift_instance_id}",
                          machine_id=machine_id, shift_instance_id=win.shift_instance_id,
                          computed_at=computed_at)
    evidence, flags = {}, set()

    def add_ev(flag, ids):
        flags.add(flag)
        bucket = evidence.setdefault(flag, [])
        for i in ids:
            if i not in bucket:
                bucket.append(i)

    t = _time_buckets(win, breaks, events, add_ev)
    res.calendar_seconds, res.planned_break_seconds = t["calendar"], t["breaks"]
    res.planned_production_seconds = t["planned"]
    res.operating_seconds, res.micro_stop_seconds = t["operating"], t["micro"]
    res.downtime_seconds, res.unreconciled_seconds = t["downtime"], t["unreconciled"]
    res.overlap_seconds, res.break_excluded_downtime_seconds = t["overlap"], t["break_dt"]
    if t["unreconciled"] > EPS:
        add_ev(F_UNRECONCILED, [win.shift_instance_id])

    # ---- counts, quality inputs and ideal time per order
    per_total, per_scrap, per_rework = defaultdict(int), defaultdict(int), defaultdict(int)
    applied = []
    for d in deltas:
        per_total[d.order_id] += d.delta
        if d.correction_id:
            applied.append(d.correction_id)
    for r in rejects:
        per_total[r.order_id] += 0
        if r.correction_id:
            applied.append(r.correction_id)
        add_ev(F_COUNT_DECREASE, [r.count_id] + ([r.prev_count_id] if r.prev_count_id else []))
    bad_rows = []
    for q in inspections:
        per_scrap[q.order_id] += q.scrap
        per_rework[q.order_id] += q.rework
        per_total[q.order_id] += 0
        if q.scrap + q.rework > q.inspected:
            bad_rows.append(q.inspection_id)
    if bad_rows:
        add_ev(F_SCRAP, bad_rows)
    res.corrections_applied = sorted(set(applied))

    missing_ct = False
    for oid in sorted(per_total):
        info = orders[oid]
        # Config is chosen at the midpoint of the order's activity inside this shift, so a config
        # that only touches the shift boundary (e.g. valid until end + 1 s) is not applied to it.
        a0 = max(win.start, info.actual_start) if info.actual_start else win.start
        a1 = min(win.end, info.actual_end) if info.actual_end else win.end
        ref = a0 + (a1 - a0) / 2 if a1 > a0 else a0
        cfg, applicable = select_config(configs, info.product_id, ref)
        total = per_total[oid]
        if cfg is None:
            missing_ct = True
            add_ev(F_NO_CONFIG, [oid])
            ct = 0.0
        else:
            ct = cfg.ideal_cycle_time_sec
            if len(applicable) > 1:
                add_ev(F_CONFIG, applicable + [oid])
        ideal = total * ct
        res.details.append(DetailResult(
            order_id=oid, product_id=info.product_id, ideal_cycle_time_sec=ct,
            config_id=cfg.config_id if cfg else None, total_count=total,
            good_count=total - per_scrap[oid] - per_rework[oid], scrap_count=per_scrap[oid],
            rework_count=per_rework[oid], ideal_time_seconds=ideal))
    res.total_count = sum(d.total_count for d in res.details)
    res.scrap_count = sum(d.scrap_count for d in res.details)
    res.rework_count = sum(d.rework_count for d in res.details)
    res.good_count = res.total_count - res.scrap_count - res.rework_count   # raw, never repaired
    res.ideal_time_seconds = sum(d.ideal_time_seconds for d in res.details)

    # ---- ratios
    exceeds = res.scrap_count + res.rework_count > res.total_count
    if exceeds:
        add_ev(F_SCRAP, [d.order_id for d in res.details])
    insufficient = t["planned"] <= EPS or not t["any_events"]
    if not t["any_events"]:
        add_ev(F_NO_STATE, [win.shift_instance_id])
    if not insufficient:
        res.availability = _ratio(res.operating_seconds, res.planned_production_seconds)
        if missing_ct:
            res.performance = None
        else:
            res.performance = _ratio(res.ideal_time_seconds, res.operating_seconds)
        if res.total_count == 0 and res.operating_seconds > EPS:
            add_ev(F_NO_COUNTS, [win.shift_instance_id])
        if res.total_count > 0 and not (F_SCRAP in flags):
            res.quality = res.good_count / res.total_count
        if res.performance is not None and res.performance > 1.0 + EPS:
            add_ev(F_PERF_OVER, [d.order_id for d in res.details])
        if None not in (res.availability, res.performance, res.quality):
            res.oee = res.availability * res.performance * res.quality
    res.flags = sorted(flags)
    res.evidence = {k: evidence[k] for k in sorted(evidence)}
    res.status = "INSUFFICIENT_DATA" if insufficient else ("DATA_QUALITY_WARNING" if flags else "OK")
    return res


# ------------------------------------------------------------------ one machine, all shifts
def compute_machine(machine_id, shifts, breaks_by_shift, data, computed_at=None):
    """shifts: [ShiftWindow]; breaks_by_shift: {shift_instance_id: [BreakWindow]}; data: MachineData."""
    deltas, rejects = [], []
    for oid, reads in data.readings.items():
        d, r = process_order_counter(oid, reads, data.corrections)
        deltas += d
        rejects += r
    deltas.sort(key=lambda x: (x.ts, x.count_id))
    rejects.sort(key=lambda x: (x.ts, x.count_id))
    insp = sorted(data.inspections, key=lambda x: (x.ts, x.inspection_id))
    events = sorted(data.events, key=lambda e: (e.start, e.event_id))
    starts = [e.start for e in events]
    max_dur = max((e.end - e.start for e in events), default=None)
    d_ts, r_ts, i_ts = [x.ts for x in deltas], [x.ts for x in rejects], [x.ts for x in insp]

    def window(items, ts_list, s, e):           # (s, e]
        return items[bisect_right(ts_list, s):bisect_right(ts_list, e)]

    out = []
    for win in sorted(shifts, key=lambda w: w.start):
        if events:
            cand = events[bisect_left(starts, win.start - max_dur):bisect_left(starts, win.end)]
        else:
            cand = []
        out.append(calculate_machine_shift(
            machine_id, win, breaks_by_shift.get(win.shift_instance_id, []), cand,
            window(deltas, d_ts, win.start, win.end), window(rejects, r_ts, win.start, win.end),
            window(insp, i_ts, win.start, win.end), data.orders, data.configs, computed_at))
    return out


# ------------------------------------------------------------------ roll-ups
def rollup(label, results) -> RollupResult:
    """Line/plant/period OEE: sum components first, then recompute every ratio (no averaging)."""
    r = RollupResult(label=label, members=len(results))
    flags = set()
    for a in results:
        r.planned_production_seconds += a.planned_production_seconds
        r.operating_seconds += a.operating_seconds
        r.downtime_seconds += a.downtime_seconds
        r.unreconciled_seconds += a.unreconciled_seconds
        r.total_count += a.total_count
        r.good_count += a.good_count
        r.ideal_time_seconds += a.ideal_time_seconds
        flags.update(a.flags)
    r.availability = _ratio(r.operating_seconds, r.planned_production_seconds)
    perf = [a for a in results if a.performance is not None]
    r.excluded_from_performance = [a.aggregate_id for a in results
                                   if a.performance is None and a.status != "INSUFFICIENT_DATA"]
    p_op = sum(a.operating_seconds for a in perf)
    r.performance = _ratio(sum(a.ideal_time_seconds for a in perf), p_op)
    qual = [a for a in results if a.quality is not None]
    r.excluded_from_quality = [a.aggregate_id for a in results
                               if a.quality is None and a.status != "INSUFFICIENT_DATA"]
    r.quality = _ratio(sum(a.good_count for a in qual), sum(a.total_count for a in qual))
    if r.performance is not None and r.performance > 1.0 + EPS:
        flags.add(F_PERF_OVER)
    if None not in (r.availability, r.performance, r.quality):
        r.oee = r.availability * r.performance * r.quality
    r.flags = sorted(flags)
    r.status = ("INSUFFICIENT_DATA" if r.planned_production_seconds <= EPS else
                "DATA_QUALITY_WARNING" if (flags or r.excluded_from_performance
                                           or r.excluded_from_quality) else "OK")
    return r
