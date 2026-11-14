"""Phase 4 tables. They live on their OWN declarative base on purpose: the Phase 1 `Base` has exactly
19 tables (asserted by tests/test_models.py) and the generator manifests iterate Base.metadata.
Created on demand with `ensure_alert_tables(bind)`. No cross-metadata foreign keys (like ground_truth)."""
from datetime import datetime
from typing import Optional
from sqlalchemy import CheckConstraint, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from backend.models.enums import SEVERITY, in_list

OPERATIONAL_TYPES = ("AVAILABILITY_DROP", "PERFORMANCE_DROP", "QUALITY_DROP")


class AlertBase(DeclarativeBase):
    pass


class Alert(AlertBase):
    """OPERATIONAL anomaly (KPI deviates from the machine's own baseline). Deterministic id => idempotent."""
    __tablename__ = "alert"
    alert_id: Mapped[str] = mapped_column(String, primary_key=True)  # ALT-<type>-<machine>-<shift_instance>
    anomaly_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    machine_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    shift_instance_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    order_id: Mapped[Optional[str]] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    kpi: Mapped[str] = mapped_column(String, nullable=False)
    measured_value: Mapped[float]
    baseline_value: Mapped[float]
    baseline_n: Mapped[int]
    deviation_score: Mapped[float]
    kpi_impact: Mapped[str] = mapped_column(Text, nullable=False)       # JSON object
    evidence_ids: Mapped[str] = mapped_column(Text, nullable=False)     # JSON list of source record ids
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False)
    ground_truth_ref: Mapped[Optional[str]] = mapped_column(String)     # comma list of truth ids, set ONLY by the post-hoc evaluator
    detected_at: Mapped[datetime]
    __table_args__ = (CheckConstraint(in_list("severity", SEVERITY)),
                      CheckConstraint(in_list("anomaly_type", OPERATIONAL_TYPES)))


class AnomalyTruthLink(AlertBase):
    """Post-hoc evaluation link between a detector output and a ground-truth label. Rewritten on every evaluation."""
    __tablename__ = "anomaly_truth_link"
    link_id: Mapped[str] = mapped_column(String, primary_key=True)      # LNK-<source_id>-<truth_id>
    source_kind: Mapped[str] = mapped_column(String, nullable=False)    # ANOMALY | ALERT
    source_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    truth_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    match_type: Mapped[str] = mapped_column(String, nullable=False)     # EXACT_TYPE | RELATED
    matched_evidence: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list
    evaluated_at: Mapped[datetime]


def ensure_alert_tables(bind):
    AlertBase.metadata.create_all(bind=bind)
