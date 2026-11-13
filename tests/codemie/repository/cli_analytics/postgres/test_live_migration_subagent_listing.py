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

"""Live-Postgres check of revision a1c1a0000018 (sub-agent listing) through the real migration runner.

The unit tests of the revision patch Alembic out. This one runs the real chain on a throwaway schema: the runner hands
the revision a connection with a transaction already open, which Alembic treats as external, so a revision that
leaves it wrongly (`autocommit_block()`) fails here and nowhere else. Skipped (not failed) when the local analytics
Postgres is unreachable.
"""

from __future__ import annotations

import os
import uuid

import pytest
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from codemie.configs.config import Config
from codemie.repository.cli_analytics.postgres import migrations
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")
_HEAD = "a1c1a0000018"
_PREVIOUS = "a1c1a0000017"
_COPILOT_CHILD = "mig-copilot-child"
_COPILOT_DESKTOP_CHILD = "mig-copilot-desktop-child"
_CLAUDE_CHILD = "mig-claude-child"
_PLAIN = "mig-plain"
_SELF_NAMED = "mig-self-named"


@pytest.fixture
def schema_settings():
    schema = f"mig_subagent_{uuid.uuid4().hex[:8]}"
    settings = AnalyticsPgSettings.from_config(
        Config(PG_URL=_LOCAL_PG_URL, CLI_ANALYTICS_PG_URL=_LOCAL_PG_URL, CLI_ANALYTICS_PG_SCHEMA=schema)
    )
    engine = create_engine(settings.sqlalchemy_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError as exc:
        engine.dispose()
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
    yield settings, engine
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _upgrade_up_to(settings: AnalyticsPgSettings, last: str) -> None:
    alembic_cfg = AlembicConfig()
    alembic_cfg.set_main_option("script_location", str(migrations.ALEMBIC_DIR))
    alembic_cfg.attributes["schema"] = settings.schema
    engine = create_engine(settings.sqlalchemy_url)
    try:
        for revision in migrations._revisions_oldest_first(alembic_cfg):
            migrations._upgrade_to(engine, settings, alembic_cfg, revision)
            if revision == last:
                break
    finally:
        engine.dispose()


def _version(connection, schema: str) -> str:
    return connection.execute(text(f'SELECT version_num FROM "{schema}".cli_analytics_alembic_version')).scalar()


def _seed(connection, schema: str) -> None:
    connection.execute(text(f'SET search_path TO "{schema}"'))
    # The chain creates hook_events partitioned and empty (the maintenance job adds partitions); a DEFAULT partition
    # takes the start events, and makes the revision build its index per partition, as on a live database.
    connection.execute(text("CREATE TABLE IF NOT EXISTS hook_events_default PARTITION OF hook_events DEFAULT"))
    for session_id in (_COPILOT_CHILD, _COPILOT_DESKTOP_CHILD, _CLAUDE_CHILD, _PLAIN, _SELF_NAMED):
        connection.execute(
            text("INSERT INTO session_dims (session_id, started_at) VALUES (:s, now())"), {"s": session_id}
        )
    starts = [
        ("parent-1", _COPILOT_CHILD, '{"platform": "copilot-cli"}'),
        ("parent-2", _COPILOT_DESKTOP_CHILD, '{"platform": "copilot-desktop"}'),
        ("parent-3", _CLAUDE_CHILD, '{"platform": "claude-code"}'),
        (_SELF_NAMED, _SELF_NAMED, '{"platform": "copilot-cli"}'),
    ]
    for parent, child, attrs in starts:
        connection.execute(
            text(
                "INSERT INTO hook_events (ts, session_id, event_type, agent_id, attrs) "
                "VALUES (now(), :p, 'agent.subagent.start', :c, CAST(:a AS jsonb))"
            ),
            {"p": parent, "c": child, "a": attrs},
        )


def test_the_real_chain_reaches_the_head_flags_the_copilot_sub_agents_and_builds_the_index(schema_settings) -> None:
    settings, engine = schema_settings
    schema = settings.schema
    _upgrade_up_to(settings, _PREVIOUS)
    with engine.begin() as connection:
        assert _version(connection, schema) == _PREVIOUS
        _seed(connection, schema)

    migrations.run_migrations(settings)

    with engine.connect() as connection:
        connection.execute(text(f'SET search_path TO "{schema}"'))
        assert _version(connection, schema) == _HEAD
        flagged = {
            row.session_id: row.is_subagent
            for row in connection.execute(text("SELECT session_id, is_subagent FROM session_dims"))
        }
        # the backfill applies the one rule of the start event: a Copilot client, another session
        assert flagged[_COPILOT_CHILD] is True and flagged[_COPILOT_DESKTOP_CHILD] is True
        assert flagged[_CLAUDE_CHILD] is None and flagged[_PLAIN] is None and flagged[_SELF_NAMED] is None
        parent_index_valid = connection.execute(
            text(
                "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = 'hook_events_subagent_start' AND c.relnamespace = to_regnamespace(:s)"
            ),
            {"s": schema},
        ).scalar()
        assert parent_index_valid is True
        unindexed = connection.execute(
            text(
                "SELECT count(*) FROM pg_partition_tree(to_regclass('hook_events')) t WHERE t.isleaf AND NOT EXISTS ("
                "SELECT 1 FROM pg_index i JOIN pg_inherits h ON h.inhrelid = i.indexrelid "
                "WHERE i.indrelid = t.relid AND h.inhparent = to_regclass('hook_events_subagent_start'))"
            )
        ).scalar()
        assert unindexed == 0


def test_the_revision_runs_again_after_a_partial_attempt(schema_settings) -> None:
    """The column is committed before the slow steps, so a retry meets it (and the index) already there."""
    settings, engine = schema_settings
    schema = settings.schema
    migrations.run_migrations(settings)
    with engine.begin() as connection:
        connection.execute(
            text(f'UPDATE "{schema}".cli_analytics_alembic_version SET version_num = :v'), {"v": _PREVIOUS}
        )

    migrations.run_migrations(settings)

    with engine.connect() as connection:
        assert _version(connection, schema) == _HEAD
    migrations.run_migrations(settings)  # and a run at the head is a no-op
