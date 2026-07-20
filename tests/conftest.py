import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app import models  # noqa: F401 — registers all tables on Base.metadata


@pytest.fixture()
def db_session():
    """Fresh in-memory SQLite DB per test — fast, fully isolated, no shared state."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def make_member(db_session):
    from app.models import Member

    def _make(phone: str, name: str) -> Member:
        member = Member(phone=phone, name=name)
        db_session.add(member)
        db_session.commit()
        return member

    return _make


@pytest.fixture()
def make_pot(db_session):
    from app.engine.pot_service import create_pot
    from app.models import Language

    def _make(admin_id: int, name="Test Pot", size=4, amount=10000.0):
        return create_pot(db_session, name=name, admin_id=admin_id, size=size, amount=amount, language=Language.EN)

    return _make
