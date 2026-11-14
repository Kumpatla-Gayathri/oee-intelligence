"""API tests (FastAPI TestClient) on a generated tiny dataset. Needs fastapi + httpx + SQLAlchemy.
Operational alerts are rare in tiny data, so one Alert row is inserted through the Phase 4 store for workflow tests."""
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from backend import models as m
from backend.data_generator.config import SEED
from backend.data_generator.generate import generate
from backend.database import _enable_fk, get_db
from backend.main import app
from backend.models.alerts import Alert, ensure_alert_tables
from backend.models.investigations import ensure_investigation_tables
from backend.services.anomaly import runner, store
from backend.services.anomaly.schemas import OperationalAlert
from backend.services.oee import engine as oee_engine

NOW = datetime(2026, 10, 22)
ALERT_ID = "ALT-AVAILABILITY_DROP-M-01-01-TESTSHIFT"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    db = tmp_path_factory.mktemp("api") / "a.db"
    generate("tiny", SEED, db)
    eng = create_engine(f"sqlite:///{db.as_posix()}", connect_args={"check_same_thread": False})
    event.listen(eng, "connect", _enable_fk)
    ensure_alert_tables(eng)
    ensure_investigation_tables(eng)
    with Session(eng) as s:
        runner.run_detection(s, detected_at=NOW)
        shift = s.execute(select(m.ShiftInstance.shift_instance_id).limit(1)).scalar()
        store.persist_alerts(s, [OperationalAlert(
            alert_id=f"ALT-AVAILABILITY_DROP-M-01-01-{shift}", anomaly_type="AVAILABILITY_DROP", machine_id="M-01-01",
            shift_instance_id=shift, order_id=None, severity="HIGH", kpi="availability", measured_value=0.5,
            baseline_value=0.85, baseline_n=8, deviation_score=-6.0,
            kpi_impact={"kpi": "availability", "kpi_delta_pts": -35.0}, evidence_ids=[f"AGG-M-01-01-{shift}", "EVT-M-01-01-000001"],
            explanation="test alert", recommended_action="check", detected_at=NOW)], detected_at=NOW)

    def override():
        s = Session(eng)
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    yield TestClient(app), eng, shift
    app.dependency_overrides.clear()


def test_health(env):
    assert env[0].get("/api/health").json()["status"] == "ok"


def test_plant_and_line_kpis_match_the_engine(env):
    c, eng, _ = env
    with Session(eng) as s:
        results = oee_engine.compute(s)
        expected = oee_engine.line_rollups(s, results)
    p = c.get("/api/kpi/plant").json()
    assert p["source"] == "computed" and p["machine_shifts"] == len(results)
    assert p["availability"] == pytest.approx(expected["PLANT"].availability)
    assert p["oee"] == pytest.approx(expected["PLANT"].oee) or p["oee"] is None
    lines = c.get("/api/kpi/lines").json()["items"]
    assert {l["line_id"] for l in lines} == {k for k in expected if k != "PLANT"}


def test_kpis_from_persisted_aggregates_are_identical(env):
    c, eng, _ = env
    before = c.get("/api/kpi/plant").json()
    with Session(eng) as s:
        oee_engine.run(s, computed_at=NOW)
    after = c.get("/api/kpi/plant").json()
    assert after["source"] == "oee_aggregate"
    for k in ("availability", "performance", "quality", "oee", "machine_shifts"):
        assert after[k] == pytest.approx(before[k]) if before[k] is not None else after[k] is None


def test_machine_shift_list_and_detail(env):
    c, eng, shift = env
    data = c.get("/api/machine-shifts").json()
    with Session(eng) as s:
        n = s.execute(select(func.count()).select_from(m.Machine)).scalar() * \
            s.execute(select(func.count()).select_from(m.ShiftInstance)).scalar()
    assert data["total"] == n
    one = c.get("/api/machine-shifts", params={"machine_id": "M-01-01"}).json()
    assert one["total"] and all(i["machine_id"] == "M-01-01" for i in one["items"])
    agg = one["items"][0]["aggregate_id"]
    d = c.get(f"/api/machine-shifts/{agg}").json()
    assert d["summary"]["aggregate_id"] == agg and "evidence" in d["detail"]
    assert c.get("/api/machine-shifts/AGG-nope").status_code == 404


def test_data_quality_warnings_are_listed_separately(env):
    c, _, _ = env
    w = c.get("/api/warnings").json()
    assert w["total"] == 5 and all(i["evidence_ids"] and i["expected_decision"] for i in w["items"])
    assert not any(a["alert_id"] == i["anomaly_id"] for a in c.get("/api/alerts").json()["items"] for i in w["items"])
    assert c.get("/api/warnings/ANM-999999").status_code == 404


def test_alert_list_filters_and_detail(env):
    c, _, shift = env
    aid = f"ALT-AVAILABILITY_DROP-M-01-01-{shift}"
    items = c.get("/api/alerts", params={"severity": "HIGH", "machine_id": "M-01-01"}).json()["items"]
    assert any(a["alert_id"] == aid for a in items)
    assert c.get("/api/alerts", params={"anomaly_type": "QUALITY_DROP", "machine_id": "M-01-01"}).json()["total"] == 0
    d = c.get(f"/api/alerts/{aid}").json()
    assert d["evidence_ids"][0].startswith("AGG-") and d["aggregate_id"] == f"AGG-M-01-01-{shift}"
    assert c.get("/api/alerts/ALT-nope").status_code == 404


def test_investigation_lifecycle_and_traceability(env):
    c, eng, shift = env
    aid = f"ALT-AVAILABILITY_DROP-M-01-01-{shift}"
    with Session(eng) as s:
        alert_before = s.get(Alert, aid).evidence_ids
    r = c.post("/api/investigations", json={"alert_id": aid, "owner": "Asha", "notes": "looking into it"})
    assert r.status_code == 201
    inv = r.json()
    assert inv["status"] == "OPEN" and inv["owner"] == "Asha" and inv["alert_id"] == aid
    assert inv["machine_id"] == "M-01-01" and inv["shift_instance_id"] == shift
    assert inv["aggregate_id"] == f"AGG-M-01-01-{shift}" and inv["evidence_ids"] == json.loads(alert_before)
    assert [n["kind"] for n in inv["notes"]] == ["CREATED", "NOTE"]
    iid = inv["investigation_id"]

    assert c.post("/api/investigations", json={"alert_id": aid}).status_code == 409        # no duplicate while active
    assert c.post("/api/investigations", json={"alert_id": "ALT-nope"}).status_code == 404
    assert c.patch(f"/api/investigations/{iid}", json={}).status_code == 422

    r = c.patch(f"/api/investigations/{iid}", json={"status": "IN_PROGRESS", "owner": "Ravi", "note": "on site",
                                                    "add_evidence_ids": ["EVT-M-01-01-000002"]})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "IN_PROGRESS" and body["owner"] == "Ravi" and "EVT-M-01-01-000002" in body["evidence_ids"]
    kinds = [n["kind"] for n in body["notes"]]
    assert {"STATUS_CHANGE", "OWNER_CHANGE", "NOTE", "EVIDENCE_ADDED"} <= set(kinds)

    assert c.patch(f"/api/investigations/{iid}", json={"status": "IN_PROGRESS"}).status_code == 422   # same status
    assert c.patch(f"/api/investigations/{iid}", json={"status": "BOGUS"}).status_code == 422
    done = c.patch(f"/api/investigations/{iid}", json={"status": "RESOLVED", "note": "fixed"}).json()
    assert done["status"] == "RESOLVED" and done["resolved_at"]
    assert c.patch(f"/api/investigations/{iid}", json={"status": "OPEN"}).status_code == 422          # must reopen via IN_PROGRESS

    assert c.get("/api/investigations", params={"status": "RESOLVED"}).json()["total"] == 1
    assert c.get("/api/investigations", params={"status": "OPEN"}).json()["total"] == 0
    assert c.get("/api/investigations", params={"status": "NOPE"}).status_code == 422
    assert c.get(f"/api/investigations/{iid}").json()["notes"]
    assert c.get("/api/investigations/INV-999999").status_code == 404
    assert [i["investigation_id"] for i in c.get(f"/api/alerts/{aid}").json()["investigations"]] == [iid]

    again = c.post("/api/investigations", json={"alert_id": aid})                  # allowed once the previous is resolved
    assert again.status_code == 201 and again.json()["investigation_id"] != iid
    with Session(eng) as s:
        assert s.get(Alert, aid).evidence_ids == alert_before                      # alert itself never modified


def test_frontend_is_served(env):
    c, _, _ = env
    r = c.get("/")
    assert r.status_code == 200 and "YottaFlex" in r.text
    assert c.get("/app.js").status_code == 200
