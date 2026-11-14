"""Investigation workflow tables. Own declarative base (Phase 1 Base must keep exactly 19 tables).
No cross-metadata foreign keys: alert_id / machine_id / shift_instance_id are stored as plain traceability references."""
from datetime import datetime
from typing import Optional
from sqlalchemy import CheckConstraint, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from backend.models.enums import SEVERITY, in_list

INVESTIGATION_STATUSES = ("OPEN", "IN_PROGRESS", "RESOLVED")


class InvestigationBase(DeclarativeBase):
    pass


class Investigation(InvestigationBase):
    __tablename__ = "investigation"
    investigation_id: Mapped[str] = mapped_column(String, primary_key=True)   # INV-000001
    alert_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    machine_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    shift_instance_id: Mapped[str] = mapped_column(String, nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String, nullable=False)         # AGG-<machine>-<shift_instance>
    order_id: Mapped[Optional[str]] = mapped_column(String)
    anomaly_type: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="OPEN", index=True)
    owner: Mapped[Optional[str]] = mapped_column(String)
    evidence_ids: Mapped[str] = mapped_column(Text, nullable=False)          # JSON list, snapshot of alert evidence + additions
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    resolved_at: Mapped[Optional[datetime]]
    __table_args__ = (CheckConstraint(in_list("status", INVESTIGATION_STATUSES)),
                      CheckConstraint(in_list("severity", SEVERITY)))


class InvestigationNote(InvestigationBase):
    """Append-only history: notes, status/owner changes, evidence additions."""
    __tablename__ = "investigation_note"
    note_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    investigation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)   # CREATED | NOTE | STATUS_CHANGE | OWNER_CHANGE | EVIDENCE_ADDED
    author: Mapped[Optional[str]] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime]


def ensure_investigation_tables(bind):
    InvestigationBase.metadata.create_all(bind=bind)
