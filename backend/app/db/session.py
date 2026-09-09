"""Engine/session setup: SQLite for local development, Postgres in deployment."""
import os
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
    # One long-lived process on one VM, not a fleet of short-lived Cloud Run
    # instances, so the pool can be sized for this process's own concurrency
    # instead of being kept tiny to avoid starving its neighbours. Override with
    # DB_POOL_SIZE if the box gets bigger.
    #
    # pool_recycle is 1800, not the 300 that suited an instance measured in
    # minutes: churning every connection every five minutes on a 24/7 process
    # buys nothing but handshake latency, and pool_pre_ping already catches the
    # connections the pooler drops out from under us.
    #
    # prepare_threshold=None stays. Session-mode pooling would technically
    # permit psycopg3's server-side prepared statements, but there is no win
    # here: upsert.py emits a distinct SQL string per chunk width, so the
    # ingester would just thrash psycopg's 100-statement cache. It is also free
    # insurance against a :6543 transaction-pooler URL landing in the env file
    # by mistake, which otherwise surfaces as intermittent "prepared statement
    # already exists" errors once a statement has run five times.
    _pool_size = int(os.environ.get("DB_POOL_SIZE", "5"))
    engine = create_engine(
        DATABASE_URL,
        pool_size=_pool_size,
        max_overflow=_pool_size,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={"prepare_threshold": None},
    )


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """Create tables directly. Local/SQLite development only -- deployments
    use Alembic (see backend/alembic/), which is the schema authority there.

    The IS_SQLITE guard enforces what that docstring always claimed: app.main
    calls this unconditionally at startup, and against the transaction pooler
    the DDL was effectively inert. On the session pooler the VM uses it would
    actually run, and create_all() happily creates a table out from under
    Alembic -- leaving a schema Alembic then believes it has yet to build.
    """
    if not IS_SQLITE:
        return
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
