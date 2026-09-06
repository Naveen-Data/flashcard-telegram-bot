"""PostgreSQL connection helpers; Alembic owns schema changes."""
import os
from contextlib import contextmanager
from typing import Iterator
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine, URL
from sqlalchemy.pool import QueuePool

_engine: Engine | None = None

def build_database_url() -> URL:
    """Build the connection URL from PGHOST/PGUSER/PGPASSWORD/PGDATABASE/etc."""
    missing = [k for k in ("PGHOST", "PGUSER", "PGPASSWORD", "PGDATABASE") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
    return URL.create(
        "postgresql+psycopg",
        username=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        host=os.environ["PGHOST"],
        port=int(os.environ.get("PGPORT", "5432")),
        database=os.environ["PGDATABASE"],
        query={"sslmode": os.environ.get("PGSSLMODE", "require")},
    )

def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(build_database_url(), poolclass=QueuePool, pool_size=3,
                                max_overflow=2, pool_pre_ping=True, pool_recycle=1800)
    return _engine

@contextmanager
def get_connection() -> Iterator[Connection]:
    with get_engine().begin() as connection:
        yield connection

def init_db() -> None:
    """Compatibility hook. Deployments must run ``alembic upgrade head`` first."""
    with get_engine().connect():
        pass
