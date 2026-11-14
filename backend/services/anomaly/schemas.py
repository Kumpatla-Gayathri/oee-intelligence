"""Plain dataclasses (no DB dependency)."""
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class ShiftKpi:
    """Everything a detector may know about one machine-shift (built from the Phase 3 result)."""
    aggregate_id: str
    machine_id: str
    shift_instance_id: str
    status: str = "OK"
    flags: tuple = ()
    evidence: dict = field(default_factory=dict)       # engine flag -> source record ids
    availability: Optional[float] = None
    performance: Optional[float] = None
    quality: Optional[float] = None
    oee: Optional[float] = None
    planned_production_seconds: float = 0.0
    operating_seconds: float = 0.0
    downtime_seconds: float = 0.0
    unreconciled_seconds: float = 0.0
    overlap_seconds: float = 0.0
    break_excluded_downtime_seconds: float = 0.0
    total_count: int = 0
    order_ids: tuple = ()
    config_ids: tuple = ()


@dataclass
class OperationalAlert:
    alert_id: str
    anomaly_type: str
    machine_id: str
    shift_instance_id: str
    order_id: Optional[str]
    severity: str
    kpi: str
    measured_value: float
    baseline_value: float
    baseline_n: int
    deviation_score: float
    kpi_impact: dict
    evidence_ids: list
    explanation: str
    recommended_action: str
    detected_at: Optional[datetime] = None
    ground_truth_ref: Optional[str] = None

    def to_dict(self):
        return asdict(self)


@dataclass
class DataQualityWarning:
    issue_type: str
    machine_id: str
    shift_instance_id: str
    order_id: Optional[str]
    severity: str
    evidence_ids: list
    explanation: str
    expected_decision: str
    source_flag: str
    aggregate_id: str
    measured: dict = field(default_factory=dict)

    @property
    def key(self):
        return (self.machine_id, self.shift_instance_id, self.issue_type)

    def to_dict(self):
        return asdict(self)


@dataclass
class DetectionResult:
    alerts: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    skipped: list = field(default_factory=list)        # {machine_id, shift_instance_id, kpi, reason}
    dq_flags_not_alerted: dict = field(default_factory=dict)   # flag -> count of machine-shifts (stay flags only)
    machine_shifts: int = 0
