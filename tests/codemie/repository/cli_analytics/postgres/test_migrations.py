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

"""Wiring of the analytics schema migrations: connection, advisory lock, schema creation, Alembic upgrade."""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock, patch

import pytest

from codemie.repository.cli_analytics.postgres import migrations
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from tests.codemie.repository.cli_analytics.postgres.test_engine import SETTINGS


def _run(settings: AnalyticsPgSettings, schema_exists: bool = False) -> tuple[MagicMock, ...]:
    """Runs the migrations with SQLAlchemy and Alembic mocked; returns
    (create_engine, connection, upgrade, register_iam, engine)."""
    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.execute.return_value.scalar.return_value = 1 if schema_exists else None
    with (
        patch.object(migrations, "create_engine", return_value=engine) as create_engine,
        patch.object(migrations.command, "upgrade") as upgrade,
        patch.object(migrations, "_register_iam_token_event") as register_iam,
    ):
        migrations.run_migrations(settings)
    return create_engine, connection, upgrade, register_iam, engine


def _statements(connection: MagicMock) -> list[str]:
    return [str(call.args[0]) for call in connection.execute.call_args_list]


def test_migrations_run_on_a_throwaway_sync_engine_for_the_analytics_database():
    create_engine, _, _, _, engine = _run(SETTINGS)

    assert create_engine.call_args.args[0] == "postgresql+psycopg2://u:p@h/d"
    engine.dispose.assert_called_once()


def test_migrations_hold_an_advisory_lock_then_prepare_the_schema():
    _, connection, _, _, _ = _run(SETTINGS)

    statements = _statements(connection)
    assert statements[1] == "SELECT pg_advisory_xact_lock(:key)"
    assert connection.execute.call_args_list[1].args[1] == {"key": SETTINGS.lock_key("migrations")}
    assert 'CREATE SCHEMA IF NOT EXISTS "codemie_analytics"' in statements
    assert statements[-1] == 'SET search_path TO "codemie_analytics"'


def test_a_schema_that_already_exists_is_not_created_again():
    # CREATE SCHEMA checks the database's CREATE privilege before IF NOT EXISTS, so a role
    # given only a schema created for it could never migrate.
    _, connection, _, _, _ = _run(SETTINGS, schema_exists=True)

    statements = _statements(connection)
    assert not any(sql.startswith("CREATE SCHEMA") for sql in statements)
    assert statements[-1] == 'SET search_path TO "codemie_analytics"'


def test_waiting_for_another_pods_migration_is_bounded():
    _, connection, _, _, _ = _run(SETTINGS)

    # Taken before the advisory lock: a pod stuck mid-migration cannot hold the others forever.
    assert _statements(connection)[0] == f"SET LOCAL lock_timeout = '{migrations.LOCK_TIMEOUT}'"


def test_a_database_that_does_not_answer_fails_the_migration_within_seconds():
    create_engine, _, _, _, _ = _run(SETTINGS)

    assert create_engine.call_args.kwargs["connect_args"] == {"connect_timeout": migrations.CONNECT_TIMEOUT_S}
    assert migrations.CONNECT_TIMEOUT_S <= 30


def test_alembic_upgrades_to_head_on_that_connection_and_schema():
    _, connection, upgrade, _, _ = _run(SETTINGS)

    cfg, target = upgrade.call_args.args
    assert target == "head"
    assert cfg.attributes == {"connection": connection, "schema": "codemie_analytics"}
    assert cfg.get_main_option("script_location").endswith("alembic_cli_analytics")


def test_iam_deployments_authenticate_the_migration_engine_with_a_token_for_the_analytics_database():
    settings = dataclasses.replace(SETTINGS, iam_auth=True, dsn="postgresql://an_user@an.example:6432/an")

    create_engine, _, _, register_iam, _ = _run(settings)

    register_iam.assert_called_once_with(create_engine.return_value, host="an.example", port=6432, user="an_user")


def test_the_engine_is_disposed_even_when_the_upgrade_fails():
    engine = MagicMock()
    with (
        patch.object(migrations, "create_engine", return_value=engine),
        patch.object(migrations.command, "upgrade", side_effect=RuntimeError("bad revision")),
        pytest.raises(RuntimeError, match="bad revision"),
    ):
        migrations.run_migrations(SETTINGS)

    engine.dispose.assert_called_once()


def test_the_migration_scripts_ship_with_the_package():
    assert (migrations.ALEMBIC_DIR / "env.py").is_file()
    assert any(migrations.ALEMBIC_DIR.joinpath("versions").glob("*_cli_analytics_initial_schema.py"))
