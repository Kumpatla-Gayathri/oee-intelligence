from datetime import date, datetime, time
from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.database import Base
from backend.models.enums import SHIFT_STATUS, in_list


class Shift(Base):
    """Reusable shift DEFINITION (e.g. morning shift A). Not tied to a date."""
    __tablename__ = "shift"
    shift_id: Mapped[str] = mapped_column(String, primary_key=True)  # SHIFTDEF-A
    plant_id: Mapped[str] = mapped_column(ForeignKey("plant.plant_id"), nullable=False)
    code: Mapped[str] = mapped_column(String, nullable=False)  # A / B / C
    name: Mapped[str] = mapped_column(String, nullable=False)
    start_time: Mapped[time]
    end_time: Mapped[time]
    crosses_midnight: Mapped[bool] = mapped_column(default=False)
    breaks: Mapped[list["ShiftBreakTemplate"]] = relationship(back_populates="shift")
    __table_args__ = (UniqueConstraint("plant_id", "code"),)


class ShiftBreakTemplate(Base):
    """Break pattern of a shift definition, as offset from shift start."""
    __tablename__ = "shift_break_template"
    template_id: Mapped[str] = mapped_column(String, primary_key=True)
    shift_id: Mapped[str] = mapped_column(ForeignKey("shift.shift_id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    start_offset_min: Mapped[int]
    duration_min: Mapped[int]
    shift: Mapped[Shift] = relationship(back_populates="breaks")
    __table_args__ = (CheckConstraint("duration_min > 0 AND start_offset_min >= 0"),)


class ShiftInstance(Base):
    """An ACTUAL shift on a specific date, e.g. SHIFT-2026-01-01-A."""
    __tablename__ = "shift_instance"
    shift_instance_id: Mapped[str] = mapped_column(String, primary_key=True)
    shift_id: Mapped[str] = mapped_column(ForeignKey("shift.shift_id"), nullable=False)
    plant_id: Mapped[str] = mapped_column(ForeignKey("plant.plant_id"), nullable=False)
    shift_date: Mapped[date]
    start_ts: Mapped[datetime]
    end_ts: Mapped[datetime]
    status: Mapped[str] = mapped_column(String, default="SCHEDULED")
    planned_breaks: Mapped[list["PlannedBreak"]] = relationship(back_populates="shift_instance")
    __table_args__ = (
        UniqueConstraint("plant_id", "shift_date", "shift_id"),
        CheckConstraint("end_ts > start_ts"),
        CheckConstraint(in_list("status", SHIFT_STATUS)),
    )


class PlannedBreak(Base):
    """Concrete break window inside one ShiftInstance (excluded from planned production time)."""
    __tablename__ = "planned_break"
    planned_break_id: Mapped[str] = mapped_column(String, primary_key=True)  # PBRK-<shift_instance_id>-1
    shift_instance_id: Mapped[str] = mapped_column(ForeignKey("shift_instance.shift_instance_id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    start_ts: Mapped[datetime]
    end_ts: Mapped[datetime]
    shift_instance: Mapped[ShiftInstance] = relationship(back_populates="planned_breaks")
    __table_args__ = (CheckConstraint("end_ts > start_ts"),)

