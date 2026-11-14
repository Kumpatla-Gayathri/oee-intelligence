"""Phase 5 evaluation: compare stored detector output with ground truth. READ-ONLY (sqlite opened with mode=ro).

Detection never imports this module; this module never calls detection. Run detection first
(python -m backend.services.anomaly.runner --db <db>), then:

    python -m backend.services.evaluation.evaluator --db data/small.db
    python -m backend.services.evaluation.evaluator --db data/small.db --report data/evaluation.json --markdown data/evaluation.md
    python -m backend.services.evaluation.evaluator --db data/small.db --operational-labels my_labels.json

Data-quality anomalies (table `anomaly`) are scored against `ground_truth` (injected labels). Operational alerts
(table `alert`) are scored ONLY against externally supplied operational labels; the generator creates none, so by
default operational precision/recall are reported as not computable.
"""
import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from backend.services.evaluation import metrics as X

DQ_TYPES = ("OVERLAPPING_STATES", "COUNT_DECREASE", "SCRAP_EXCEEDS_TOTAL", "WRONG_CYCLE_TIME",
            "DOWNTIME_IN_PLANNED_BREAK")
OPERATIONAL_TYPES = ("AVAILABILITY_DROP", "PERFORMANCE_DROP", "QUALITY_DROP")
SCHEMA_VERSION = 1

METRIC_DEFINITIONS = {
    "precision": "true-positive predictions / predictions; null if no predictions",
    "recall": "labels detected by >=1 prediction / labels; null if no labels",
    "f1": "2PR/(P+R); null if P or R is null; 0.0 if P+R == 0",
    "false_positive_rate": "FP / (FP + TN); negatives = machine-shift x type units without a label; null if none",
    "label_coverage": "same value as recall (share of labels detected)",
    "type_coverage": "issue types with >=1 detected label / issue types that have labels; null if none",
    "false_positives": "predictions matching no label", "missed_detections": "labels matched by no prediction",
    "matching_rule_data_quality": "same machine + same issue type + >=1 shared evidence id",
    "matching_rule_operational_labels": "same machine + shift + alert type",
    "note": "Data-quality metrics are NOT operational-alert accuracy and are reported separately.",
}


def connect_readonly(db_path):
    p = Path(db_path).resolve()
    if not p.exists():
        raise FileNotFoundError(str(p))
    conn = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _has(conn, table):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _rows(conn, table, order):
    return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")] if _has(conn, table) else []


def _ev(text):
    try:
        v = json.loads(text) if text else []
    except (TypeError, ValueError):
        return []
    return [str(i) for i in v] if isinstance(v, list) else []


def count_machine_shifts(conn):
    """machine x shift_instance pairs, honouring machine effective dates (same rule as the OEE engine)."""
    if not (_has(conn, "machine") and _has(conn, "shift_instance")):
        return None
    return conn.execute(
        "SELECT COUNT(*) FROM machine m JOIN shift_instance s ON "
        "(m.effective_from IS NULL OR m.effective_from <= s.shift_date) AND "
        "(m.effective_to IS NULL OR s.shift_date <= m.effective_to)").fetchone()[0]


def load_operational_labels(path):
    p = Path(path)
    raw = list(csv.DictReader(p.open(encoding="utf-8-sig", newline=""))) if p.suffix.lower() == ".csv" \
        else json.loads(p.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("labels", [])
    out = []
    for i, r in enumerate(raw, 1):
        t = r.get("anomaly_type") or r.get("type")
        if t not in OPERATIONAL_TYPES or not r.get("machine_id") or not r.get("shift_instance_id"):
            raise ValueError(f"operational label #{i} needs machine_id, shift_instance_id and anomaly_type "
                             f"in {OPERATIONAL_TYPES}: {r}")
        out.append({"id": r.get("label_id") or f"OPLBL-{i:06d}", "type": t, "machine_id": r["machine_id"],
                    "shift_instance_id": r["shift_instance_id"]})
    return out


def _negatives(types, labels, machine_shifts):
    if machine_shifts is None:
        return None
    return {t: max(0, machine_shifts - sum(1 for l in labels if l["type"] == t)) for t in types}


def evaluate_data_quality(conn, machine_shifts):
    truths = _rows(conn, "ground_truth", "truth_id")
    anoms = _rows(conn, "anomaly", "anomaly_id")
    labels = [{"id": t["truth_id"], "type": t["issue_type"], "machine_id": t["machine_id"],
               "evidence_ids": _ev(t["evidence_ids"]), "expected_decision": t.get("expected_decision")}
              for t in truths]
    preds = [{"id": a["anomaly_id"], "type": a["issue_type"], "machine_id": a["machine_id"],
              "shift_instance_id": a["shift_instance_id"], "evidence_ids": _ev(a["evidence_ids"]),
              "severity": a["severity"], "expected_decision": a["expected_decision"]} for a in anoms]
    res = X.evaluate_group(labels, preds, X.match_by_evidence, _negatives(DQ_TYPES, labels, machine_shifts))
    by_l, by_p = {l["id"]: l for l in labels}, {p["id"]: p for p in preds}
    agree = 0
    for m in res["matches"]:
        ok = by_l[m["label_id"]]["expected_decision"] == by_p[m["prediction_id"]]["expected_decision"]
        m.update(truth_id=m.pop("label_id"), anomaly_id=m.pop("prediction_id"), expected_decision_agrees=ok)
        agree += ok
    for m in res["missed_detections"]:
        m["truth_id"] = m.pop("label_id")
    for f in res["false_positive_items"]:
        f["anomaly_id"] = f.pop("prediction_id")
    res["expected_decision_agreement"] = {"agreeing_matches": agree, "matches": len(res["matches"]),
                                          "rate": X.safe_div(agree, len(res["matches"]))}
    res["status"] = "EVALUATED" if labels else "NO_LABELS"
    if not labels:
        res["reason"] = "ground_truth is empty or missing; recall/precision are undefined"
    return res


def evaluate_operational(conn, machine_shifts, labels=None):
    alerts = _rows(conn, "alert", "alert_id")
    preds = [{"id": a["alert_id"], "type": a["anomaly_type"], "machine_id": a["machine_id"],
              "shift_instance_id": a["shift_instance_id"], "evidence_ids": _ev(a["evidence_ids"]),
              "severity": a["severity"]} for a in alerts]
    out = {"alert_counts": {
        "total": len(preds), "by_type": dict(sorted(Counter(p["type"] for p in preds).items())),
        "by_severity": dict(sorted(Counter(p["severity"] for p in preds).items())),
        "by_machine": dict(sorted(Counter(p["machine_id"] for p in preds).items())),
        "alerted_machine_shifts": len({(p["machine_id"], p["shift_instance_id"]) for p in preds}),
        "alert_rate_per_machine_shift": X.safe_div(len(preds), machine_shifts)},
        "alerts": [{k: p[k] for k in ("id", "type", "machine_id", "shift_instance_id", "severity")} for p in preds]}
    for a in out["alerts"]:
        a["alert_id"] = a.pop("id")
    truths = _rows(conn, "ground_truth", "truth_id")          # injected DQ labels: informational link only
    linked = []
    for p in preds:
        tids = sorted(t["truth_id"] for t in truths
                      if t["machine_id"] == p["machine_id"] and set(_ev(t["evidence_ids"])) & set(p["evidence_ids"]))
        if tids:
            linked.append({"alert_id": p["id"], "machine_id": p["machine_id"],
                           "shift_instance_id": p["shift_instance_id"], "truth_ids": tids})
    out["alerts_sharing_evidence_with_injected_dq_labels"] = {
        "items": linked, "note": "informational only; NOT a precision or recall measure"}
    if labels is None:
        out.update(status="LABELS_UNAVAILABLE",
                   reason="No reliable operational ground-truth labels exist (the generator only injects "
                          "data-quality issues). Operational precision, recall, F1 and false-positive rate cannot "
                          "be calculated. Supply --operational-labels to enable them.",
                   precision=None, recall=None, f1=None, false_positive_rate=None)
        return out
    res = X.evaluate_group(labels, preds, X.match_by_key, _negatives(OPERATIONAL_TYPES, labels, machine_shifts))
    for m in res["matches"]:
        m["alert_id"] = m.pop("prediction_id")
    for f in res["false_positive_items"]:
        f["alert_id"] = f.pop("prediction_id")
    res.update(out)
    res["status"] = "EVALUATED"
    res["label_source"] = "external operational labels file"
    return res


def build_report(conn, operational_labels=None, machine_shifts=None, evaluated_at=None) -> dict:
    ms = machine_shifts if machine_shifts is not None else count_machine_shifts(conn)
    dq = evaluate_data_quality(conn, ms)
    op = evaluate_operational(conn, ms, operational_labels)
    flagged = {(a["machine_id"], a["shift_instance_id"]) for a in op["alerts"]} | \
              {(m["machine_id"], m["shift_instance_id"]) for m in dq["matches"]} | \
              {(f["machine_id"], f["shift_instance_id"]) for f in dq["false_positive_items"]}
    return {"schema_version": SCHEMA_VERSION, "evaluated_at": evaluated_at,
            "dataset": {"machine_shifts": ms, "flagged_machine_shifts": len(flagged),
                        "flagged_machine_shift_rate": X.safe_div(len(flagged), ms)},
            "metric_definitions": METRIC_DEFINITIONS, "data_quality": dq, "operational": op}


def _f(v):
    return "n/a" if v is None else f"{v:.3f}"


def render_text(rep) -> str:
    d, o, ds = rep["data_quality"], rep["operational"], rep["dataset"]
    c = d["counts"]
    L = [f"Machine-shifts: {ds['machine_shifts']}   flagged: {ds['flagged_machine_shifts']}",
         "", f"DATA-QUALITY anomalies vs ground truth [{d['status']}]",
         f"  labels={c['labels']} predictions={c['predictions']} detected={c['true_positive_labels']} "
         f"missed={c['false_negatives']} false_positives={c['false_positives']}",
         f"  precision={_f(d['precision'])} recall={_f(d['recall'])} f1={_f(d['f1'])} "
         f"FPR={_f(d['false_positive_rate'])} type_coverage={_f(d['type_coverage'])}"]
    for t, m in d["per_type"].items():
        L.append(f"    {t:<28} P={_f(m['precision'])} R={_f(m['recall'])} F1={_f(m['f1'])} "
                 f"FN={m['counts']['false_negatives']} FP={m['counts']['false_positives']}")
    L += ["", f"OPERATIONAL alerts [{o['status']}]  total={o['alert_counts']['total']} "
              f"by_type={o['alert_counts']['by_type']}"]
    if o["status"] == "LABELS_UNAVAILABLE":
        L.append("  " + o["reason"])
    else:
        L.append(f"  precision={_f(o['precision'])} recall={_f(o['recall'])} f1={_f(o['f1'])} "
                 f"FPR={_f(o['false_positive_rate'])}")
    return "\n".join(L)


def main(argv=None):
    p = argparse.ArgumentParser(description="Evaluate stored anomaly detection (read-only).")
    p.add_argument("--db", required=True)
    p.add_argument("--report", help="write the full JSON report here")
    p.add_argument("--markdown", help="write the text summary here")
    p.add_argument("--operational-labels", help="JSON/CSV of reliable operational labels (optional)")
    p.add_argument("--machine-shifts", type=int, help="override the evaluation-universe size")
    p.add_argument("--no-timestamp", action="store_true", help="omit evaluated_at (byte-identical reruns)")
    a = p.parse_args(argv)
    try:
        conn = connect_readonly(a.db)
    except FileNotFoundError as e:
        print(f"ERROR: {e} not found")
        return 1
    try:
        labels = load_operational_labels(a.operational_labels) if a.operational_labels else None
    except (OSError, ValueError) as e:
        print(f"ERROR: {e}")
        return 2
    with conn:
        rep = build_report(conn, labels, a.machine_shifts,
                           None if a.no_timestamp else datetime.now().replace(microsecond=0).isoformat())
    conn.close()
    text = render_text(rep)
    print(text)
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(json.dumps(rep, indent=2, sort_keys=True), encoding="utf-8")
    if a.markdown:
        Path(a.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(a.markdown).write_text("```\n" + text + "\n```\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
