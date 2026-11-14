"""Investigation persistence. Alerts are only READ (traceability snapshot); the alert table is never modified."""
import json
from datetime import datetime

from sqlalchemy import select

from backend.models.alerts import Alert
from backend.models.investigations import Investigation, InvestigationNote
from backend.services.investigation import workflow as W


class NotFound(LookupError):
    pass


class Conflict(RuntimeError):
    def __init__(self, message, existing_id):
        super().__init__(message)
        self.existing_id = existing_id


def _now():
    return datetime.now().replace(microsecond=0)


def _note(db, inv_id, kind, text, author, now):
    db.add(InvestigationNote(investigation_id=inv_id, kind=kind, author=author, text=text, created_at=now))


def to_dict(inv, notes=None) -> dict:
    d = {"investigation_id": inv.investigation_id, "alert_id": inv.alert_id, "machine_id": inv.machine_id,
         "shift_instance_id": inv.shift_instance_id, "aggregate_id": inv.aggregate_id, "order_id": inv.order_id,
         "anomaly_type": inv.anomaly_type, "severity": inv.severity, "title": inv.title, "status": inv.status,
         "owner": inv.owner, "evidence_ids": json.loads(inv.evidence_ids), "created_at": inv.created_at,
         "updated_at": inv.updated_at, "resolved_at": inv.resolved_at}
    if notes is not None:
        d["notes"] = [{"note_id": n.note_id, "kind": n.kind, "author": n.author, "text": n.text,
                       "created_at": n.created_at} for n in notes]
    return d


def get_notes(db, inv_id):
    return db.execute(select(InvestigationNote).where(InvestigationNote.investigation_id == inv_id)
                      .order_by(InvestigationNote.note_id)).scalars().all()


def get(db, inv_id) -> Investigation:
    inv = db.get(Investigation, inv_id)
    if inv is None:
        raise NotFound(f"investigation {inv_id} not found")
    return inv


def create_from_alert(db, alert_id, owner=None, notes=None, author=None, now=None) -> Investigation:
    now = now or _now()
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise NotFound(f"alert {alert_id} not found")
    active = db.execute(select(Investigation).where(Investigation.alert_id == alert_id,
                                                    Investigation.status.in_(W.ACTIVE))).scalars().first()
    if active is not None:
        raise Conflict(f"alert {alert_id} already has an active investigation ({active.investigation_id})",
                       active.investigation_id)
    last = db.execute(select(Investigation.investigation_id).order_by(Investigation.investigation_id.desc())
                      .limit(1)).scalar()
    inv = Investigation(
        investigation_id=W.next_investigation_id(last), alert_id=alert.alert_id, machine_id=alert.machine_id,
        shift_instance_id=alert.shift_instance_id, aggregate_id=f"AGG-{alert.machine_id}-{alert.shift_instance_id}",
        order_id=alert.order_id, anomaly_type=alert.anomaly_type, severity=alert.severity,
        title=W.title_for(alert.anomaly_type, alert.machine_id, alert.shift_instance_id), status="OPEN",
        owner=(owner or "").strip() or None, evidence_ids=alert.evidence_ids, created_at=now, updated_at=now)
    db.add(inv)
    _note(db, inv.investigation_id, "CREATED", f"Created from alert {alert.alert_id}", author, now)
    if notes and notes.strip():
        _note(db, inv.investigation_id, "NOTE", notes.strip(), author, now)
    db.commit()
    return inv


def update(db, inv_id, status=None, owner=None, note=None, add_evidence_ids=None, author=None, now=None):
    """owner=None means 'unchanged'; owner='' clears it. Raises NotFound / workflow.WorkflowError."""
    now = now or _now()
    inv = get(db, inv_id)
    if status is None and owner is None and not (note and note.strip()) and not add_evidence_ids:
        raise W.WorkflowError("nothing to update: give status, owner, note or add_evidence_ids")
    if status is not None:
        W.check_transition(inv.status, status)                      # validate before any change
    if status is not None:
        _note(db, inv_id, "STATUS_CHANGE", f"{inv.status} -> {status}", author, now)
        inv.status = status
        inv.resolved_at = now if status == "RESOLVED" else None
    if owner is not None and (owner.strip() or None) != inv.owner:
        new = owner.strip() or None
        _note(db, inv_id, "OWNER_CHANGE", f"owner: {inv.owner or '-'} -> {new or '-'}", author, now)
        inv.owner = new
    if note and note.strip():
        _note(db, inv_id, "NOTE", note.strip(), author, now)
    if add_evidence_ids:
        merged = W.merge_evidence(json.loads(inv.evidence_ids), add_evidence_ids)
        added = [e for e in merged if e not in json.loads(inv.evidence_ids)]
        if added:
            _note(db, inv_id, "EVIDENCE_ADDED", ", ".join(added), author, now)
            inv.evidence_ids = json.dumps(merged)
    inv.updated_at = now
    db.commit()
    return inv


def list_investigations(db, status=None, alert_id=None, machine_id=None):
    q = select(Investigation).order_by(Investigation.investigation_id.desc())
    if status:
        q = q.where(Investigation.status == status)
    if alert_id:
        q = q.where(Investigation.alert_id == alert_id)
    if machine_id:
        q = q.where(Investigation.machine_id == machine_id)
    return db.execute(q).scalars().all()
