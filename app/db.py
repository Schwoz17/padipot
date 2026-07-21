from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


def _normalize_database_url(url: str) -> str:
    """
    Render (and some other hosts) hand out Postgres URLs starting with
    'postgres://', which SQLAlchemy 2.x no longer accepts — it requires
    the explicit 'postgresql://' scheme. Normalizing here means pasting
    Render's connection string straight into DATABASE_URL just works,
    no manual edit needed.
    """
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


_database_url = _normalize_database_url(settings.database_url)
connect_args = {"check_same_thread": False} if _database_url.startswith("sqlite") else {}
engine = create_engine(_database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    """Create all tables. Call once at startup (migrations can replace this later)."""
    from app import models  # noqa: F401  (ensures models are registered on Base)
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency: yields a session, always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope():
    """For use outside FastAPI request handlers (scheduler jobs, scripts, tests)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
