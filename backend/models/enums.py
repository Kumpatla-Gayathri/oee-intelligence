"""Allowed values (used by CHECK constraints, generator, detectors and validation)."""
STATES = ("RUN", "MICRO_STOP", "CHANGEOVER", "BREAKDOWN", "IDLE", "PLANNED_BREAK")
ORDER_STATUS = ("PLANNED", "RUNNING", "COMPLETED", "CANCELLED")
SHIFT_STATUS = ("SCHEDULED", "COMPLETED")
ENTITY_STATUS = ("ACTIVE", "INACTIVE")
REASON_CATEGORY = ("PLANNED", "UNPLANNED", "PERFORMANCE_LOSS")
ISSUE_TYPES = (
    "OVERLAPPING_STATES",
    "COUNT_DECREASE",
    "SCRAP_EXCEEDS_TOTAL",
    "WRONG_CYCLE_TIME",
    "DOWNTIME_IN_PLANNED_BREAK",
)
SEVERITY = ("LOW", "MEDIUM", "HIGH")
AGG_STATUS = ("OK", "DATA_QUALITY_WARNING", "INSUFFICIENT_DATA")


def in_list(col: str, values) -> str:
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"
