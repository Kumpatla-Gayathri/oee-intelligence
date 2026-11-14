"""POST-HOC evaluation against injected ground truth. Detectors, runner (detection path) and store never import
this module's data; only `evaluate` reads ground_truth, after detection results are stored.

Match rule: same machine AND shared evidence id. Data-quality anomaly <-> label also needs the same issue_type
(EXACT_TYPE). Operational alerts that merely share evidence with a label are linked as RELATED (never counted
as hits or false positives)."""
import json
from collections import defaultdict
from datetime import datetime

from sqlalchemy import delete, select

from backend import models as m
from backend.models.alerts import Alert, AnomalyTruthLink, ensure_alert_tables


def evaluate(session, evaluated_at=None, write_links=True) -> dict:
    ensure_alert_tables(session.get_bind())
    evaluated_at = evaluated_at or datetime.now().replace(microsecond=0)
    truths = session.execute(select(m.GroundTruth).order_by(m.GroundTruth.truth_id)).scalars().all()
    anomalies = session.execute(select(m.Anomaly).order_by(m.Anomaly.anomaly_id)).scalars().all()
    alerts = session.execute(select(Alert).order_by(Alert.alert_id)).scalars().all()
    links, hit_truth, hit_anom = [], defaultdict(list), set()
    per_type = defaultdict(lambda: {"labels": 0, "detected": 0, "false_positives": 0})
    decision_ok = 0
    for t in truths:
        per_type[t.issue_type]["labels"] += 1
        tev = set(json.loads(t.evidence_ids))
        for a in anomalies:
            shared = tev & set(json.loads(a.evidence_ids))
            if a.machine_id == t.machine_id and a.issue_type == t.issue_type and shared:
                hit_truth[t.truth_id].append(a.anomaly_id)
                hit_anom.add(a.anomaly_id)
                decision_ok += a.expected_decision == t.expected_decision
                links.append(("ANOMALY", a.anomaly_id, t.truth_id, "EXACT_TYPE", sorted(shared)))
        for al in alerts:
            shared = tev & set(json.loads(al.evidence_ids))
            if al.machine_id == t.machine_id and shared:
                links.append(("ALERT", al.alert_id, t.truth_id, "RELATED", sorted(shared)))
    for t in truths:
        if hit_truth.get(t.truth_id):
            per_type[t.issue_type]["detected"] += 1
    false_pos = [a for a in anomalies if a.anomaly_id not in hit_anom]
    for a in false_pos:
        per_type[a.issue_type]["false_positives"] += 1
    detected = sum(1 for t in truths if hit_truth.get(t.truth_id))
    if write_links:
        session.execute(delete(AnomalyTruthLink))
        for kind, sid, tid, mt, ev in links:
            session.add(AnomalyTruthLink(link_id=f"LNK-{sid}-{tid}", source_kind=kind, source_id=sid, truth_id=tid,
                                         match_type=mt, matched_evidence=json.dumps(ev), evaluated_at=evaluated_at))
        refs = defaultdict(list)
        for kind, sid, tid, _, _ in links:
            if kind == "ALERT":
                refs[sid].append(tid)
        for al in alerts:
            al.ground_truth_ref = ",".join(sorted(set(refs[al.alert_id]))) or None
        session.commit()
    return {"labels": len(truths), "detected": detected,
            "missed": [t.truth_id for t in truths if not hit_truth.get(t.truth_id)],
            "false_positive_anomalies": [a.anomaly_id for a in false_pos],
            "recall": detected / len(truths) if truths else None,
            "precision": (len(anomalies) - len(false_pos)) / len(anomalies) if anomalies else None,
            "expected_decision_agreement": decision_ok, "per_issue_type": dict(per_type),
            "matches": {k: v for k, v in hit_truth.items()}, "links_written": len(links) if write_links else 0}
