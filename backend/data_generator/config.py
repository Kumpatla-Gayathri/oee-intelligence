"""Generator configuration: seed, shift calendar, reasons and scale presets.

The SAME generator code runs for every scale. Only the numbers below change.
"""
from dataclasses import dataclass
from datetime import date, datetime, time

SEED = 20261022
YEAR = 2026
START_DATE = date(2026, 1, 1)
# Fixed timestamp used for 'injected_at' so reruns with the same seed are byte-identical.
GENERATED_AT = datetime(2026, 10, 22, 0, 0, 0)

PLANT_ID = "PLANT-01"
PLANT_NAME = "YottaFlex Plant 1"

# (code, start, end, crosses_midnight)
SHIFT_DEFS = (
    ("A", time(6, 0), time(14, 0), False),
    ("B", time(14, 0), time(22, 0), False),
    ("C", time(22, 0), time(6, 0), True),
)
SHIFT_MINUTES = 480
# (offset from shift start in minutes, duration in minutes, name)
BREAK_TEMPLATES = (
    (120, 10, "Tea break 1"),
    (240, 30, "Lunch break"),
    (360, 10, "Tea break 2"),
)

# (reason_id, code, description, category)
REASONS = (
    ("RSN-001", "MICRO_JAM", "Material jam (micro-stop)", "PERFORMANCE_LOSS"),
    ("RSN-002", "MICRO_SENSOR", "Sensor blocked (micro-stop)", "PERFORMANCE_LOSS"),
    ("RSN-003", "CHANGEOVER", "Product changeover", "PLANNED"),
    ("RSN-004", "MECH_FAILURE", "Mechanical failure", "UNPLANNED"),
    ("RSN-005", "ELEC_FAULT", "Electrical fault", "UNPLANNED"),
    ("RSN-006", "NO_MATERIAL", "Waiting for material", "UNPLANNED"),
    ("RSN-007", "NO_OPERATOR", "No operator available", "UNPLANNED"),
    ("RSN-008", "PLANNED_MAINT", "Planned maintenance", "PLANNED"),
)
MICRO_REASONS = ("RSN-001", "RSN-002")
BREAKDOWN_REASONS = ("RSN-004", "RSN-005")
IDLE_REASONS = ("RSN-006", "RSN-007")
CHANGEOVER_REASON = "RSN-003"
INJECTED_BREAKDOWN_REASON = "RSN-004"

MACHINE_TYPES = ("Filling", "Packaging", "Labeling", "Capping", "Inspection", "Palletizing")

# Documented decision for each deliberately injected issue type.
EXPECTED_DECISION = {
    "OVERLAPPING_STATES": "EXCLUDE_OVERLAP_AND_REVIEW",
    "COUNT_DECREASE": "EXCLUDE_DELTA_REQUEST_CORRECTION",
    "SCRAP_EXCEEDS_TOTAL": "QUARANTINE_QUALITY_AND_REVIEW",
    "WRONG_CYCLE_TIME": "REVIEW_IDEAL_CYCLE_TIME",
    "DOWNTIME_IN_PLANNED_BREAK": "TREAT_AS_PLANNED_BREAK",
}


@dataclass(frozen=True)
class Scale:
    name: str
    lines: int
    machines: int               # total machines (must divide evenly by lines)
    products: int
    days: int
    products_per_machine: int   # how many products each machine is configured for
    count_interval_min: int     # minutes between counter readings
    mean_run_min: float         # mean uninterrupted RUN length (controls event volume)
    two_order_prob: float       # chance a machine-shift runs two products (adds a changeover)
    correction_prob: float      # chance a machine-shift contains a legitimate count correction
    anomalies_per_type: int     # injected anomalies per issue type
    batch_size: int = 20000     # rows buffered before a bulk insert


SCALES = {
    "tiny": Scale("tiny", 1, 2, 3, 3, 3, 15, 105.0, 0.30, 0.30, 1),
    "small": Scale("small", 2, 6, 6, 7, 4, 15, 105.0, 0.15, 0.10, 3),
    "full": Scale("full", 5, 30, 20, 90, 6, 15, 105.0, 0.10, 0.03, 20),
}


def get_scale(scale) -> Scale:
    if isinstance(scale, Scale):
        return scale
    if scale not in SCALES:
        raise ValueError(f"Unknown scale '{scale}'. Choose one of: {', '.join(SCALES)}")
    return SCALES[scale]
