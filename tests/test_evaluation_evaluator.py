"""Phase 5 evaluator tests on hand-built sqlite databases (stdlib only; read-only evaluator contract)."""
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from backend.services.evaluation import evaluator as E

SCHEMA = """
CREATE TABLE machine(machine_id TEXT, effective_from DATE, effective_to DATE);
CREATE TABLE shift_instance(shift_instance_id TEXT, shift_date DATE);
CREATE TABLE ground_truth(truth_id TEXT, issue_type TEXT, machine_id TEXT, order_id TEXT, evidence_ids TEXT, expected_decision TEXT);
CREATE TABLE anomaly(anomaly_id TEXT, machine_id TEXT, order_id TEXT, shift_instance_id TEXT, issue_type TEXT, severity TEXT,
                     evidence_ids TEXT, explanation TEXT, expected_decision TEXT);
CREATE TABLE alert(alert_id TEXT, anomaly_type TEXT, machine_id TEXT, shift_instance_id TEXT, severity TEXT, evidence_ids TEXT);
"""


def make_db(truths=(), anomalies=(), alerts=(), machines=2, shifts=5, schema=True):
    path = Path(tempfile.mkdtemp()) / "t.db"
    c = sqlite3.connect(path)
    if schema:
        c.executescript(SCHEMA)
        c.executemany("INSERT INTO machine VALUES (?,NULL,NULL)", [(f"M{i}",) for i in range(1, machines + 1)])
        c.executemany("INSERT INTO shift_instance VALUES (?,?)", [(f"S{i}", f"2026-01-0{i}") for i in range(1, shifts + 1)])
        c.executemany("INSERT INTO ground_truth VALUES (?,?,?,?,?,?)",
                      [(t, ty, m, None, json.dumps(ev), d) for t, ty, m, ev, d in truths])
        c.executemany("INSERT INTO anomaly VALUES (?,?,?,?,?,?,?,?,?)",
                      [(a, m, None, s, ty, "MEDIUM", json.dumps(ev), "x", d) for a, m, s, ty, ev, d in anomalies])
        c.executemany("INSERT INTO alert VALUES (?,?,?,?,?,?)",
                      [(a, ty, m, s, "HIGH", json.dumps(ev)) for a, ty, m, s, ev in alerts])
    c.commit()
    c.close()
    return path


def report(path, **kw):
    conn = E.connect_readonly(path)
    try:
        return E.build_report(conn, evaluated_at="T", **kw)
    finally:
        conn.close()


GT1 = ("GT-1", "COUNT_DECREASE", "M1", ["CNT-1", "CNT-0"], "EXCLUDE_DELTA_REQUEST_CORRECTION")
GT2 = ("GT-2", "SCRAP_EXCEEDS_TOTAL", "M2", ["QC-9"], "QUARANTINE_QUALITY_AND_REVIEW")
AN1 = ("ANM-000001", "M1", "S2", "COUNT_DECREASE", ["CNT-1", "CNT-0"], "EXCLUDE_DELTA_REQUEST_CORRECTION")
AN2 = ("ANM-000002", "M2", "S3", "SCRAP_EXCEEDS_TOTAL", ["QC-9"], "QUARANTINE_QUALITY_AND_REVIEW")


def test_perfect_data_quality_match_with_traceability():
    r = report(make_db([GT1, GT2], [AN1, AN2]))
    d = r["data_quality"]
    assert d["status"] == "EVALUATED" and r["dataset"]["machine_shifts"] == 10
    assert (d["precision"], d["recall"], d["f1"], d["false_positive_rate"]) == (1.0, 1.0, 1.0, 0.0)
    assert d["type_coverage"] == 1.0 and d["expected_decision_agreement"]["rate"] == 1.0
    m = d["matches"][0]
    assert (m["truth_id"], m["anomaly_id"], m["machine_id"], m["shift_instance_id"], m["type"]) == \
           ("GT-1", "ANM-000001", "M1", "S2", "COUNT_DECREASE")
    assert m["shared_evidence_ids"] == ["CNT-0", "CNT-1"] and m["expected_decision_agrees"] is True
    assert d["per_type"]["SCRAP_EXCEEDS_TOTAL"]["recall"] == 1.0


def test_missed_detection_and_false_positive_are_listed():
    fp = ("ANM-000003", "M1", "S4", "OVERLAPPING_STATES", ["EVT-5"], "EXCLUDE_OVERLAP_AND_REVIEW")
    d = report(make_db([GT1, GT2], [AN1, fp]))["data_quality"]
    assert d["recall"] == 0.5 and d["precision"] == 0.5
    assert [x["truth_id"] for x in d["missed_detections"]] == ["GT-2"]
    f = d["false_positive_items"][0]
    assert (f["anomaly_id"], f["machine_id"], f["shift_instance_id"], f["type"]) == \
           ("ANM-000003", "M1", "S4", "OVERLAPPING_STATES")
    assert d["counts"]["false_negatives"] == 1 and d["counts"]["false_positives"] == 1
    assert d["false_positive_rate"] is not None and d["false_positive_rate"] > 0


def test_wrong_type_or_machine_does_not_match():
    wrong = ("ANM-000001", "M2", "S2", "COUNT_DECREASE", ["CNT-1"], "x")
    d = report(make_db([GT1], [wrong]))["data_quality"]
    assert d["recall"] == 0.0 and d["precision"] == 0.0 and d["f1"] == 0.0


def test_empty_database_and_missing_tables_are_handled():
    for path in (make_db(), make_db(schema=False)):
        r = report(path)
        d = r["data_quality"]
        assert d["status"] == "NO_LABELS" and d["precision"] is None and d["recall"] is None and d["f1"] is None
        assert d["counts"]["labels"] == 0 and d["counts"]["predictions"] == 0
        assert r["operational"]["status"] == "LABELS_UNAVAILABLE" and r["operational"]["alert_counts"]["total"] == 0
    assert report(make_db(schema=False))["dataset"]["machine_shifts"] is None
    assert report(make_db(schema=False))["dataset"]["flagged_machine_shift_rate"] is None


def test_labels_but_no_detection_run_gives_zero_recall_and_undefined_precision():
    d = report(make_db([GT1, GT2], []))["data_quality"]
    assert d["recall"] == 0.0 and d["precision"] is None and d["f1"] is None
    assert len(d["missed_detections"]) == 2


def test_operational_metrics_are_not_computable_and_dq_scores_do_not_leak():
    alerts = [("ALT-AVAILABILITY_DROP-M1-S2", "AVAILABILITY_DROP", "M1", "S2", ["AGG-M1-S2", "EVT-1"]),
              ("ALT-PERFORMANCE_DROP-M2-S3", "PERFORMANCE_DROP", "M2", "S3", ["AGG-M2-S3"])]
    r = report(make_db([GT1, GT2], [AN1, AN2], alerts))
    op = r["operational"]
    assert r["data_quality"]["precision"] == 1.0 and r["data_quality"]["recall"] == 1.0
    assert op["status"] == "LABELS_UNAVAILABLE" and "cannot" in op["reason"]
    assert (op["precision"], op["recall"], op["f1"], op["false_positive_rate"]) == (None, None, None, None)
    assert op["alert_counts"]["total"] == 2 and op["alert_counts"]["by_type"] == \
           {"AVAILABILITY_DROP": 1, "PERFORMANCE_DROP": 1}
    assert op["alert_counts"]["by_severity"] == {"HIGH": 2} and op["alert_counts"]["by_machine"] == {"M1": 1, "M2": 1}
    assert op["alert_counts"]["alert_rate_per_machine_shift"] == 0.2
    assert op["alerts"][0]["alert_id"] == "ALT-AVAILABILITY_DROP-M1-S2"


def test_alert_sharing_evidence_with_injected_label_is_informational_only():
    alerts = [("ALT-1", "AVAILABILITY_DROP", "M1", "S2", ["AGG-M1-S2", "CNT-1"])]
    op = report(make_db([GT1], [AN1], alerts))["operational"]
    link = op["alerts_sharing_evidence_with_injected_dq_labels"]
    assert link["items"] == [{"alert_id": "ALT-1", "machine_id": "M1", "shift_instance_id": "S2", "truth_ids": ["GT-1"]}]
    assert op["precision"] is None


def test_operational_metrics_with_external_labels():
    alerts = [("ALT-A", "AVAILABILITY_DROP", "M1", "S1", ["AGG-1"]), ("ALT-B", "AVAILABILITY_DROP", "M1", "S2", ["AGG-2"])]
    labels = [{"id": "L1", "type": "AVAILABILITY_DROP", "machine_id": "M1", "shift_instance_id": "S1"},
              {"id": "L2", "type": "QUALITY_DROP", "machine_id": "M2", "shift_instance_id": "S3"}]
    op = report(make_db([], [], alerts), operational_labels=labels)["operational"]
    assert op["status"] == "EVALUATED"
    assert op["precision"] == 0.5 and op["recall"] == 0.5
    assert [m["alert_id"] for m in op["matches"]] == ["ALT-A"]
    assert [f["alert_id"] for f in op["false_positive_items"]] == ["ALT-B"]
    assert [x["label_id"] for x in op["missed_detections"]] == ["L2"]
    assert op["per_type"]["QUALITY_DROP"]["recall"] == 0.0


def test_machine_shift_universe_respects_effective_dates():
    p = make_db(machines=0, shifts=4)
    c = sqlite3.connect(p)
    c.execute("INSERT INTO machine VALUES ('M1','2026-01-02','2026-01-03')")
    c.execute("INSERT INTO machine VALUES ('M2',NULL,NULL)")
    c.commit(); c.close()
    assert report(p)["dataset"]["machine_shifts"] == 2 + 4


def test_repeated_evaluation_identical_and_database_untouched():
    p = make_db([GT1, GT2], [AN1], [("ALT-1", "AVAILABILITY_DROP", "M1", "S1", ["AGG-1"])])
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    a, b = report(p), report(p)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert hashlib.sha256(p.read_bytes()).hexdigest() == h
    conn = E.connect_readonly(p)
    try:
        conn.execute("DELETE FROM anomaly")
        raise AssertionError("evaluator connection must be read-only")
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()


def test_cli_writes_report_and_reports_errors():
    p = make_db([GT1], [AN1])
    out = Path(tempfile.mkdtemp())
    assert E.main(["--db", str(p), "--report", str(out / "r.json"), "--markdown", str(out / "r.md"), "--no-timestamp"]) == 0
    first = (out / "r.json").read_bytes()
    assert E.main(["--db", str(p), "--report", str(out / "r.json"), "--no-timestamp"]) == 0
    assert (out / "r.json").read_bytes() == first                       # byte-identical rerun
    rep = json.loads(first)
    assert rep["evaluated_at"] is None and rep["data_quality"]["recall"] == 1.0 and (out / "r.md").exists()
    assert E.main(["--db", str(out / "missing.db")]) == 1
    bad = out / "bad.json"
    bad.write_text(json.dumps([{"machine_id": "M1"}]), encoding="utf-8")
    assert E.main(["--db", str(p), "--operational-labels", str(bad)]) == 2


def test_cli_operational_labels_csv():
    p = make_db([], [], [("ALT-A", "AVAILABILITY_DROP", "M1", "S1", ["AGG-1"])])
    lab = Path(tempfile.mkdtemp()) / "l.csv"
    lab.write_text("machine_id,shift_instance_id,anomaly_type\nM1,S1,AVAILABILITY_DROP\n", encoding="utf-8")
    labels = E.load_operational_labels(lab)
    assert labels[0]["id"] == "OPLBL-000001"
    assert report(p, operational_labels=labels)["operational"]["recall"] == 1.0


def test_evaluation_is_separate_from_detection():
    root = Path(__file__).resolve().parent.parent / "backend" / "services" / "anomaly"
    for f in root.glob("*.py"):
        assert "services.evaluation" not in f.read_text(encoding="utf-8"), f.name
    ev = (Path(E.__file__)).read_text(encoding="utf-8")
    assert "services.anomaly" not in ev.replace("python -m backend.services.anomaly.runner", "")
    assert tuple(E.DQ_TYPES) == ("OVERLAPPING_STATES", "COUNT_DECREASE", "SCRAP_EXCEEDS_TOTAL",
                                 "WRONG_CYCLE_TIME", "DOWNTIME_IN_PLANNED_BREAK")
