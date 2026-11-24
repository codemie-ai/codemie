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

"""Live-Postgres check of revision a1c1a0000019 (reset stale zero-lines activity facts) through the real migration
runner, on a schema stopped just before it. The unit tests of the revision patch Alembic out; this one runs the
real statement against real `session_dims`/`lines_daily`/`turns_daily` rows, including the "must not touch" guard
the review asked about (MR review, EPMCDME-16496). Skipped (not failed) when the local analytics Postgres is
unreachable.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import date

import pytest
from alembic.config import Config as AlembicConfig
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import OperationalError

from codemie.configs.config import Config
from codemie.repository.cli_analytics.postgres import migrations
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")
_HEAD = "a1c1a0000022"  # run_migrations reaches the true head, now one revision past this file's own
_PREVIOUS = "a1c1a0000018"
_DAY = date(2026, 1, 15)

# A legacy session whose only activity-table row, ever, is a zero-value lines_daily row: `has_activity` is
# forced to `true` to stand in for what the pre-fix derive left behind (the current code never computes this).
_LEGACY_ZERO_LINES_ONLY = "mig-legacy-zero-lines-only"
# A session with real (nonzero) line changes: the reset's selection must not touch it.
_REAL_LINES = "mig-real-lines"
# A session whose only lines_daily row is zero-value, like the legacy one, but that also has a turns_daily row
# (real activity elsewhere): must not be touched either, exactly like arms 3-6 of the admission rule already treat
# it as active.
_ZERO_LINES_BUT_TURNS = "mig-zero-lines-but-turns"


@pytest.fixture
def schema_settings() -> Iterator[tuple[AnalyticsPgSettings, Engine]]:
    schema = f"mig_reset_zero_lines_{uuid.uuid4().hex[:8]}"
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


def _version(connection: Connection, schema: str) -> str:
    return connection.execute(text(f'SELECT version_num FROM "{schema}".cli_analytics_alembic_version')).scalar()


def _seed(connection: Connection, schema: str) -> None:
    connection.execute(text(f'SET search_path TO "{schema}"'))
    # The chain creates lines_daily/turns_daily partitioned and empty (the maintenance job adds real partitions);
    # a DEFAULT partition takes these rows, as the subagent-listing test does for hook_events.
    connection.execute(text("CREATE TABLE IF NOT EXISTS lines_daily_default PARTITION OF lines_daily DEFAULT"))
    connection.execute(text("CREATE TABLE IF NOT EXISTS turns_daily_default PARTITION OF turns_daily DEFAULT"))
    for session_id in (_LEGACY_ZERO_LINES_ONLY, _REAL_LINES, _ZERO_LINES_BUT_TURNS):
        connection.execute(
            text("INSERT INTO session_dims (session_id, started_at) VALUES (:s, now())"), {"s": session_id}
        )
    zero_lines = (_LEGACY_ZERO_LINES_ONLY, _ZERO_LINES_BUT_TURNS)
    for session_id in zero_lines:
        connection.execute(
            text(
                "INSERT INTO lines_daily (day, session_id, user_email, model_name, lines_added, lines_removed) "
                "VALUES (:d, :s, '', '', 0, 0)"
            ),
            {"d": _DAY, "s": session_id},
        )
    connection.execute(
        text(
            "INSERT INTO lines_daily (day, session_id, user_email, model_name, lines_added, lines_removed) "
            "VALUES (:d, :s, '', '', 12, 3)"
        ),
        {"d": _DAY, "s": _REAL_LINES},
    )
    connection.execute(
        text("INSERT INTO turns_daily (day, session_id, turns) VALUES (:d, :s, 1)"),
        {"d": _DAY, "s": _ZERO_LINES_BUT_TURNS},
    )
    # Stands in for what the pre-fix, value-blind derive left on every session with a lines_daily row: the real
    # (fixed) DERIVE_SQL never produces this for a zero-lines-only session any more, so it must be forced here.
    for session_id in (*zero_lines, _REAL_LINES):
        connection.execute(
            text("UPDATE session_dims SET has_activity = true, last_activity_day = :d WHERE session_id = :s"),
            {"d": _DAY, "s": session_id},
        )


def test_the_real_chain_resets_only_the_zero_lines_only_legacy_session(
    schema_settings: tuple[AnalyticsPgSettings, Engine],
) -> None:
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
        facts = {
            row.session_id: (row.has_activity, row.last_activity_day)
            for row in connection.execute(text("SELECT session_id, has_activity, last_activity_day FROM session_dims"))
        }
        # the only session with no other activity-table row and only a zero-value lines_daily row is reset to NULL,
        # ready for the maintenance backfill to re-derive it under the corrected (value-aware) rule
        assert facts[_LEGACY_ZERO_LINES_ONLY] == (None, None)
        # real lines changes, and a zero-lines session with real activity elsewhere, are left exactly as found
        assert facts[_REAL_LINES] == (True, _DAY)
        assert facts[_ZERO_LINES_BUT_TURNS] == (True, _DAY)


def test_the_revision_runs_again_after_a_partial_attempt(schema_settings: tuple[AnalyticsPgSettings, Engine]) -> None:
    """The reset is an UPDATE driven by `has_activity`, so a retry at the same revision meets nothing left to do."""
    settings, engine = schema_settings
    schema = settings.schema
    _upgrade_up_to(settings, _PREVIOUS)
    with engine.begin() as connection:
        _seed(connection, schema)

    migrations.run_migrations(settings)
    with engine.begin() as connection:
        connection.execute(
            text(f'UPDATE "{schema}".cli_analytics_alembic_version SET version_num = :v'), {"v": _PREVIOUS}
        )

    migrations.run_migrations(settings)  # the legacy row is already NULL: nothing matches a second time

    with engine.connect() as connection:
        connection.execute(text(f'SET search_path TO "{schema}"'))
        assert _version(connection, schema) == _HEAD
        facts = dict(connection.execute(text("SELECT session_id, has_activity FROM session_dims")).fetchall())
        assert facts[_LEGACY_ZERO_LINES_ONLY] is None
        assert facts[_REAL_LINES] is True
        assert facts[_ZERO_LINES_BUT_TURNS] is True
