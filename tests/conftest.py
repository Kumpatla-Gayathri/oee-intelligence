import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.database import Base, _enable_fk
from sqlalchemy import event


@pytest.fixture()
def db():
    """Fresh in-memory database per test (same models, FK enforcement on)."""
    from backend import models  # noqa: F401
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    event.listen(eng, "connect", _enable_fk)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, autoflush=False)()
    yield s
    s.close()
