"""Alembic environment.

The database URL comes from :mod:`app.config`, never from ``alembic.ini``, so there is
exactly one place that decides where the database lives. ``render_as_batch`` is on
because SQLite cannot ALTER most things in place -- without it, every future column
change would be an unrunnable migration.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.config import get_settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, and since migrations run at app
    # startup (after logging is configured) the default silently kills every
    # application logger -- no request logs, no error tracebacks, nothing.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

_settings = get_settings()
_settings.ensure_directories()
config.set_main_option("sqlalchemy.url", _settings.database_url)


def include_object(
    _object: object, name: str, type_: str, _reflected: bool, _compare_to: object
) -> bool:
    """Hide hand-written DDL from autogenerate.

    SQLAlchemy's metadata cannot describe an FTS5 virtual table (or its four shadow
    tables), nor a partial unique index. Without this filter every autogenerate run
    proposes dropping the full-text search index and the constraint that stops a copy
    being lent twice -- and one distracted `alembic revision --autogenerate` would take
    them out.
    """
    if type_ == "table" and name.startswith("oracle_text_fts"):
        return False
    return not (type_ == "index" and name == "ix_loans_open")


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        # The PRAGMA opens an implicit transaction. SQLite reports itself as
        # non-transactional-DDL, so alembic's begin_transaction() is a no-op and will
        # not commit for us -- without this the DDL lands but the alembic_version row
        # is rolled back on close, and the *next* upgrade re-runs the same migration
        # and dies on "table already exists".
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
