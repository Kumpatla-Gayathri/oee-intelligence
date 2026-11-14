"""FastAPI app.  Run from the project root:
    $env:DATABASE_URL = "sqlite:///data/oee_demo.db"
    python -m uvicorn backend.main:app --reload --port 8000
UI: http://127.0.0.1:8000/    API docs: http://127.0.0.1:8000/docs"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.database import DATABASE_URL, engine
from backend.models.alerts import ensure_alert_tables
from backend.models.investigations import ensure_investigation_tables
from backend.routers import alerts, investigations, kpi

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app):
    ensure_alert_tables(engine)           # additive, idempotent: alert / anomaly_truth_link
    ensure_investigation_tables(engine)    # additive, idempotent: investigation / investigation_note
    yield


app = FastAPI(title="YottaFlex OEE Intelligence", version="0.1", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(kpi.router)
app.include_router(alerts.router)
app.include_router(investigations.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "database": DATABASE_URL}


if FRONTEND.exists():                       # mounted last so /api/* and /docs win
    app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")

