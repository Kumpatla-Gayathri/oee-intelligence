"""Pure workflow rules (no database)."""
STATUSES = ("OPEN", "IN_PROGRESS", "RESOLVED")
TRANSITIONS = {"OPEN": {"IN_PROGRESS", "RESOLVED"},
               "IN_PROGRESS": {"OPEN", "RESOLVED"},
               "RESOLVED": {"IN_PROGRESS"}}           # reopen goes through IN_PROGRESS
ACTIVE = ("OPEN", "IN_PROGRESS")


class WorkflowError(ValueError):
    pass


def check_transition(old: str, new: str) -> None:
    if new not in STATUSES:
        raise WorkflowError(f"unknown status '{new}'; allowed: {', '.join(STATUSES)}")
    if new == old:
        raise WorkflowError(f"investigation is already {old}")
    if new not in TRANSITIONS[old]:
        raise WorkflowError(f"cannot move from {old} to {new}; allowed: {', '.join(sorted(TRANSITIONS[old]))}")


def merge_evidence(existing, additions):
    """Order-preserving union; blanks dropped."""
    return list(dict.fromkeys([e for e in list(existing) + [a.strip() for a in additions] if e]))


def next_investigation_id(last_id):
    n = int(last_id.split("-")[1]) + 1 if last_id else 1
    return f"INV-{n:06d}"


def title_for(anomaly_type, machine_id, shift_instance_id):
    return f"{anomaly_type.replace('_', ' ').title()} on {machine_id} ({shift_instance_id})"
