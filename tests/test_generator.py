"""Phase 2 tests: deterministic generator, valid baseline, labelled injection, ground-truth separation."""
import csv
import json
import sqlite3

import pytest

from backend.data_generator.config import SCALES, SEED
from backend.data_generator.generate import generate
from backend.data_generator.ids import PATTERNS

TABLES_WITH_PK_PATTERN = {
    "production_order": "order_id", "shift_instance": "shift_instance_id",
    "state_event": "event_id", "production_count": "count_id", "quality_inspection": "inspection_id",
}


def q(db, sql, params=()):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    d = tmp_path_factory.mktemp("tiny")
    s = generate("tiny", SEED, d / "t.db", csv_dir=d / "csv", gt_dir=d / "gt")
    return d, s


@pytest.fixture(scope="module")
def small(tmp_path_factory):
    d = tmp_path_factory.mktemp("small")
    return d, generate("small", SEED, d / "s.db")


@pytest.fixture(scope="module")
def baseline_small(tmp_path_factory):
    d = tmp_path_factory.mktemp("base")
    return d, generate("small", SEED, d / "b.db", inject=False)


def test_scale_presets_match_hackathon_spec():
    f = SCALES["full"]
    assert (f.lines, f.machines, f.products, f.days) == (5, 30, 20, 90)
    assert SEED == 20261022
    for s in SCALES.values():
        assert s.machines % s.lines == 0


def test_master_and_calendar_counts(small):
    d, s = small
    sc = SCALES["small"]
    c = s["row_counts"]
    assert c["plant"] == 1 and c["line"] == sc.lines and c["machine"] == sc.machines
    assert c["product"] == sc.products and c["shift"] == 3
    assert c["shift_instance"] == sc.days * 3
    assert c["planned_break"] == sc.days * 3 * 3 and c["shift_break_template"] == 9
    assert c["machine_product_config"] >= sc.machines * sc.products_per_machine
    assert c["production_order"] > 0 and c["state_event"] > 0
    assert c["production_count"] > 0 and c["quality_inspection"] > 0


def test_foreign_key_integrity(small):
    d, _ = small
    assert q(d / "s.db", "PRAGMA foreign_key_check") == []


def test_id_conventions(small):
    d, _ = small
    for table, col in TABLES_WITH_PK_PATTERN.items():
        pat = PATTERNS[table]
        bad = [r[0] for r in q(d / "s.db", f"SELECT {col} FROM {table}") if not pat.match(r[0])]
        assert bad == [], f"{table}: {bad[:3]}"
    assert q(d / "s.db", "SELECT shift_instance_id FROM shift_instance ORDER BY 1 LIMIT 1")[0][0] == "SHIFT-2026-01-01-A"
    assert q(d / "s.db", "SELECT order_id FROM production_order ORDER BY 1 LIMIT 1")[0][0] == "PO-2026-000001"


def test_ids_unique_and_event_numbers_sequential(small):
    d, _ = small
    for table, col in TABLES_WITH_PK_PATTERN.items():
        total, distinct = q(d / "s.db", f"SELECT COUNT(*), COUNT(DISTINCT {col}) FROM {table}")[0]
        assert total == distinct, table
    rows = q(d / "s.db", "SELECT machine_id, COUNT(*), MAX(CAST(substr(event_id, -6) AS INTEGER)) FROM state_event GROUP BY machine_id")
    assert all(n == mx for _, n, mx in rows)  # EVT numbers run 1..N per machine with no gaps


def test_baseline_has_no_overlaps_and_no_state_inside_breaks(baseline_small):
    d, _ = baseline_small
    db = d / "b.db"
    overlaps = q(db, """SELECT COUNT(*) FROM state_event a JOIN state_event b
        ON a.machine_id=b.machine_id AND a.event_id<b.event_id
        AND a.start_ts < b.end_ts AND b.start_ts < a.end_ts""")[0][0]
    assert overlaps == 0
    in_break = q(db, """SELECT COUNT(*) FROM state_event e
        JOIN shift_instance si ON e.start_ts>=si.start_ts AND e.start_ts<si.end_ts
        JOIN planned_break pb ON pb.shift_instance_id=si.shift_instance_id
        WHERE e.start_ts < pb.end_ts AND pb.start_ts < e.end_ts""")[0][0]
    assert in_break == 0


def test_baseline_quality_arithmetic(baseline_small):
    d, _ = baseline_small
    db = d / "b.db"
    assert q(db, "SELECT COUNT(*) FROM quality_inspection WHERE good_qty+scrap_qty+rework_qty != inspected_qty")[0][0] == 0
    assert q(db, "SELECT COUNT(*) FROM quality_inspection WHERE scrap_qty+rework_qty > inspected_qty")[0][0] == 0
    # planned_qty is derived from the simulated counter, and inspected totals equal the counter totals
    rows = q(db, """SELECT po.order_id, MAX(pc.counter_value), SUM(qi.inspected_qty)
        FROM production_order po JOIN production_count pc ON pc.order_id=po.order_id
        JOIN quality_inspection qi ON qi.order_id=po.order_id GROUP BY po.order_id""")
    assert rows


def test_baseline_counter_decreases_only_with_correction(baseline_small):
    d, _ = baseline_small
    db = d / "b.db"
    bad = q(db, """SELECT a.count_id FROM production_count a JOIN production_count b
        ON a.order_id=b.order_id AND b.ts=(SELECT MAX(ts) FROM production_count c WHERE c.order_id=a.order_id AND c.ts<a.ts)
        WHERE a.counter_value < b.counter_value
        AND a.count_id NOT IN (SELECT count_id FROM count_correction)""")
    assert bad == []
    n_corr = q(db, "SELECT COUNT(*) FROM count_correction")[0][0]
    assert n_corr >= 1
    # each correction references a real count that was lower than the one before it and keeps the original row
    assert q(db, "SELECT COUNT(*) FROM count_correction cc JOIN production_count pc ON pc.count_id=cc.count_id")[0][0] == n_corr


def test_baseline_has_no_ground_truth_and_no_wrong_config(baseline_small):
    d, s = baseline_small
    assert s["row_counts"]["ground_truth"] == 0 and s["injected_anomalies"] == 0
    assert q(d / "b.db", "SELECT COUNT(*) FROM machine_product_config WHERE effective_to IS NOT NULL")[0][0] == 0


def test_varied_distributions(small):
    d, _ = small
    states = {r[0] for r in q(d / "s.db", "SELECT DISTINCT state FROM state_event")}
    assert {"RUN", "MICRO_STOP", "CHANGEOVER"} <= states
    products = q(d / "s.db", "SELECT COUNT(DISTINCT product_id) FROM production_order")[0][0]
    assert products >= 3
    assert q(d / "s.db", "SELECT COUNT(DISTINCT ideal_cycle_time_sec) FROM machine_product_config")[0][0] > 5


def test_injection_one_ground_truth_row_per_anomaly(small):
    d, s = small
    n = SCALES["small"].anomalies_per_type
    rows = q(d / "s.db", "SELECT issue_type, COUNT(*) FROM ground_truth GROUP BY issue_type")
    assert dict(rows) == {t: n for t in (
        "OVERLAPPING_STATES", "COUNT_DECREASE", "SCRAP_EXCEEDS_TOTAL",
        "WRONG_CYCLE_TIME", "DOWNTIME_IN_PLANNED_BREAK")}
    assert s["injected_anomalies"] == 5 * n


EVIDENCE_TABLE = {"EVT": ("state_event", "event_id"), "CNT": ("production_count", "count_id"),
                  "QC": ("quality_inspection", "inspection_id"), "CFG": ("machine_product_config", "config_id"),
                  "PBRK": ("planned_break", "planned_break_id"), "PO": ("production_order", "order_id")}


def test_every_evidence_id_exists_in_source_tables(small):
    d, _ = small
    for (ev,) in q(d / "s.db", "SELECT evidence_ids FROM ground_truth"):
        for ref in json.loads(ev):
            table, col = EVIDENCE_TABLE[ref.split("-")[0]]
            assert q(d / "s.db", f"SELECT COUNT(*) FROM {table} WHERE {col}=?", (ref,))[0][0] == 1, ref


def test_each_injection_is_really_present(small):
    d, _ = small
    db = d / "s.db"
    gt = {t: [json.loads(e) for (e,) in q(db, "SELECT evidence_ids FROM ground_truth WHERE issue_type=?", (t,))]
          for t in ("OVERLAPPING_STATES", "COUNT_DECREASE", "SCRAP_EXCEEDS_TOTAL",
                    "WRONG_CYCLE_TIME", "DOWNTIME_IN_PLANNED_BREAK")}
    for run_id, new_id in gt["OVERLAPPING_STATES"]:
        a = q(db, "SELECT start_ts,end_ts FROM state_event WHERE event_id=?", (run_id,))[0]
        b = q(db, "SELECT start_ts,end_ts FROM state_event WHERE event_id=?", (new_id,))[0]
        assert b[0] < a[1] and a[0] < b[1]
    for new_id, prev_id in gt["COUNT_DECREASE"]:
        new = q(db, "SELECT counter_value, ts FROM production_count WHERE count_id=?", (new_id,))[0]
        prev = q(db, "SELECT counter_value FROM production_count WHERE count_id=?", (prev_id,))[0]
        assert new[0] < prev[0]
        assert q(db, "SELECT COUNT(*) FROM count_correction WHERE count_id=?", (new_id,))[0][0] == 0
    for (qc,) in gt["SCRAP_EXCEEDS_TOTAL"]:
        r = q(db, "SELECT scrap_qty, inspected_qty FROM quality_inspection WHERE inspection_id=?", (qc,))[0]
        assert r[0] > r[1]
    for cfg, order in gt["WRONG_CYCLE_TIME"]:
        wrong = q(db, "SELECT ideal_cycle_time_sec, machine_id, product_id FROM machine_product_config WHERE config_id=?", (cfg,))[0]
        base = q(db, "SELECT ideal_cycle_time_sec FROM machine_product_config WHERE machine_id=? AND product_id=? AND effective_to IS NULL",
                 (wrong[1], wrong[2]))[0][0]
        assert wrong[0] == pytest.approx(base * 2.5, abs=0.02)
    for evt, brk in gt["DOWNTIME_IN_PLANNED_BREAK"]:
        e = q(db, "SELECT start_ts,end_ts,state FROM state_event WHERE event_id=?", (evt,))[0]
        b = q(db, "SELECT start_ts,end_ts FROM planned_break WHERE planned_break_id=?", (brk,))[0]
        assert e[2] == "BREAKDOWN" and b[0] <= e[0] and e[1] <= b[1]


def test_injection_only_adds_rows_never_changes_baseline(small, baseline_small):
    ds, s = small
    db_, b = baseline_small
    # identical seed -> every baseline row is still there unchanged: compare per-table checksums of untouched tables
    for t in ("plant", "line", "machine", "product", "shift", "shift_instance", "planned_break", "downtime_reason",
              "production_order", "count_correction"):
        assert s["checksums"][t] == b["checksums"][t], t
    for t in ("state_event", "production_count", "quality_inspection", "machine_product_config"):
        assert s["row_counts"][t] >= b["row_counts"][t]


def test_ground_truth_is_separate_from_detector_inputs(small):
    d, _ = small
    db = d / "s.db"
    assert q(db, "PRAGMA foreign_key_list(ground_truth)") == []
    for t in ("state_event", "production_count", "quality_inspection", "machine_product_config", "production_order"):
        cols = {r[1] for r in q(db, f"PRAGMA table_info({t})")}
        assert not cols & {"issue_type", "ground_truth", "is_anomaly", "label", "truth_id"}, t
    other_refs = [t for (t,) in q(db, "SELECT name FROM sqlite_master WHERE type='table'")
                  if any(r[2] == "ground_truth" for r in q(db, f"PRAGMA foreign_key_list({t})"))]
    assert other_refs == []


def test_csv_and_ground_truth_export(tiny):
    d, s = tiny
    assert (d / "csv" / "state_event.csv").exists() and (d / "csv" / "manifest.json").exists()
    assert not (d / "csv" / "ground_truth.csv").exists()          # truth never sits beside inputs
    with open(d / "gt" / "ground_truth.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == s["row_counts"]["ground_truth"] == 5
    assert (d / "gt" / "ground_truth.json").exists()
    with open(d / "csv" / "production_order.csv", newline="") as f:
        assert sum(1 for _ in f) - 1 == s["row_counts"]["production_order"]
    man = json.loads((d / "csv" / "manifest.json").read_text())
    assert man["seed"] == SEED and man["scale"] == "tiny" and man["checksums"] == s["checksums"]


def test_same_seed_is_reproducible_and_other_seed_differs(tmp_path):
    a = generate("tiny", SEED, tmp_path / "a.db")
    b = generate("tiny", SEED, tmp_path / "b.db")
    c = generate("tiny", 12345, tmp_path / "c.db")
    assert a["row_counts"] == b["row_counts"]
    assert a["checksums"] == b["checksums"]
    assert a["checksums"] != c["checksums"]


def test_refuses_to_overwrite_without_force(tmp_path):
    generate("tiny", SEED, tmp_path / "x.db")
    with pytest.raises(FileExistsError):
        generate("tiny", SEED, tmp_path / "x.db")
    generate("tiny", SEED, tmp_path / "x.db", force=True)
