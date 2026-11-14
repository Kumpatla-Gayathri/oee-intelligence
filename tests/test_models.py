from datetime import datetime
import pytest
from sqlalchemy.exc import IntegrityError
from backend.models import (Plant, Line, Machine, Product, ProductionOrder,
                            StateEvent, QualityInspection, ShiftInstance, Shift)

T0 = datetime(2026, 1, 1, 6, 0)
T1 = datetime(2026, 1, 1, 7, 0)


def seed_basic(db):
    db.add_all([Plant(plant_id="PLANT-01", name="P1"),
                Line(line_id="LINE-01", plant_id="PLANT-01", name="L1"),
                Machine(machine_id="M-01-01", line_id="LINE-01", name="M1"),
                Product(product_id="PROD-001", name="Widget")])
    db.flush()  # parents must be written before children (no ORM relationship links these tables)
    db.add(ProductionOrder(order_id="PO-2026-000001", machine_id="M-01-01", product_id="PROD-001",
                           planned_qty=100, planned_start=T0, planned_end=T1))
    db.commit()


def test_all_tables_created(db):
    from backend.database import Base
    assert len(Base.metadata.tables) == 19


def test_foreign_key_enforced(db):
    db.add(Line(line_id="LINE-X", plant_id="NOPE", name="bad"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_invalid_state_rejected(db):
    seed_basic(db)
    db.add(StateEvent(event_id="EVT-M-01-01-000001", machine_id="M-01-01", state="FLYING", start_ts=T0, end_ts=T1))
    with pytest.raises(IntegrityError):
        db.commit()


def test_end_before_start_rejected(db):
    seed_basic(db)
    db.add(StateEvent(event_id="EVT-M-01-01-000001", machine_id="M-01-01", state="RUN", start_ts=T1, end_ts=T0))
    with pytest.raises(IntegrityError):
        db.commit()


def test_scrap_exceeding_total_is_storable_for_detector(db):
    seed_basic(db)
    db.add(QualityInspection(inspection_id="QC-2026-000001", order_id="PO-2026-000001", machine_id="M-01-01",
                             ts=T0, inspected_qty=10, good_qty=0, scrap_qty=50, rework_qty=0))
    db.commit()  # must NOT raise: anomaly 3 has to be insertable


def test_duplicate_primary_key_rejected(db):
    seed_basic(db)
    db.add(Product(product_id="PROD-001", name="dup"))
    with pytest.raises(IntegrityError):
        db.commit()
