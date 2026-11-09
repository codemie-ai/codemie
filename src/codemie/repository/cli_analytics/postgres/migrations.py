# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Schema migrations of the PostgreSQL analytics storage (src/external/alembic_cli_analytics).

A separate Alembic environment from the application's, because the analytics tables can
live in another database. Several pods may start at once: a session-level advisory lock lets one of them migrate
while the others wait and then find nothing to do.

Each revision runs on its own connection and transaction. A later revision that fails (a lock timeout of an index
build, say) rolls back only itself: the revisions before it stay applied, and the next attempt continues from there.
A revision may also end its own transaction part-way (`op.get_bind().commit()`), to keep a small safe step (the
nullable columns the readers need) when a slow step after it can time out: that is why the connection is the
runner's own (`engine.connect()`, committed at the end) and the lock is session-level, so it outlives that commit.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.pool import NullPool

from codemie.clients.postgres import _register_iam_token_event
from codemie.repository.cli_analytics.postgres.maintenance import quote_ident
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings

ALEMBIC_DIR = Path(__file__).resolve().parents[4] / "external" / "alembic_cli_analytics"
# A database that does not answer fails the attempt in seconds; the next job retries it.
CONNECT_TIMEOUT_S = 10
# How long a pod waits for another pod's migration before giving up until the next job.
LOCK_TIMEOUT = "2min"


def _revisions_oldest_first(alembic_cfg: AlembicConfig) -> list[str]:
    """The revision ids of the migration chain, from the first to the head."""
    return [script.revision for script in reversed(list(ScriptDirectory.from_config(alembic_cfg).walk_revisions()))]


def run_migrations(settings: AnalyticsPgSettings) -> None:
    """Create the analytics schema if needed and upgrade it to the latest revision, one revision and one transaction at
    a time. Blocking."""
    engine = create_engine(
        settings.sqlalchemy_url, poolclass=NullPool, connect_args={"connect_timeout": CONNECT_TIMEOUT_S}
    )
    if settings.iam_auth:
        host, port, user = settings.iam_endpoint
        _register_iam_token_event(engine, host=host, port=port, user=user)
    try:
        alembic_cfg = AlembicConfig()  # no alembic.ini: migrations run only through this function
        alembic_cfg.set_main_option("script_location", str(ALEMBIC_DIR))
        alembic_cfg.attributes["schema"] = settings.schema
        for revision in _revisions_oldest_first(alembic_cfg):
            _upgrade_to(engine, settings, alembic_cfg, revision)
    finally:
        engine.dispose()


def _upgrade_to(engine: Engine, settings: AnalyticsPgSettings, alembic_cfg: AlembicConfig, revision: str) -> None:
    """Upgrade to `revision` in one transaction (a no-op when the database is already at or past it). The advisory
    lock is session-level and released when the connection closes, also after a failure: a revision may commit part-way,
    and a transaction-level lock would end with that commit."""
    with engine.connect() as connection:
        connection.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": settings.lock_key("migrations")})
        # CREATE SCHEMA checks the database's CREATE privilege even when the schema exists:
        # a role given only a schema created for it must not issue it.
        exists = connection.execute(
            text("SELECT 1 FROM pg_namespace WHERE nspname = :schema"), {"schema": settings.schema}
        ).scalar()
        if not exists:
            connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {quote_ident(settings.schema)}"))
        connection.execute(text(f"SET search_path TO {quote_ident(settings.schema)}"))
        alembic_cfg.attributes["connection"] = connection
        command.upgrade(alembic_cfg, revision)
        connection.commit()
