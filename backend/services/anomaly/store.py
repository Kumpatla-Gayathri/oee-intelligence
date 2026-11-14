"""Idempotent persistence.
* Operational alerts -> `alert` (primary key is deterministic: type + machine + shift).
* Data-quality warnings -> existing `anomaly` table, natural key (machine, shift_instance, issue_type);
  the ANM-nnnnnn id is assigned once and kept on reruns.
Reruns update changed rows, leave identical rows untouched, and delete rows in the run scope that are no longer detected."""
import json
from datetime import datetime

from sqlalchemy import delete, select

from backend import models as m
from backend.models.alerts import Alert, ensure_alert_tables


def _in_scope(col_machine, col_shift, machine_ids, shift_ids):
    conds = []
    if machine_ids is not None:
        conds.append(col_machine.in_(list(machine_ids)))
    if shift_ids is not None:
        conds.append(col_shift.in_(list(shift_ids)))
    return conds


def persist_alerts(session, alerts, machine_ids=None, shift_ids=None, detected_at=None) -> dict:
    ensure_alert_tables(session.get_bind())
    detected_at = detected_at or datetime.now().replace(microsecond=0)
    stats = {"inserted": 0, "updated": 0, "unchanged": 0, "deleted": 0}
    existing = {a.alert_id: a for a in session.execute(
        select(Alert).where(*_in_scope(Alert.machine_id, Alert.shift_instance_id, machine_ids, shift_ids))).scalars()}
    seen = set()
    for a in alerts:
        if a.alert_id in seen:
            continue
        seen.add(a.alert_id)
        fields = dict(
            anomaly_type=a.anomaly_type, machine_id=a.machine_id, shift_instance_id=a.shift_instance_id,
            order_id=a.order_id, severity=a.severity, kpi=a.kpi, measured_value=a.measured_value,
            baseline_value=a.baseline_value, baseline_n=a.baseline_n, deviation_score=a.deviation_score,
            kpi_impact=json.dumps(a.kpi_impact, sort_keys=True), evidence_ids=json.dumps(a.evidence_ids),
            explanation=a.explanation, recommended_action=a.recommended_action)
        row = existing.get(a.alert_id) or session.get(Alert, a.alert_id)
        if row is None:
            session.add(Alert(alert_id=a.alert_id, detected_at=a.detected_at or detected_at, **fields))
            stats["inserted"] += 1
        elif any(getattr(row, k) != v for k, v in fields.items()):
            for k, v in fields.items():
                setattr(row, k, v)
            row.detected_at = a.detected_at or detected_at
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1
    for aid, row in existing.items():
        if aid not in seen:
            session.delete(row)
            stats["deleted"] += 1
    session.commit()
    return stats


def _next_anomaly_number(session) -> int:
    last = session.execute(select(m.Anomaly.anomaly_id).order_by(m.Anomaly.anomaly_id.desc()).limit(1)).scalar()
    return int(last.split("-")[1]) + 1 if last else 1


def persist_warnings(session, warnings, machine_ids=None, shift_ids=None, detected_at=None) -> dict:
    """Returns stats plus `ids`: {(machine, shift, issue_type): anomaly_id}."""
    detected_at = detected_at or datetime.now().replace(microsecond=0)
    stats = {"inserted": 0, "updated": 0, "unchanged": 0, "deleted": 0}
    q = select(m.Anomaly).where(m.Anomaly.shift_instance_id.is_not(None),
                                *_in_scope(m.Anomaly.machine_id, m.Anomaly.shift_instance_id, machine_ids, shift_ids))
    existing = {(r.machine_id, r.shift_instance_id, r.issue_type): r for r in session.execute(q).scalars()}
    # keys outside the requested scope must still be found so a scoped rerun cannot duplicate them
    for r in session.execute(select(m.Anomaly).where(m.Anomaly.shift_instance_id.is_not(None))).scalars():
        existing.setdefault((r.machine_id, r.shift_instance_id, r.issue_type), r)
    scoped = set(session.execute(q).scalars().all())
    n, seen, ids = _next_anomaly_number(session), set(), {}
    for w in sorted(warnings, key=lambda x: x.key):
        if w.key in seen:
            continue
        seen.add(w.key)
        fields = dict(order_id=w.order_id, severity=w.severity, evidence_ids=json.dumps(w.evidence_ids),
                      explanation=w.explanation, expected_decision=w.expected_decision)
        row = existing.get(w.key)
        if row is None:
            row = m.Anomaly(anomaly_id=f"ANM-{n:06d}", machine_id=w.machine_id, shift_instance_id=w.shift_instance_id,
                            issue_type=w.issue_type, detected_at=detected_at, **fields)
            session.add(row)
            n += 1
            stats["inserted"] += 1
        elif any(getattr(row, k) != v for k, v in fields.items()):
            for k, v in fields.items():
                setattr(row, k, v)
            row.detected_at = detected_at
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1
        ids[w.key] = row.anomaly_id
    for r in scoped:
        if (r.machine_id, r.shift_instance_id, r.issue_type) not in seen:
            session.delete(r)
            stats["deleted"] += 1
    session.commit()
    stats["ids"] = ids
    return stats
