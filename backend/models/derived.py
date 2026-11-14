from datetime import datetime
from typing import Optional
from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from backend.database import Base
from backend.models.enums import AGG_STATUS, ISSUE_TYPES, SEVERITY, in_list


class OEEAggregate(Base):
    """Derived per machine per ShiftInstance. Always recomputable from source records."""
    __tablename__ = "oee_aggregate"
    aggregate_id: Mapped[str] = mapped_column(String, primary_key=True)  # AGG-<machine>-<shift_instance>
    machine_id: Mapped[str] = mapped_column(ForeignKey("machine.machine_id"), nullable=False, index=True)
    shift_instance_id: Mapped[str] = mapped_column(ForeignKey("shift_instance.shift_instance_id"), nullable=False, index=True)
    calendar_seconds: Mapped[float]
    planned_break_seconds: Mapped[float]
    planned_production_seconds: Mapped[float]
    operating_seconds: Mapped[float]
    downtime_seconds: Mapped[float]
    unreconciled_seconds: Mapped[float]
    total_count: Mapped[int]
    good_count: Mapped[int]
    scrap_count: Mapped[int]
    rework_count: Mapped[int]
    ideal_time_seconds: Mapped[float]
    availability: Mapped[Optional[float]]   # decimal ratio 0..1, NULL if denominator is 0
    performance: Mapped[Optional[float]]    # may exceed 1.0 -> flagged, never capped
    quality: Mapped[Optional[float]]
    oee: Mapped[Optional[float]]
    status: Mapped[str] = mapped_column(String, default="OK")
    flags: Mapped[Optional[str]] = mapped_column(Text)  # comma list e.g. PERFORMANCE_OVER_100
    computed_at: Mapped[datetime]
    __table_args__ = (
        UniqueConstraint("machine_id", "shift_instance_id"),
        CheckConstraint(in_list("status", AGG_STATUS)),
    )


class OEEAggregateDetail(Base):
    """Per order/product breakdown so multi-product Performance is traceable."""
    __tablename__ = "oee_aggregate_detail"
    detail_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    aggregate_id: Mapped[str] = mapped_column(ForeignKey("oee_aggregate.aggregate_id"), nullable=False, index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("production_order.order_id"), nullable=False)
    product_id: Mapped[str] = mapped_column(ForeignKey("product.product_id"), nullable=False)
    ideal_cycle_time_sec: Mapped[float]
    total_count: Mapped[int]
    good_count: Mapped[int]
    ideal_time_seconds: Mapped[float]


class Anomaly(Base):
    """Detector OUTPUT. Evidence ids point to source rows."""
    __tablename__ = "anomaly"
    anomaly_id: Mapped[str] = mapped_column(String, primary_key=True)  # ANM-000001
    machine_id: Mapped[str] = mapped_column(ForeignKey("machine.machine_id"), nullable=False, index=True)
    order_id: Mapped[Optional[str]] = mapped_column(ForeignKey("production_order.order_id"))
    shift_instance_id: Mapped[Optional[str]] = mapped_column(ForeignKey("shift_instance.shift_instance_id"))
    issue_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    evidence_ids: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of source record ids
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    expected_decision: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime]
    __table_args__ = (
        CheckConstraint(in_list("issue_type", ISSUE_TYPES)),
        CheckConstraint(in_list("severity", SEVERITY)),
    )


class GroundTruth(Base):
    """Injected-anomaly labels. Deliberately NO foreign keys: detectors must never join to this table."""
    __tablename__ = "ground_truth"
    truth_id: Mapped[str] = mapped_column(String, primary_key=True)  # GT-000001
    issue_type: Mapped[str] = mapped_column(String, nullable=False)
    machine_id: Mapped[str] = mapped_column(String, nullable=False)
    order_id: Mapped[Optional[str]] = mapped_column(String)
    evidence_ids: Mapped[str] = mapped_column(Text, nullable=False)
    expected_decision: Mapped[str] = mapped_column(Text, nullable=False)
    injected_at: Mapped[datetime]
    __table_args__ = (CheckConstraint(in_list("issue_type", ISSUE_TYPES)),)
