"""Plain dataclasses for calculator inputs and results (no DB or framework dependency)."""
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

# ---------------------------------------------------------------- inputs
@dataclass(frozen=True)
class StateEvt:
    event_id: str
    state: str
    start: datetime
    end: datetime
    order_id: Optional[str] = None


@dataclass(frozen=True)
class Reading:
    count_id: str
    order_id: str
    ts: datetime
    counter_value: int


@dataclass(frozen=True)
class Correction:
    correction_id: str
    count_id: str
    corrected_value: int
    created_ts: datetime


@dataclass(frozen=True)
class Inspection:
    inspection_id: str
    order_id: str
    ts: datetime
    inspected: int
    good: int
    scrap: int
    rework: int


@dataclass(frozen=True)
class OrderInfo:
    order_id: str
    product_id: str
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None


@dataclass(frozen=True)
class Cfg:
    config_id: str
    product_id: str
    ideal_cycle_time_sec: float
    effective_from: datetime
    effective_to: Optional[datetime] = None


@dataclass(frozen=True)
class ShiftWindow:
    shift_instance_id: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class BreakWindow:
    break_id: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class CountDelta:
    """Accepted increment of an order's effective counter, attributed to the reading time."""
    order_id: str
    ts: datetime
    delta: int
    count_id: str
    correction_id: Optional[str] = None


@dataclass(frozen=True)
class CountReject:
    """A reading whose effective value is lower than the last accepted one (delta excluded)."""
    order_id: str
    ts: datetime
    count_id: str
    prev_count_id: Optional[str]
    drop: int
    correction_id: Optional[str] = None


@dataclass
class MachineData:
    events: list = field(default_factory=list)
    readings: dict = field(default_factory=dict)       # order_id -> [Reading]
    corrections: dict = field(default_factory=dict)    # count_id -> [Correction]
    inspections: list = field(default_factory=list)
    orders: dict = field(default_factory=dict)         # order_id -> OrderInfo
    configs: list = field(default_factory=list)


# ---------------------------------------------------------------- results
@dataclass
class DetailResult:
    order_id: str
    product_id: str
    ideal_cycle_time_sec: float
    config_id: Optional[str]
    total_count: int
    good_count: int
    scrap_count: int
    rework_count: int
    ideal_time_seconds: float


@dataclass
class AggregateResult:
    aggregate_id: str
    machine_id: str
    shift_instance_id: str
    calendar_seconds: float = 0.0
    planned_break_seconds: float = 0.0
    planned_production_seconds: float = 0.0
    operating_seconds: float = 0.0
    micro_stop_seconds: float = 0.0            # informational subset of operating_seconds
    downtime_seconds: float = 0.0
    unreconciled_seconds: float = 0.0
    overlap_seconds: float = 0.0               # time covered by >1 event (counted once)
    break_excluded_downtime_seconds: float = 0.0
    total_count: int = 0
    good_count: int = 0
    scrap_count: int = 0
    rework_count: int = 0
    ideal_time_seconds: float = 0.0
    availability: Optional[float] = None
    performance: Optional[float] = None
    quality: Optional[float] = None
    oee: Optional[float] = None
    status: str = "OK"
    flags: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)   # flag -> [source record ids]
    corrections_applied: list = field(default_factory=list)
    details: list = field(default_factory=list)
    computed_at: Optional[datetime] = None

    @property
    def flags_text(self) -> Optional[str]:
        return ",".join(self.flags) if self.flags else None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RollupResult:
    label: str
    members: int = 0
    planned_production_seconds: float = 0.0
    operating_seconds: float = 0.0
    downtime_seconds: float = 0.0
    unreconciled_seconds: float = 0.0
    total_count: int = 0
    good_count: int = 0
    ideal_time_seconds: float = 0.0
    availability: Optional[float] = None
    performance: Optional[float] = None
    quality: Optional[float] = None
    oee: Optional[float] = None
    status: str = "OK"
    flags: list = field(default_factory=list)
    excluded_from_performance: list = field(default_factory=list)  # aggregate_ids
    excluded_from_quality: list = field(default_factory=list)      # aggregate_ids

    def to_dict(self) -> dict:
        return asdict(self)
