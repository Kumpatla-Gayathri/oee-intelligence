from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.schemas.investigation import InvestigationCreate, InvestigationUpdate, NoteCreate
from backend.services.investigation import service as S
from backend.services.investigation.workflow import STATUSES, WorkflowError

router = APIRouter(prefix="/api", tags=["investigations"])


def _full(db, inv):
    return S.to_dict(inv, S.get_notes(db, inv.investigation_id))


@router.post("/investigations", status_code=201)
def create_investigation(body: InvestigationCreate, db: Session = Depends(get_db)):
    try:
        inv = S.create_from_alert(db, body.alert_id, body.owner, body.notes, body.author)
    except S.NotFound as e:
        raise HTTPException(404, str(e))
    except S.Conflict as e:
        raise HTTPException(409, str(e))
    return _full(db, inv)


@router.get("/investigations")
def list_investigations(status: Optional[str] = None, alert_id: Optional[str] = None,
                        machine_id: Optional[str] = None, db: Session = Depends(get_db)):
    if status and status not in STATUSES:
        raise HTTPException(422, f"status must be one of {', '.join(STATUSES)}")
    items = S.list_investigations(db, status, alert_id, machine_id)
    return {"total": len(items), "items": [S.to_dict(i) for i in items]}


@router.get("/investigations/{investigation_id}")
def get_investigation(investigation_id: str, db: Session = Depends(get_db)):
    try:
        return _full(db, S.get(db, investigation_id))
    except S.NotFound as e:
        raise HTTPException(404, str(e))


@router.patch("/investigations/{investigation_id}")
def update_investigation(investigation_id: str, body: InvestigationUpdate, db: Session = Depends(get_db)):
    try:
        inv = S.update(db, investigation_id, body.status, body.owner, body.note, body.add_evidence_ids, body.author)
    except S.NotFound as e:
        raise HTTPException(404, str(e))
    except WorkflowError as e:
        raise HTTPException(422, str(e))
    return _full(db, inv)


@router.post("/investigations/{investigation_id}/notes", status_code=201)
def add_note(investigation_id: str, body: NoteCreate, db: Session = Depends(get_db)):
    try:
        inv = S.update(db, investigation_id, note=body.text, author=body.author)
    except S.NotFound as e:
        raise HTTPException(404, str(e))
    except WorkflowError as e:
        raise HTTPException(422, str(e))
    return _full(db, inv)
