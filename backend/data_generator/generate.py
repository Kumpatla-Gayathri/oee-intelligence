"""Command line entry point for the synthetic data generator.

Examples (PowerShell, from the project root):
    python -m backend.data_generator.generate --scale tiny  --out data/tiny.db  --force
    python -m backend.data_generator.generate --scale small --out data/oee.db   --force
    python -m backend.data_generator.generate --scale full  --out data/full.db  --force

The same code runs for every scale; only config.SCALES differs.
"""
import argparse
import csv
import hashlib
import json
import random
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from backend.database import Base, ROOT, _enable_fk
from backend.data_generator.baseline import Buffer, generate_baseline
from backend.data_generator.config import SEED, get_scale
from backend.data_generator.inject import inject_all


def make_engine(db_path: Path):
    engine = create_engine(f"sqlite:///{Path(db_path).as_posix()}", connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _enable_fk)

    @event.listens_for(engine, "connect")
    def _fast(dbapi_conn, _):  # bulk-load speed; the DB is rebuilt from scratch every run
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=MEMORY")
        cur.execute("PRAGMA synchronous=OFF")
        cur.close()

    return engine


def row_counts(engine) -> dict:
    with engine.connect() as conn:
        return {t.name: conn.execute(select(func.count()).select_from(t)).scalar_one()
                for t in Base.metadata.sorted_tables}


def compute_checksums(engine) -> dict:
    """sha256 over every row of every table, ordered by primary key (reproducibility proof)."""
    out = {}
    with engine.connect() as conn:
        for t in Base.metadata.sorted_tables:
            h, n = hashlib.sha256(), 0
            for row in conn.execute(select(t).order_by(*t.primary_key.columns)):
                h.update(repr(tuple(row)).encode("utf-8"))
                n += 1
            out[t.name] = {"rows": n, "sha256": h.hexdigest()}
    return out


def export_csv(engine, csv_dir: Path, gt_dir: Path):
    """Source tables -> csv_dir. ground_truth -> gt_dir (kept apart from detector inputs)."""
    csv_dir, gt_dir = Path(csv_dir), Path(gt_dir)
    csv_dir.mkdir(parents=True, exist_ok=True)
    gt_dir.mkdir(parents=True, exist_ok=True)
    with engine.connect() as conn:
        for t in Base.metadata.sorted_tables:
            if t.name in ("anomaly", "oee_aggregate", "oee_aggregate_detail"):
                continue  # derived tables are produced by the engine/detectors, not the generator
            target = (gt_dir / "ground_truth.csv") if t.name == "ground_truth" else (csv_dir / f"{t.name}.csv")
            with open(target, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow([c.name for c in t.columns])
                for row in conn.execute(select(t).order_by(*t.primary_key.columns)):
                    w.writerow(row)
        gt = t = Base.metadata.tables["ground_truth"]
        rows = [dict(r._mapping) for r in conn.execute(select(gt).order_by(gt.c.truth_id))]
    with open(gt_dir / "ground_truth.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, default=str)


def generate(scale="tiny", seed: int = SEED, out_db=None, force: bool = False, inject: bool = True,
             csv_dir=None, gt_dir=None) -> dict:
    """Build a database. Returns a summary dict (row counts, checksums, timing)."""
    scale = get_scale(scale)
    out_db = Path(out_db) if out_db else ROOT / "data" / "oee.db"
    if out_db.exists():
        if not force:
            raise FileExistsError(f"{out_db} already exists. Use --force to rebuild it.")
        out_db.unlink()
    out_db.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    engine = make_engine(out_db)
    from backend import models  # noqa: F401
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        buf = Buffer(session, scale.batch_size)
        state = generate_baseline(scale, seed, buf)
        injected = 0
        if inject:
            injected = inject_all(session, buf, state, scale, random.Random(f"{seed}:inject"))
        buf.flush()
    counts = row_counts(engine)
    checks = compute_checksums(engine)
    if csv_dir:
        export_csv(engine, csv_dir, gt_dir or (ROOT / "data" / "ground_truth"))
        manifest = {
            "generator": "backend.data_generator.generate", "seed": seed, "scale": scale.name,
            "command": f"python -m backend.data_generator.generate --scale {scale.name} --seed {seed}",
            "row_counts": counts, "checksums": checks, "injected_anomalies": injected,
        }
        with open(Path(csv_dir) / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
    engine.dispose()
    return {"scale": scale.name, "seed": seed, "db": str(out_db), "row_counts": counts,
            "checksums": checks, "injected_anomalies": injected, "seconds": round(time.time() - t0, 1)}


def main(argv=None):
    p = argparse.ArgumentParser(description="Generate deterministic synthetic OEE data.")
    p.add_argument("--scale", default="tiny", choices=["tiny", "small", "full"])
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--out", default=str(ROOT / "data" / "oee.db"), help="SQLite file to create")
    p.add_argument("--force", action="store_true", help="overwrite --out if it exists")
    p.add_argument("--no-inject", action="store_true", help="baseline only, no anomalies")
    p.add_argument("--no-csv", action="store_true", help="skip CSV/JSON export")
    p.add_argument("--csv-dir", default=None, help="default: data/generated/<scale>")
    p.add_argument("--gt-dir", default=str(ROOT / "data" / "ground_truth"))
    a = p.parse_args(argv)
    csv_dir = None if a.no_csv else (a.csv_dir or str(ROOT / "data" / "generated" / a.scale))
    try:
        s = generate(a.scale, a.seed, a.out, a.force, not a.no_inject, csv_dir, a.gt_dir)
    except FileExistsError as e:
        print(f"ERROR: {e}")
        return 1
    print(f"scale={s['scale']} seed={s['seed']} db={s['db']} time={s['seconds']}s "
          f"injected_anomalies={s['injected_anomalies']}")
    for t, n in s["row_counts"].items():
        print(f"  {t:<24}{n:>10,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
