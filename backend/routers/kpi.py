from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.services import read_model as R
from backend.services.oee import engine as oee_engine

router = APIRouter(prefix="/api", tags=["kpi"])


@router.get("/kpi/plant")
def plant_kpi(date_from: Optional[date] = None, date_to: Optional[date] = None, db: Session = Depends(get_db)):
    results, source, _ = R.load_results(db, date_from, date_to)
    return R.plant_kpi(results, source)


@router.get("/kpi/lines")
def line_kpis(date_from: Optional[date] = None, date_to: Optional[date] = None, db: Session = Depends(get_db)):
    results, source, _ = R.load_results(db, date_from, date_to)
    return {"items": R.line_kpis(db, results, source)}


@router.get("/machine-shifts")
def machine_shifts(machine_id: Optional[str] = None, shift_instance_id: Optional[str] = None,
                   status: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
                   limit: int = Query(200, ge=1, le=2000), offset: int = Query(0, ge=0),
                   db: Session = Depends(get_db)):
    results, source, shifts = R.load_results(db, date_from, date_to, machine_id)
    if shift_instance_id:
        results = [r for r in results if r.shift_instance_id == shift_instance_id]
    if status:
        results = [r for r in results if r.status == status]
    return {"total": len(results), "source": source,
            "items": [R.summarize(r, shifts) for r in results[offset:offset + limit]]}


@router.get("/machine-shifts/{aggregate_id}")
def machine_shift(aggregate_id: str, db: Session = Depends(get_db)):
    results, source, shifts = R.load_results(db)
    hit = next((r for r in results if r.aggregate_id == aggregate_id), None)
    if hit is None:
        raise HTTPException(404, f"machine-shift {aggregate_id} not found")
    # recompute this single machine-shift (read-only) to expose per-flag evidence and per-order detail
    full = oee_engine.compute(db, machine_ids=[hit.machine_id], shift_instance_ids=[hit.shift_instance_id])
    rec = next((r for r in full if r.aggregate_id == aggregate_id), hit)
    return {"summary": R.summarize(rec, shifts), "source": source, "detail": rec.to_dict()}
