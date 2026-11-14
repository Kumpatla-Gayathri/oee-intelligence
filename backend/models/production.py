from datetime import datetime
from typing import Optional
from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from backend.database import Base
from backend.models.enums import ORDER_STATUS, STATES, in_list


class MachineProductConfig(Base):
    """Bridge machine<->product (N:M) holding the effective-dated ideal cycle time."""
    __tablename__ = "machine_product_config"
    config_id: Mapped[str] = mapped_column(String, primary_key=True)  # CFG-<machine>-<product>-1
    machine_id: Mapped[str] = mapped_column(ForeignKey("machine.machine_id"), nullable=False)
    product_id: Mapped[str] = mapped_column(ForeignKey("product.product_id"), nullable=False)
    ideal_cycle_time_sec: Mapped[float]
    effective_from: Mapped[datetime]
    effective_to: Mapped[Optional[datetime]]
    __table_args__ = (
        UniqueConstraint("machine_id", "product_id", "effective_from"),
        CheckConstraint("ideal_cycle_time_sec > 0"),
    )


class ProductionOrder(Base):
    __tablename__ = "production_order"
    order_id: Mapped[str] = mapped_column(String, primary_key=True)  # PO-2026-000001
    machine_id: Mapped[str] = mapped_column(ForeignKey("machine.machine_id"), nullable=False)
    product_id: Mapped[str] = mapped_column(ForeignKey("product.product_id"), nullable=False)
    planned_qty: Mapped[int]
    status: Mapped[str] = mapped_column(String, default="PLANNED")
    planned_start: Mapped[datetime]
    planned_end: Mapped[datetime]
    actual_start: Mapped[Optional[datetime]]
    actual_end: Mapped[Optional[datetime]]
    __table_args__ = (
        CheckConstraint("planned_qty > 0"),
        CheckConstraint("planned_end > planned_start"),
        CheckConstraint(in_list("status", ORDER_STATUS)),
    )


class StateEvent(Base):
    """One machine state interval. Source record: never edited, overlaps are flagged not fixed."""
    __tablename__ = "state_event"
    event_id: Mapped[str] = mapped_column(String, primary_key=True)  # EVT-<machine>-000001
    machine_id: Mapped[str] = mapped_column(ForeignKey("machine.machine_id"), nullable=False, index=True)
    order_id: Mapped[Optional[str]] = mapped_column(ForeignKey("production_order.order_id"))
    state: Mapped[str] = mapped_column(String, nullable=False)
    start_ts: Mapped[datetime] = mapped_column(index=True)
    end_ts: Mapped[datetime]
    reason_id: Mapped[Optional[str]] = mapped_column(ForeignKey("downtime_reason.reason_id"))
    __table_args__ = (
        CheckConstraint("end_ts > start_ts"),
        CheckConstraint(in_list("state", STATES)),
    )


class ProductionCount(Base):
    """Cumulative counter reading per order. increment_qty is what the machine reported since last reading."""
    __tablename__ = "production_count"
    count_id: Mapped[str] = mapped_column(String, primary_key=True)  # CNT-<order>-000001
    order_id: Mapped[str] = mapped_column(ForeignKey("production_order.order_id"), nullable=False, index=True)
    machine_id: Mapped[str] = mapped_column(ForeignKey("machine.machine_id"), nullable=False, index=True)
    ts: Mapped[datetime] = mapped_column(index=True)
    counter_value: Mapped[int]   # cumulative for the order
    increment_qty: Mapped[int]   # may be negative only in injected anomaly rows (no DB check on purpose)
    __table_args__ = (CheckConstraint("counter_value >= 0"),)


class CountCorrection(Base):
    """Append-only correction. The original ProductionCount row is never changed."""
    __tablename__ = "count_correction"
    correction_id: Mapped[str] = mapped_column(String, primary_key=True)  # COR-<count_id>-1
    count_id: Mapped[str] = mapped_column(ForeignKey("production_count.count_id"), nullable=False)
    order_id: Mapped[str] = mapped_column(ForeignKey("production_order.order_id"), nullable=False)
    corrected_counter_value: Mapped[int]
    reason: Mapped[str] = mapped_column(String, nullable=False)
    created_ts: Mapped[datetime]
    approved_by: Mapped[Optional[str]]


class QualityInspection(Base):
    """Scrap > total is NOT blocked by the DB: it must be storable so the detector can flag it."""
    __tablename__ = "quality_inspection"
    inspection_id: Mapped[str] = mapped_column(String, primary_key=True)  # QC-2026-000001
    order_id: Mapped[str] = mapped_column(ForeignKey("production_order.order_id"), nullable=False, index=True)
    machine_id: Mapped[str] = mapped_column(ForeignKey("machine.machine_id"), nullable=False, index=True)
    ts: Mapped[datetime] = mapped_column(index=True)
    inspected_qty: Mapped[int]
    good_qty: Mapped[int]
    scrap_qty: Mapped[int]
    rework_qty: Mapped[int]
    __table_args__ = (CheckConstraint("inspected_qty >= 0 AND good_qty >= 0 AND scrap_qty >= 0 AND rework_qty >= 0"),)
