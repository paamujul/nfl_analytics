"""Engine/session setup: SQLite for local development, Postgres in deployment."""
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import DATABASE_URL
from app.db.models import Base

IS_SQLITE = DATABASE_URL.startswith("sqlite")

if IS_SQLITE:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _):
        # WAL lets the ingestion writer and API readers coexist without lock errors.
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()
else:
    # Cloud Run runs many short-lived instances against Supabase's shared
    # transaction pooler, so keep each instance's pool small and validate
    # connections on checkout. prepare_threshold=None turns off psycopg3's
    # server-side prepared statements, which transaction-mode pooling cannot
    # support -- without it you get intermittent "prepared statement already
    # exists" errors once a statement has run five times.
    engine = create_engine(
        DATABASE_URL,
        pool_size=2,
        max_overflow=3,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"prepare_threshold": None},
    )


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """Create tables directly. Local/SQLite development only -- deployments
    use Alembic (see backend/alembic/), which is the schema authority there."""
    Base.metadata.create_all(engine)


@contextmanager
def db_session() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db():
    """FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
