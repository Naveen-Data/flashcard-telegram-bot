import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alembic import context
from sqlalchemy import create_engine, pool
from studybot.db.connection import build_database_url

config = context.config
url = build_database_url()
target_metadata = None
def run_migrations_offline():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle":"named"})
    with context.begin_transaction(): context.run_migrations()
def run_migrations_online():
    # Built directly from the URL object rather than round-tripped through
    # alembic.ini's ConfigParser, since a password can contain a literal '%'
    # (e.g. a URL-encoded '%3D') that ConfigParser misparses as interpolation.
    connectable = create_engine(url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction(): context.run_migrations()
if context.is_offline_mode(): run_migrations_offline()
else: run_migrations_online()
