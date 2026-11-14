"""Pure detection logic (no database, never reads ground-truth labels).

Operational anomalies: a KPI (availability / performance / quality) of a machine-shift is far below that machine's
own baseline = median/MAD of its OTHER eligible shifts (leave-one-out). Shifts whose KPI is untrustworthy
(INSUFFICIENT_DATA, KPI missing, or a data-quality flag that invalidates that KPI) are skipped and never enter a baseline.

Data-quality warnings: engine flags mapped to the five issue types. They are reported separately and are NOT
operational alerts. Flags without a mapping (e.g. UNRECONCILED_TIME) are only counted, never turned into rows.
"""
import statistics
from collections import Counter, defaultdict

from backend.services.anomaly import config as C
from backend.services.anomaly.schemas import DataQualityWarning, DetectionResult, OperationalAlert, ShiftKpi


def _dedupe(ids):
    return list(dict.fromkeys(i for i in ids if i))


def _eligible_value(s: ShiftKpi, kpi: str):
    """(value, None) when usable, else (None, reason)."""
    if s.status == "INSUFFICIENT_DATA":
        return None, "INSUFFICIENT_DATA"
    value = getattr(s, kpi)
    if value is None:
        return None, "KPI_NOT_AVAILABLE"
    bad = sorted(set(s.flags) & C.KPI_UNRELIABLE_FLAGS[kpi])
    if bad:
        return None, "KPI_UNRELIABLE:" + ",".join(bad)
    if kpi == "availability" and s.planned_production_seconds > 0 and \
            s.unreconciled_seconds / s.planned_production_seconds > C.UNRECONCILED_MAX_SHARE:
        return None, "KPI_UNRELIABLE:UNRECONCILED_TIME"
    return value, None


def robust_baseline(values):
    """(median, robust scale). Scale never below SCALE_FLOOR."""
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values)
    return med, max(1.4826 * mad, C.SCALE_FLOOR)


def severity_for_drop(drop: float, min_abs: float) -> str:
    ratio = drop / min_abs
    return "HIGH" if ratio >= 2.0 else "MEDIUM" if ratio >= 1.5 else "LOW"


def _impact(kpi, drop, s: ShiftKpi):
    others = {"availability": (s.performance, s.quality), "performance": (s.availability, s.quality),
              "quality": (s.availability, s.performance)}[kpi]
    oee_pts = None if None in others else round(-drop * others[0] * others[1] * 100, 2)
    out = {"kpi": kpi, "kpi_delta_pts": round(-drop * 100, 2), "estimated_oee_impact_pts": oee_pts}
    if kpi == "availability":
        out["lost_minutes"] = round(drop * s.planned_production_seconds / 60, 1)
    elif kpi == "performance":
        out["lost_operating_minutes_equivalent"] = round(drop * s.operating_seconds / 60, 1)
    else:
        out["units_lost_to_quality"] = round(drop * s.total_count)
    return out


def detect_operational(shifts, support_fn=None, detected_at=None, skipped=None):
    skipped = skipped if skipped is not None else []
    support_fn = support_fn or (lambda s, kpi: [])
    alerts = []
    by_machine = defaultdict(list)
    for s in shifts:
        by_machine[s.machine_id].append(s)
    for machine, group in sorted(by_machine.items()):
        group.sort(key=lambda s: s.shift_instance_id)
        for kpi in ("availability", "performance", "quality"):
            usable = {}
            for s in group:
                v, why = _eligible_value(s, kpi)
                if v is None:
                    skipped.append({"machine_id": machine, "shift_instance_id": s.shift_instance_id,
                                    "kpi": kpi, "reason": why})
                else:
                    usable[s.shift_instance_id] = v
            for s in group:
                if s.shift_instance_id not in usable:
                    continue
                x = usable[s.shift_instance_id]
                others = [v for k, v in usable.items() if k != s.shift_instance_id]
                if len(others) < C.MIN_BASELINE_SHIFTS:
                    skipped.append({"machine_id": machine, "shift_instance_id": s.shift_instance_id,
                                    "kpi": kpi, "reason": "INSUFFICIENT_BASELINE"})
                    continue
                med, scale = robust_baseline(others)
                z, drop = (x - med) / scale, med - x
                min_abs = C.MIN_ABS_DROP[kpi]
                if z > -C.Z_THRESHOLD or drop < min_abs:
                    continue
                typ = C.OPERATIONAL_TYPE[kpi]
                impact = _impact(kpi, drop, s)
                ev = _dedupe([s.aggregate_id] + list(support_fn(s, kpi)))
                oee_txt = ("" if impact["estimated_oee_impact_pts"] is None
                           else f" Estimated OEE impact {impact['estimated_oee_impact_pts']:+.1f} pts.")
                alerts.append(OperationalAlert(
                    alert_id=f"ALT-{typ}-{machine}-{s.shift_instance_id}", anomaly_type=typ, machine_id=machine,
                    shift_instance_id=s.shift_instance_id,
                    order_id=s.order_ids[0] if len(s.order_ids) == 1 else None,
                    severity=severity_for_drop(drop, min_abs), kpi=kpi, measured_value=round(x, 6),
                    baseline_value=round(med, 6), baseline_n=len(others), deviation_score=round(z, 3),
                    kpi_impact=impact, evidence_ids=ev,
                    explanation=(f"{kpi.capitalize()} {x:.1%} vs {machine} baseline {med:.1%} (median of "
                                 f"{len(others)} other shifts): {drop * 100:.1f} pts lower, robust z={z:.1f}." + oee_txt),
                    recommended_action=C.RECOMMENDED_ACTION[typ], detected_at=detected_at))
    return alerts


# ------------------------------------------------------------------ data-quality warnings
def _dq_severity(issue, s: ShiftKpi, evidence):
    if issue == "OVERLAPPING_STATES":
        o = s.overlap_seconds
        return "HIGH" if o >= C.OVERLAP_SEC_HIGH else "MEDIUM" if o >= C.OVERLAP_SEC_MEDIUM else "LOW"
    if issue == "COUNT_DECREASE":
        return "HIGH" if len(evidence) >= 6 else "MEDIUM"
    if issue == "SCRAP_EXCEEDS_TOTAL":
        return "HIGH"
    if issue == "DOWNTIME_IN_PLANNED_BREAK":
        d = s.break_excluded_downtime_seconds
        return "HIGH" if d >= C.BREAK_DT_SEC_HIGH else "MEDIUM" if d >= C.BREAK_DT_SEC_MEDIUM else "LOW"
    p = s.performance                                   # WRONG_CYCLE_TIME
    return "HIGH" if p is None or p > 1.5 else "MEDIUM" if p > 1.0 else "LOW"


def _dq_text(issue, s: ShiftKpi, evidence):
    n = len(evidence)
    return {
        "OVERLAPPING_STATES": f"{s.overlap_seconds / 60:.1f} min of {s.machine_id} time is covered by more than one "
                              f"state event; counted once, downtime takes precedence ({n} evidence records).",
        "COUNT_DECREASE": f"A cumulative counter reading is lower than the previous accepted reading; the delta was "
                          f"excluded and no correction exists ({n} evidence records).",
        "SCRAP_EXCEEDS_TOTAL": f"Scrap + rework exceed the produced/inspected quantity; Quality is withheld "
                               f"for this shift ({n} evidence records).",
        "DOWNTIME_IN_PLANNED_BREAK": f"{s.break_excluded_downtime_seconds / 60:.1f} min of downtime was recorded "
                                     f"inside a planned break; it is excluded from downtime ({n} evidence records).",
        "WRONG_CYCLE_TIME": ("Ideal cycle time looks wrong: " + (f"Performance is {s.performance:.0%}"
                             if s.performance is not None else "Performance is unavailable") +
                             f" and/or several configurations apply ({n} evidence records)."),
    }[issue]


def detect_data_quality(shifts, order_resolver=None):
    warnings, not_alerted = [], Counter()
    for s in shifts:
        flags = set(s.flags)
        for flag in sorted(flags):
            if flag not in C.DQ_FLAG_TO_ISSUE:
                not_alerted[flag] += 1
        issues = {}
        for flag in sorted(flags & set(C.DQ_FLAG_TO_ISSUE)):
            if flag == "PERFORMANCE_OVER_100" and "CONFIG_CONFLICT" in flags:
                issues.setdefault("WRONG_CYCLE_TIME", [])
                continue
            issues.setdefault(C.DQ_FLAG_TO_ISSUE[flag], []).extend(s.evidence.get(flag, []))
        for issue, ev in sorted(issues.items()):
            ev = _dedupe(ev) or [s.aggregate_id]
            order = next((i for i in ev if i.startswith("PO-")), None)
            if order is None and order_resolver:
                order = order_resolver(ev)
            if order is None and len(s.order_ids) == 1:
                order = s.order_ids[0]
            src = "CONFIG_CONFLICT" if issue == "WRONG_CYCLE_TIME" and "CONFIG_CONFLICT" in flags else \
                  next(f for f in sorted(flags) if C.DQ_FLAG_TO_ISSUE.get(f) == issue)
            warnings.append(DataQualityWarning(
                issue_type=issue, machine_id=s.machine_id, shift_instance_id=s.shift_instance_id, order_id=order,
                severity=_dq_severity(issue, s, ev), evidence_ids=ev, explanation=_dq_text(issue, s, ev),
                expected_decision=C.EXPECTED_DECISION[issue], source_flag=src, aggregate_id=s.aggregate_id,
                measured={"overlap_seconds": s.overlap_seconds,
                          "break_excluded_downtime_seconds": s.break_excluded_downtime_seconds,
                          "performance": s.performance}))
    return warnings, dict(not_alerted)


def detect(shifts, support_fn=None, order_resolver=None, detected_at=None) -> DetectionResult:
    shifts = list(shifts)
    res = DetectionResult(machine_shifts=len(shifts))
    res.alerts = detect_operational(shifts, support_fn, detected_at, res.skipped)
    res.warnings, res.dq_flags_not_alerted = detect_data_quality(shifts, order_resolver)
    return res
