"""Pure investigation-rule tests (no database / web framework)."""
from backend.services.investigation import workflow as W


def _raises(fn, *a):
    try:
        fn(*a)
    except W.WorkflowError as e:
        return str(e)
    raise AssertionError("expected WorkflowError")


def test_allowed_transitions():
    for old, new in (("OPEN", "IN_PROGRESS"), ("OPEN", "RESOLVED"), ("IN_PROGRESS", "RESOLVED"),
                     ("IN_PROGRESS", "OPEN"), ("RESOLVED", "IN_PROGRESS")):
        W.check_transition(old, new)


def test_rejected_transitions():
    assert "already OPEN" in _raises(W.check_transition, "OPEN", "OPEN")
    assert "cannot move" in _raises(W.check_transition, "RESOLVED", "OPEN")
    assert "unknown status" in _raises(W.check_transition, "OPEN", "CLOSED")


def test_statuses_and_active_set():
    assert W.STATUSES == ("OPEN", "IN_PROGRESS", "RESOLVED") and W.ACTIVE == ("OPEN", "IN_PROGRESS")


def test_evidence_merge_dedupes_and_keeps_order():
    assert W.merge_evidence(["A", "B"], ["B", " C ", ""]) == ["A", "B", "C"]


def test_id_generation_and_title():
    assert W.next_investigation_id(None) == "INV-000001" and W.next_investigation_id("INV-000009") == "INV-000010"
    assert W.title_for("AVAILABILITY_DROP", "M-01-01", "SHIFT-1") == "Availability Drop on M-01-01 (SHIFT-1)"
