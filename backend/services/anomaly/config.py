"""Thresholds and rules. Everything tunable lives here; nothing in detectors is hard-coded."""
MIN_BASELINE_SHIFTS = 5          # other eligible shifts of the same machine needed to form a baseline
Z_THRESHOLD = 3.0                # robust z (median/MAD) must be <= -Z_THRESHOLD
SCALE_FLOOR = 0.02               # lower bound for the robust scale so a flat baseline cannot over-fire
MIN_ABS_DROP = {"availability": 0.10, "performance": 0.10, "quality": 0.05}   # absolute ratio drop required
UNRECONCILED_MAX_SHARE = 0.05    # availability is unreliable above this share of unreconciled planned time

# Flags that make a KPI untrustworthy for operational detection (the KPI is skipped and excluded from baselines).
KPI_UNRELIABLE_FLAGS = {
    "availability": {"OVERLAPPING_STATES", "NO_STATE_DATA"},
    "performance": {"CONFIG_CONFLICT", "MISSING_CYCLE_TIME", "PERFORMANCE_OVER_100", "COUNT_DECREASE", "NO_COUNT_DATA"},
    "quality": {"SCRAP_EXCEEDS_TOTAL", "COUNT_DECREASE", "NO_COUNT_DATA"},
}
OPERATIONAL_TYPE = {"availability": "AVAILABILITY_DROP", "performance": "PERFORMANCE_DROP", "quality": "QUALITY_DROP"}
RECOMMENDED_ACTION = {
    "AVAILABILITY_DROP": "Review the listed downtime events and their reasons; check maintenance/material/operator "
                         "readiness for this machine and shift.",
    "PERFORMANCE_DROP": "Check speed losses and micro-stops on the listed orders; verify the machine ran at its "
                        "ideal cycle time.",
    "QUALITY_DROP": "Inspect the listed quality records and the process settings for the affected orders; "
                    "sample-check output before release.",
}

# Data-quality warnings go to the existing `anomaly` table (its CHECK allows only these five issue types).
# engine flag -> anomaly issue type. Every other engine flag stays a flag on oee_aggregate (no anomaly row).
DQ_FLAG_TO_ISSUE = {
    "OVERLAPPING_STATES": "OVERLAPPING_STATES",
    "COUNT_DECREASE": "COUNT_DECREASE",
    "SCRAP_EXCEEDS_TOTAL": "SCRAP_EXCEEDS_TOTAL",
    "DOWNTIME_IN_PLANNED_BREAK": "DOWNTIME_IN_PLANNED_BREAK",
    "CONFIG_CONFLICT": "WRONG_CYCLE_TIME",
    "PERFORMANCE_OVER_100": "WRONG_CYCLE_TIME",   # only when no CONFIG_CONFLICT in the same shift
}
# Same decisions as the Phase 2 generator (a test asserts equality); copied so detectors never import generator code.
EXPECTED_DECISION = {
    "OVERLAPPING_STATES": "EXCLUDE_OVERLAP_AND_REVIEW",
    "COUNT_DECREASE": "EXCLUDE_DELTA_REQUEST_CORRECTION",
    "SCRAP_EXCEEDS_TOTAL": "QUARANTINE_QUALITY_AND_REVIEW",
    "WRONG_CYCLE_TIME": "REVIEW_IDEAL_CYCLE_TIME",
    "DOWNTIME_IN_PLANNED_BREAK": "TREAT_AS_PLANNED_BREAK",
}
OVERLAP_SEC_MEDIUM, OVERLAP_SEC_HIGH = 300, 900
BREAK_DT_SEC_MEDIUM, BREAK_DT_SEC_HIGH = 600, 1800
