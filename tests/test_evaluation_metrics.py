"""Phase 5 pure metric tests (no database; plain asserts so they also run without pytest)."""
from backend.services.evaluation import metrics as X


def L(i, t="COUNT_DECREASE", m="M1", ev=None):
    return {"id": i, "type": t, "machine_id": m, "evidence_ids": ev or [f"E-{i}"]}


def P(i, t="COUNT_DECREASE", m="M1", ev=None, shift="S1"):
    return {"id": i, "type": t, "machine_id": m, "evidence_ids": ev or [f"E-{i}"], "shift_instance_id": shift}


def test_safe_div_and_f1_zero_denominators():
    assert X.safe_div(1, 0) is None and X.safe_div(0, 5) == 0.0
    assert X.f1_score(None, 0.5) is None and X.f1_score(0.5, None) is None
    assert X.f1_score(0.0, 0.0) == 0.0
    assert abs(X.f1_score(1.0, 0.5) - 2 / 3) < 1e-12


def test_perfect_match():
    r = X.evaluate_group([L("a"), L("b", "OVERLAPPING_STATES")], [P("x", ev=["E-a"]), P("y", "OVERLAPPING_STATES", ev=["E-b"])],
                         X.match_by_evidence, {"COUNT_DECREASE": 10, "OVERLAPPING_STATES": 10})
    assert (r["precision"], r["recall"], r["f1"], r["false_positive_rate"]) == (1.0, 1.0, 1.0, 0.0)
    assert r["type_coverage"] == 1.0 and r["missed_detections"] == [] and r["false_positive_items"] == []
    assert [(m["label_id"], m["prediction_id"]) for m in r["matches"]] == [("a", "x"), ("b", "y")]


def test_missed_detection_lowers_recall_only():
    r = X.evaluate_group([L("a"), L("b")], [P("x", ev=["E-a"])], X.match_by_evidence, {"COUNT_DECREASE": 10})
    assert r["precision"] == 1.0 and r["recall"] == 0.5 and abs(r["f1"] - 2 / 3) < 1e-12
    assert [m["label_id"] for m in r["missed_detections"]] == ["b"] and r["counts"]["false_negatives"] == 1


def test_false_positive_lowers_precision_only_and_gives_fpr():
    r = X.evaluate_group([L("a")], [P("x", ev=["E-a"]), P("z", ev=["E-none"])], X.match_by_evidence,
                         {"COUNT_DECREASE": 10})
    assert r["precision"] == 0.5 and r["recall"] == 1.0
    assert r["counts"]["false_positives"] == 1 and r["counts"]["true_negatives"] == 9
    assert abs(r["false_positive_rate"] - 1 / 10) < 1e-12
    assert [f["prediction_id"] for f in r["false_positive_items"]] == ["z"]


def test_match_requires_same_machine_type_and_shared_evidence():
    labels = [L("a", ev=["E1"])]
    assert X.match_by_evidence(labels, [P("x", m="M2", ev=["E1"])]) == []
    assert X.match_by_evidence(labels, [P("x", t="OVERLAPPING_STATES", ev=["E1"])]) == []
    assert X.match_by_evidence(labels, [P("x", ev=["E9"])]) == []
    assert len(X.match_by_evidence(labels, [P("x", ev=["E1", "E9"])])) == 1


def test_empty_dataset_everything_undefined_not_perfect():
    r = X.evaluate_group([], [], X.match_by_evidence, {})
    assert r["precision"] is None and r["recall"] is None and r["f1"] is None
    assert r["false_positive_rate"] is None and r["type_coverage"] is None
    assert r["counts"]["labels"] == 0 and r["counts"]["predictions"] == 0
    assert set(r["undefined_reason"]) >= {"precision", "recall", "f1"}


def test_no_labels_with_predictions_and_no_predictions_with_labels():
    r = X.evaluate_group([], [P("x")], X.match_by_evidence, {"COUNT_DECREASE": 5})
    assert r["precision"] == 0.0 and r["recall"] is None and r["f1"] is None
    r = X.evaluate_group([L("a")], [], X.match_by_evidence, {"COUNT_DECREASE": 5})
    assert r["precision"] is None and r["recall"] == 0.0 and r["f1"] is None and r["false_positive_rate"] == 0.0


def test_unknown_universe_makes_fpr_undefined():
    r = X.evaluate_group([L("a")], [P("x", ev=["E-a"])], X.match_by_evidence, None)
    assert r["false_positive_rate"] is None and r["counts"]["true_negatives"] is None
    r = X.evaluate_group([L("a")], [P("x", ev=["E-a"])], X.match_by_evidence, {"COUNT_DECREASE": 0})
    assert r["false_positive_rate"] is None            # zero negative units


def test_per_type_breakdown_and_type_coverage():
    labels = [L("a"), L("b", "OVERLAPPING_STATES")]
    r = X.evaluate_group(labels, [P("x", ev=["E-a"])], X.match_by_evidence,
                         {"COUNT_DECREASE": 9, "OVERLAPPING_STATES": 9})
    assert r["per_type"]["COUNT_DECREASE"]["recall"] == 1.0 and r["per_type"]["OVERLAPPING_STATES"]["recall"] == 0.0
    assert r["type_coverage"] == 0.5


def test_match_by_key_for_operational_labels():
    lab = {"id": "o1", "type": "AVAILABILITY_DROP", "machine_id": "M1", "shift_instance_id": "S1"}
    hit = {"id": "ALT-1", "type": "AVAILABILITY_DROP", "machine_id": "M1", "shift_instance_id": "S1"}
    miss = {"id": "ALT-2", "type": "AVAILABILITY_DROP", "machine_id": "M1", "shift_instance_id": "S2"}
    r = X.evaluate_group([lab], [hit, miss], X.match_by_key, {"AVAILABILITY_DROP": 20})
    assert r["precision"] == 0.5 and r["recall"] == 1.0
    assert r["false_positive_items"][0]["prediction_id"] == "ALT-2"


def test_repeated_evaluation_is_identical():
    a = X.evaluate_group([L("a"), L("b")], [P("x", ev=["E-a"]), P("z")], X.match_by_evidence, {"COUNT_DECREASE": 9})
    b = X.evaluate_group([L("b"), L("a")], [P("z"), P("x", ev=["E-a"])], X.match_by_evidence, {"COUNT_DECREASE": 9})
    assert a == b
