"""Pure metric math and matching (stdlib only, no database).

Metric definitions (also emitted in every report under `metric_definitions`):
  label            a ground-truth item that SHOULD be detected.
  prediction       a detector output (data-quality anomaly or operational alert).
  TP (labels)      labels matched by >= 1 prediction.            FN = labels - TP      (missed detections)
  TP (predictions) predictions matched to >= 1 label.            FP = predictions - TP (false positives)
  precision        TP_predictions / predictions                  None when there are no predictions
  recall           TP_labels / labels                            None when there are no labels
  f1               2PR/(P+R)                                     None if P or R is None; 0.0 if P+R == 0
  false_positive_rate   FP / (FP + TN)   TN = negatives - FP, negatives = evaluation units without a label
                        (units = machine-shift x type); None when the number of negative units is 0
  label_coverage   detected labels / labels (same value as recall, named for reporting)   None when no labels
  type_coverage    issue types with >= 1 detected label / issue types that have labels      None when none
Zero denominators NEVER become 1.0 or 0.0 silently: the value is None and `undefined_reason` says why.
"""
from collections import defaultdict


def safe_div(num, den):
    return None if not den else num / den


def f1_score(p, r):
    if p is None or r is None:
        return None
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def match_by_evidence(labels, preds):
    """Same machine, same type, >=1 shared evidence id. Returns [(label, pred, sorted shared ids)]."""
    out = []
    for lab in labels:
        lev = set(lab["evidence_ids"])
        for p in preds:
            shared = lev & set(p["evidence_ids"])
            if lab["machine_id"] == p["machine_id"] and lab["type"] == p["type"] and shared:
                out.append((lab, p, sorted(shared)))
    return out


def match_by_key(labels, preds):
    """Same machine, shift and type (used for externally supplied operational labels)."""
    out = []
    for lab in labels:
        for p in preds:
            if (lab["machine_id"], lab["shift_instance_id"], lab["type"]) == \
               (p["machine_id"], p["shift_instance_id"], p["type"]):
                out.append((lab, p, []))
    return out


def _metrics(n_labels, n_preds, tp_labels, tp_preds, negatives):
    fp = n_preds - tp_preds
    p, r = safe_div(tp_preds, n_preds), safe_div(tp_labels, n_labels)
    tn = None if negatives is None else max(0, negatives - fp)
    fpr = None if negatives is None else safe_div(fp, fp + tn)
    why = {}
    if p is None:
        why["precision"] = "no predictions"
    if r is None:
        why["recall"] = "no labels"
    if p is None or r is None:
        why["f1"] = "needs both precision and recall"
    if negatives is None or (fp + (tn or 0)) == 0:
        why["false_positive_rate"] = "no negative evaluation units"
    return {"counts": {"labels": n_labels, "predictions": n_preds, "true_positive_labels": tp_labels,
                       "true_positive_predictions": tp_preds, "false_negatives": n_labels - tp_labels,
                       "false_positives": fp, "true_negatives": tn, "negative_units": negatives},
            "precision": p, "recall": r, "f1": f1_score(p, r), "false_positive_rate": fpr,
            "label_coverage": r, "undefined_reason": why}


def evaluate_group(labels, preds, matcher, negatives_by_type=None):
    """labels/preds: dicts with id, type, machine_id (+ evidence_ids / shift_instance_id, + extras kept in output).
    negatives_by_type: {type: number of negative units} or None (FPR then undefined)."""
    pairs = matcher(labels, preds)
    hit_labels = {l["id"] for l, _, _ in pairs}
    hit_preds = {p["id"] for _, p, _ in pairs}

    overall_neg = None if negatives_by_type is None else sum(negatives_by_type.values())
    res = _metrics(len(labels), len(preds), len(hit_labels), len(hit_preds), overall_neg)
    per_type = {}
    for t in sorted({l["type"] for l in labels} | {p["type"] for p in preds}):
        ls = [l for l in labels if l["type"] == t]
        ps = [p for p in preds if p["type"] == t]
        neg = None if negatives_by_type is None else negatives_by_type.get(t)
        per_type[t] = _metrics(len(ls), len(ps), sum(l["id"] in hit_labels for l in ls),
                               sum(p["id"] in hit_preds for p in ps), neg)
    lab_types = {l["type"] for l in labels}
    det_types = {l["type"] for l in labels if l["id"] in hit_labels}
    res["type_coverage"] = safe_div(len(det_types), len(lab_types))
    res["per_type"] = per_type
    res["matches"] = sorted(({"label_id": l["id"], "prediction_id": p["id"], "type": p["type"],
                              "machine_id": p["machine_id"], "shift_instance_id": p.get("shift_instance_id"),
                              "shared_evidence_ids": sh} for l, p, sh in pairs),
                            key=lambda x: (x["label_id"], x["prediction_id"]))
    res["missed_detections"] = [{"label_id": l["id"], "type": l["type"], "machine_id": l["machine_id"],
                                 "evidence_ids": l.get("evidence_ids", [])}
                                for l in sorted(labels, key=lambda x: x["id"]) if l["id"] not in hit_labels]
    res["false_positive_items"] = [{"prediction_id": p["id"], "type": p["type"], "machine_id": p["machine_id"],
                                    "shift_instance_id": p.get("shift_instance_id"),
                                    "evidence_ids": p.get("evidence_ids", [])}
                                   for p in sorted(preds, key=lambda x: x["id"]) if p["id"] not in hit_preds]
    return res
