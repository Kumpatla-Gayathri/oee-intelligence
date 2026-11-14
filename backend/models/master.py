from datetime import date
from typing import Optional
from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.database import Base
from backend.models.enums import ENTITY_STATUS, REASON_CATEGORY, in_list


class Plant(Base):
    __tablename__ = "plant"
    plant_id: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. PLANT-01
    name: Mapped[str] = mapped_column(String, nullable=False)
    timezone: Mapped[str] = mapped_column(String, default="Asia/Kolkata")
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    lines: Mapped[list["Line"]] = relationship(back_populates="plant")
    __table_args__ = (CheckConstraint(in_list("status", ENTITY_STATUS)),)


class Line(Base):
    __tablename__ = "line"
    line_id: Mapped[str] = mapped_column(String, primary_key=True)  # LINE-01
    plant_id: Mapped[str] = mapped_column(ForeignKey("plant.plant_id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    plant: Mapped[Plant] = relationship(back_populates="lines")
    machines: Mapped[list["Machine"]] = relationship(back_populates="line")
    __table_args__ = (CheckConstraint(in_list("status", ENTITY_STATUS)),)


class Machine(Base):
    __tablename__ = "machine"
    machine_id: Mapped[str] = mapped_column(String, primary_key=True)  # M-01-01
    line_id: Mapped[str] = mapped_column(ForeignKey("line.line_id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    machine_type: Mapped[Optional[str]] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    effective_from: Mapped[Optional[date]]
    effective_to: Mapped[Optional[date]]
    line: Mapped[Line] = relationship(back_populates="machines")
    __table_args__ = (CheckConstraint(in_list("status", ENTITY_STATUS)),)


class Product(Base):
    __tablename__ = "product"
    product_id: Mapped[str] = mapped_column(String, primary_key=True)  # PROD-001
    name: Mapped[str] = mapped_column(String, nullable=False)
    unit: Mapped[str] = mapped_column(String, default="pcs")
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    __table_args__ = (CheckConstraint(in_list("status", ENTITY_STATUS)),)


class DowntimeReason(Base):
    __tablename__ = "downtime_reason"
    reason_id: Mapped[str] = mapped_column(String, primary_key=True)  # RSN-001
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    __table_args__ = (CheckConstraint(in_list("category", REASON_CATEGORY)),)
