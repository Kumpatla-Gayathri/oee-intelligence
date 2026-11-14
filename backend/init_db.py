"""Create all tables. Run:  python -m backend.init_db"""
from backend.database import init_db, engine, DATABASE_URL

if __name__ == "__main__":
    init_db()
    from sqlalchemy import inspect
    names = sorted(inspect(engine).get_table_names())
    print("DB:", DATABASE_URL)
    print(f"{len(names)} tables:", ", ".join(names))
