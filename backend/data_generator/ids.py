"""Deterministic, zero-padded ID builders. IDs are immutable once generated."""
import re
from datetime import date

from backend.data_generator.config import YEAR


def plant_id(n: int = 1) -> str:
    return f"PLANT-{n:02d}"


def line_id(n: int) -> str:
    return f"LINE-{n:02d}"


def machine_id(line_no: int, machine_no: int) -> str:
    return f"M-{line_no:02d}-{machine_no:02d}"


def product_id(n: int) -> str:
    return f"PROD-{n:03d}"


def shift_def_id(code: str) -> str:
    return f"SHIFTDEF-{code}"


def break_template_id(code: str, n: int) -> str:
    return f"SBT-{code}-{n}"


def shift_instance_id(d: date, code: str) -> str:
    return f"SHIFT-{d.isoformat()}-{code}"


def planned_break_id(shift_instance: str, n: int) -> str:
    return f"PBRK-{shift_instance}-{n}"


def config_id(machine: str, product: str, n: int = 1) -> str:
    return f"CFG-{machine}-{product}-{n}"


def order_id(n: int) -> str:
    return f"PO-{YEAR}-{n:06d}"


def event_id(machine: str, n: int) -> str:
    return f"EVT-{machine}-{n:06d}"


def count_id(order: str, n: int) -> str:
    return f"CNT-{order}-{n:06d}"


def correction_id(count: str, n: int = 1) -> str:
    return f"COR-{count}-{n}"


def qc_id(n: int) -> str:
    return f"QC-{YEAR}-{n:06d}"


def truth_id(n: int) -> str:
    return f"GT-{n:06d}"


# Regexes used by tests and validation to confirm every ID follows its convention.
PATTERNS = {
    "production_order": re.compile(r"^PO-2026-\d{6}$"),
    "shift_instance": re.compile(r"^SHIFT-\d{4}-\d{2}-\d{2}-[ABC]$"),
    "state_event": re.compile(r"^EVT-M-\d{2}-\d{2}-\d{6}$"),
    "production_count": re.compile(r"^CNT-PO-2026-\d{6}-\d{6}$"),
    "quality_inspection": re.compile(r"^QC-2026-\d{6}$"),
}
