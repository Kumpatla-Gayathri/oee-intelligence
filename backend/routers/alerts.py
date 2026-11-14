from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import models as m
from backend.database import get_db
from backend.models.alerts import Alert
from backend.services import read_model as R
from backend.services.investigation import service as inv_service

router = APIRouter(prefix="/api", tags=["alerts"])


@router.get("/alerts")
def list_alerts(severity: Optional[str] = None, anomaly_type: Optional[str] = None, machine_id: Optional[str] = None,
                shift_instance_id: Optional[str] = None, limit: int = Query(500, ge=1, le=2000),
                offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    q = select(Alert)
    for col, val in ((Alert.severity, severity), (Alert.anomaly_type, anomaly_type), (Alert.machine_id, machine_id),
                     (Alert.shift_instance_id, shift_instance_id)):
        if val:
            q = q.where(col == val)
    rows = sorted(db.execute(q).scalars().all(),
                  key=lambda a: (R.SEV_ORDER.get(a.severity, 9), a.machine_id, a.shift_instance_id, a.alert_id))
    return {"total": len(rows), "items": [R.alert_dict(a) for a in rows[offset:offset + limit]]}


@router.get("/alerts/{alert_id}")
def get_alert(alert_id: str, db: Session = Depends(get_db)):
    a = db.get(Alert, alert_id)
    if a is None:
        raise HTTPException(404, f"alert {alert_id} not found")
    d = R.alert_dict(a)
    d["investigations"] = [inv_service.to_dict(i) for i in inv_service.list_investigations(db, alert_id=alert_id)]
    return d


@router.get("/warnings")
def list_warnings(severity: Optional[str] = None, issue_type: Optional[str] = None, machine_id: Optional[str] = None,
                  limit: int = Query(500, ge=1, le=2000), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    q = select(m.Anomaly)
    for col, val in ((m.Anomaly.severity, severity), (m.Anomaly.issue_type, issue_type),
                     (m.Anomaly.machine_id, machine_id)):
        if val:
            q = q.where(col == val)
    rows = sorted(db.execute(q).scalars().all(),
                  key=lambda a: (R.SEV_ORDER.get(a.severity, 9), a.machine_id, a.anomaly_id))
    return {"total": len(rows), "items": [R.warning_dict(a) for a in rows[offset:offset + limit]]}


@router.get("/warnings/{anomaly_id}")
def get_warning(anomaly_id: str, db: Session = Depends(get_db)):
    a = db.get(m.Anomaly, anomaly_id)
    if a is None:
        raise HTTPException(404, f"warning {anomaly_id} not found")
    return R.warning_dict(a)
