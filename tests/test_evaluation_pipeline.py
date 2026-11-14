"""Phase 5 end-to-end: generator -> detection (Phase 4) -> read-only evaluation. Needs SQLAlchemy (project requirements)."""
import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from backend.data_generator.config import SEED
from backend.data_generator.generate import generate
from backend.database import _enable_fk
from backend.services.anomaly import runner
from backend.services.evaluation import evaluator as E

NOW = datetime(2026, 10, 22, 0, 0, 0)


@pytest.fixture(scope="module")
def small_db(tmp_path_factory):
    db = tmp_path_factory.mktemp("p5small") / "s.db"
    generate("small", SEED, db)
    eng = create_engine(f"sqlite:///{db.as_posix()}", connect_args={"check_same_thread": False})
    event.listen(eng, "connect", _enable_fk)
    with Session(eng) as s:
        runner.run_detection(s, detected_at=NOW)
    eng.dispose()
    return db


def _report(db):
    conn = E.connect_readonly(db)
    try:
        return E.build_report(conn, evaluated_at="T")
    finally:
        conn.close()


def test_data_quality_metrics_on_generated_small_dataset(small_db):
    r = _report(small_db)
    d = r["data_quality"]
    assert d["status"] == "EVALUATED" and d["counts"]["labels"] == 15
    assert d["precision"] == 1.0 and d["recall"] == 1.0 and d["f1"] == 1.0
    assert d["missed_detections"] == [] and d["false_positive_items"] == []
    assert set(d["per_type"]) == set(E.DQ_TYPES) and r["dataset"]["machine_shifts"] == 126


def test_operational_metrics_are_reported_as_not_computable(small_db):
    op = _report(small_db)["operational"]
    assert op["status"] == "LABELS_UNAVAILABLE"
    assert op["precision"] is None and op["recall"] is None and op["f1"] is None
    assert op["alert_counts"]["total"] == sum(op["alert_counts"]["by_type"].values())


def test_repeated_evaluation_after_repeated_detection_is_identical(small_db):
    first = json.dumps(_report(small_db), sort_keys=True)
    eng = create_engine(f"sqlite:///{small_db.as_posix()}", connect_args={"check_same_thread": False})
    event.listen(eng, "connect", _enable_fk)
    with Session(eng) as s:
        runner.run_detection(s, detected_at=NOW)
    eng.dispose()
    assert json.dumps(_report(small_db), sort_keys=True) == first
