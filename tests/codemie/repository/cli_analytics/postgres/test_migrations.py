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
import importlib.util
import re
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from codemie.repository.cli_analytics.postgres import ingestor, migrations, rollups
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from tests.codemie.repository.cli_analytics.postgres.test_engine import SETTINGS


def _run(settings: AnalyticsPgSettings, schema_exists: bool = False) -> tuple[MagicMock, ...]:
    """Runs the migrations with SQLAlchemy and Alembic mocked; returns
    (create_engine, connection, upgrade, register_iam, engine)."""
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
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
    assert statements[1] == "SELECT pg_advisory_lock(:key)"  # session-level: it outlives a commit of the revision
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


def test_alembic_upgrades_one_revision_at_a_time_ending_at_the_head_on_that_connection_and_schema():
    _, connection, upgrade, _, _ = _run(SETTINGS)

    targets = [call.args[1] for call in upgrade.call_args_list]
    assert len(targets) > 3 and len(set(targets)) == len(targets)
    assert targets[0] == "a1c1a0000001" and targets[-1] == "a1c1a0000011"  # the first and the head
    assert targets.index("a1c1a0000010") < targets.index("a1c1a0000011")  # main's publication contract first
    cfg = upgrade.call_args.args[0]
    assert cfg.attributes == {"connection": connection, "schema": "codemie_analytics"}
    assert cfg.get_main_option("script_location").endswith("alembic_cli_analytics")


def test_each_revision_runs_on_its_own_connection_with_its_own_lock_wait_and_session_lock_and_is_committed():
    # A revision that fails (an index build past its lock timeout) must roll back only itself: the revisions before it
    # stay committed. The advisory lock is session-level, so a revision that commits part-way keeps it.
    _, connection, upgrade, _, engine = _run(SETTINGS)

    revisions = upgrade.call_count
    assert engine.connect.call_count == revisions
    assert connection.commit.call_count == revisions
    statements = _statements(connection)
    assert statements.count("SELECT pg_advisory_lock(:key)") == revisions
    assert not any("pg_advisory_xact_lock" in sql for sql in statements)
    assert statements.count(f"SET LOCAL lock_timeout = '{migrations.LOCK_TIMEOUT}'") == revisions


def test_a_failing_revision_leaves_the_earlier_ones_applied_and_stops_the_later_ones():
    engine = MagicMock()
    outcomes = {"a1c1a0000011": RuntimeError("lock timeout")}
    done: list[str] = []

    def upgrade(cfg, revision):
        if revision in outcomes:
            raise outcomes[revision]
        done.append(revision)

    with (
        patch.object(migrations, "create_engine", return_value=engine),
        patch.object(migrations.command, "upgrade", side_effect=upgrade),
        pytest.raises(RuntimeError, match="lock timeout"),
    ):
        migrations.run_migrations(SETTINGS)

    assert "a1c1a0000010" in done and "a1c1a0000011" not in done  # main's revision was upgraded before this one failed
    assert (
        engine.connect.return_value.__exit__.call_count == len(done) + 1
    )  # each on its own connection, the last unwound
    engine.dispose.assert_called_once()


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


def _load_revision(file_name: str) -> ModuleType:
    path = Path(migrations.ALEMBIC_DIR) / "versions" / file_name
    spec = importlib.util.spec_from_file_location(file_name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCHEMA_EXTENSION = _load_revision("a1c1a0000006_cli_analytics_schema_extension.py")
SESSION_ROUTING = _load_revision("a1c1a0000007_cli_analytics_session_routing.py")
_COST_DAILY_KEY_STATEMENTS = (
    "ALTER TABLE cost_daily DROP CONSTRAINT cost_daily_pkey",
    "ALTER TABLE cost_daily ADD PRIMARY KEY (day, session_id, user_email, model_name, query_source, speed, "
    "inference_geo, scope_kind, scope_name, agent_type)",
)


def _normalized(statement: str) -> str:
    return " ".join(statement.split())


def test_schema_extension_revision_follows_the_initial_revision() -> None:
    assert SCHEMA_EXTENSION.revision == "a1c1a0000006"
    assert SCHEMA_EXTENSION.down_revision == "a1c1a0000001"


def test_schema_extension_uses_only_the_allowed_statement_kinds() -> None:
    for statement in SCHEMA_EXTENSION.UPGRADE:
        text = _normalized(statement)
        allowed = (
            text.startswith(("CREATE TABLE ", "CREATE INDEX "))
            or (text.startswith("ALTER TABLE ") and " ADD COLUMN " in text and "cost_daily_pkey" not in text)
            or text in _COST_DAILY_KEY_STATEMENTS
        )
        assert allowed, text


def test_schema_extension_swaps_the_cost_daily_key_with_exactly_two_statements() -> None:
    texts = [_normalized(s) for s in SCHEMA_EXTENSION.UPGRADE]

    assert [t for t in texts if "cost_daily_pkey" in t or "ADD PRIMARY KEY" in t] == list(_COST_DAILY_KEY_STATEMENTS)


@pytest.mark.parametrize(
    "forbidden",
    [
        "IF NOT EXISTS",
        "CHECK",
        "REFERENCES",
        "UNIQUE",
        "CREATE TYPE",
        "EXTENSION",
        "NULLS NOT DISTINCT",
        "PARTITION OF",
        "codemie_analytics.",
    ],
)
def test_schema_extension_avoids_forbidden_constructs(forbidden: str) -> None:
    assert not [s for s in SCHEMA_EXTENSION.UPGRADE if forbidden in s]


INITIAL_SCHEMA = _load_revision("a1c1a0000001_cli_analytics_initial_schema.py")
SESSION_TITLE = _load_revision("a1c1a0000009_cli_analytics_session_title.py")
_INSERT_TARGET = re.compile(r"INSERT INTO (\w+)(?: AS \w+)? \(([^)]*)\)")


def _declared_columns() -> dict[str, set[str]]:
    """The columns of each table once all revisions ran, read from the text of their statements."""
    declared: dict[str, set[str]] = {}
    for statement in [
        *INITIAL_SCHEMA.UPGRADE,
        *SCHEMA_EXTENSION.UPGRADE,
        *SESSION_TITLE.UPGRADE,
        *SESSION_ROUTING.UPGRADE,
    ]:
        lines = statement.strip().splitlines()
        created = re.match(r"CREATE TABLE (\w+) \(", lines[0])
        if created:
            for line in lines[1:]:
                definition = line.split("--", 1)[0].strip()
                if definition.startswith(")"):
                    break
                if definition and not definition.startswith("PRIMARY KEY"):
                    declared.setdefault(created.group(1), set()).add(definition.split()[0])
        altered = re.match(r"ALTER TABLE (\w+)", lines[0])
        if altered:
            declared.setdefault(altered.group(1), set()).update(re.findall(r"ADD COLUMN (\w+)", statement))
    return declared


def _written_columns(statement: str) -> tuple[str, set[str]]:
    """(table, columns) of the column list of an INSERT statement."""
    table, names = _INSERT_TARGET.search(_normalized(statement)).groups()
    return table, {name.strip() for name in names.split(",")}


def test_session_title_revision_follows_the_schema_extension() -> None:
    assert SESSION_TITLE.revision == "a1c1a0000009"
    assert SESSION_TITLE.down_revision == SCHEMA_EXTENSION.revision


def test_session_title_revision_only_adds_a_nullable_column_and_downgrade_drops_it() -> None:
    assert [_normalized(s) for s in SESSION_TITLE.UPGRADE] == ["ALTER TABLE session_dims ADD COLUMN session_title text"]
    assert [_normalized(s) for s in SESSION_TITLE.DOWNGRADE] == ["ALTER TABLE session_dims DROP COLUMN session_title"]


def test_the_session_title_column_is_declared_and_kept_out_of_session_dims_attrs() -> None:
    assert "session_title" in _declared_columns()["session_dims"]
    assert "session_title" in rollups._SESSION_DIMS_COLUMNS


def test_the_declared_columns_are_read_from_all_revisions() -> None:
    declared = _declared_columns()

    assert {"ts", "session_id", "attrs", "request_id"} <= declared["log_events"]  # initial + ADD COLUMN
    assert {"tool_name", "skill_name", "ingest_seq"} <= declared["hook_events"]  # the shared text columns too
    assert "PRIMARY" not in declared["session_usage"] and ")" not in declared["log_events"]
    assert declared["cost_daily"] >= {"api_call_count", "speed", "cache_creation_1h_tokens"}


@pytest.mark.parametrize("table", sorted(ingestor._INSERT_SQL))
def test_every_column_ingest_writes_is_declared_by_the_revisions(table: str) -> None:
    # A column dropped from a revision (request_id of log_events, thinking_tokens of usage_requests)
    # would fail every ingest INSERT of that table at run time.
    target, written = _written_columns(ingestor._INSERT_SQL[table])

    assert target == table
    assert written and written <= _declared_columns()[table], written - _declared_columns()[table]


def test_every_column_the_recompute_writes_is_declared_by_the_revisions() -> None:
    # The same for the refresher: one missing column fails every batch that reaches its statement.
    declared = _declared_columns()
    inserts = [_written_columns(s) for s in rollups.RECOMPUTE if s.lstrip().startswith("INSERT INTO")]
    inserts = [(table, written) for table, written in inserts if not table.startswith("_")]  # not the temp tables

    assert {"cost_daily", "session_usage", "session_usage_hourly", "session_dims", "subagent_invocations"} <= {
        table for table, _ in inserts
    }
    for table, written in inserts:
        assert written and written <= declared[table], (table, written - declared[table])


def test_downgrade_removes_group_b_before_group_a() -> None:
    texts = [_normalized(s) for s in SCHEMA_EXTENSION.DOWNGRADE]
    group_b = texts[: texts.index("DROP TABLE subagent_invocations")]  # the first statement of group A

    assert group_b[:2] == ["DROP TABLE session_usage_hourly", "DROP INDEX log_events_session_request"]
    for statement in SCHEMA_EXTENSION.UPGRADE_GROUP_B:
        text = _normalized(statement)
        if text.startswith("ALTER TABLE "):
            table = text.split()[2]
            dropped = [t for t in group_b if t.startswith(f"ALTER TABLE {table} DROP COLUMN")]
            assert len(dropped) == 1, table
            assert re.findall(r"DROP COLUMN (\w+)", dropped[0]) == re.findall(r"ADD COLUMN (\w+)", text), table


def test_downgrade_restores_the_five_column_cost_daily_key_and_drops_what_the_upgrade_added() -> None:
    texts = [_normalized(s) for s in SCHEMA_EXTENSION.DOWNGRADE]
    drop_key = texts.index("ALTER TABLE cost_daily DROP CONSTRAINT cost_daily_pkey")
    drop_columns = next(i for i, t in enumerate(texts) if t.startswith("ALTER TABLE cost_daily DROP COLUMN"))
    restore_key = texts.index(
        "ALTER TABLE cost_daily ADD PRIMARY KEY (day, session_id, user_email, model_name, query_source)"
    )

    # the wide key goes first, then its columns, then the original key
    assert drop_key < drop_columns < restore_key
    upgrade = [_normalized(s) for s in SCHEMA_EXTENSION.UPGRADE]
    created = {t.split()[2] for t in upgrade if t.startswith("CREATE TABLE ")}
    assert {t.split()[2] for t in texts if t.startswith("DROP TABLE ")} == created
    added = {
        (t.split()[2], c) for t in upgrade if t.startswith("ALTER TABLE ") for c in re.findall(r"ADD COLUMN (\w+)", t)
    }
    dropped = {
        (t.split()[2], c) for t in texts if t.startswith("ALTER TABLE ") for c in re.findall(r"DROP COLUMN (\w+)", t)
    }
    assert dropped == added  # the columns added to a table of this revision too, before the table is dropped


_ROUTING_COLUMNS = {
    "hook_events": [
        "subscription_type",
        "project_origin",
        "route_note",
        "client_subscription_type",
        "client_policy_version",
    ],
    "session_dims": ["owner_email", "subscription_type", "project_origin", "route_note"],
}


def test_session_routing_revision_follows_the_session_title() -> None:
    assert SESSION_ROUTING.revision == "a1c1a0000007"
    assert SESSION_ROUTING.down_revision == SESSION_TITLE.revision


def test_session_routing_adds_only_nullable_text_columns_to_hook_events_and_session_dims() -> None:
    added = {}
    for statement in SESSION_ROUTING.UPGRADE:
        text = _normalized(statement)
        assert text.startswith("ALTER TABLE "), text
        assert not any(word in text for word in ("NOT NULL", "DEFAULT", "UNIQUE", "IF NOT EXISTS")), text
        added[text.split()[2]] = re.findall(r"ADD COLUMN (\w+) text(?:,|$)", text)

    assert added == _ROUTING_COLUMNS


def test_session_routing_downgrade_drops_what_the_upgrade_added() -> None:
    dropped = {_normalized(s).split()[2]: re.findall(r"DROP COLUMN (\w+)", s) for s in SESSION_ROUTING.DOWNGRADE}

    assert dropped == _ROUTING_COLUMNS


def test_the_routing_columns_are_declared_for_ingest_and_recompute() -> None:
    declared = _declared_columns()

    for table, columns in _ROUTING_COLUMNS.items():
        assert set(columns) <= declared[table], table


FACTS = _load_revision("a1c1a0000011_cli_analytics_session_activity_facts.py")
_ALL_INDEXES = [
    "session_dims_started_at",
    "session_dims_started_shared",
    "session_dims_active_started",
    "session_dims_active_shared_cov",
    "session_dims_resumed_last",
    "session_dims_activity_pending",
]


def test_the_activity_facts_are_one_revision_after_the_publication_contract_with_one_head() -> None:
    assert FACTS.revision == "a1c1a0000011"
    assert FACTS.down_revision == "a1c1a0000010"  # the publication contract, the head of main

    versions = Path(migrations.ALEMBIC_DIR) / "versions"
    revisions = {}
    for path in versions.glob("a1c1a*.py"):
        module = _load_revision(path.name)
        revisions[module.revision] = module.down_revision
    heads = set(revisions) - {down for down in revisions.values() if down}
    assert heads == {"a1c1a0000011"}


def test_the_activity_columns_are_nullable_without_default_under_a_bounded_lock_wait() -> None:
    statements = [_normalized(s) for s in FACTS.column_statements()]

    # NULL = not derived yet: no default, so the add rewrites nothing and no pre-existing row reads as "no activity"
    assert statements == ["SET LOCAL lock_timeout = '5s'", _normalized(FACTS.ADD_COLUMNS)]
    add = statements[1]
    for column in ("has_activity boolean", "last_activity_day date", "resumed boolean"):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in add
    assert "NOT NULL" not in add and "DEFAULT" not in add


def test_the_upgrade_commits_the_columns_before_the_index_builds_and_the_downgrade_drops_indexes_then_columns() -> None:
    events: list[str] = []
    bind = MagicMock()
    bind.commit.side_effect = lambda: events.append("COMMIT")
    with (
        patch.object(FACTS.op, "execute", side_effect=events.append),
        patch.object(FACTS.op, "get_bind", return_value=bind),
    ):
        FACTS.upgrade()
        events.append("--")
        FACTS.downgrade()

    # the columns, a commit of them, then the indexes: a lock timeout of the builds cannot take the columns back
    assert events == [
        *FACTS.column_statements(),
        "COMMIT",
        *FACTS.index_statements(),
        "--",
        *FACTS.INDEXES_DOWNGRADE,
        *FACTS.COLUMNS_DOWNGRADE,
    ]
    drop = FACTS.COLUMNS_DOWNGRADE[0]
    assert drop.startswith("ALTER TABLE session_dims DROP COLUMN IF EXISTS")
    for column in ("has_activity", "last_activity_day", "resumed"):
        assert column in drop


def test_the_index_step_bounds_the_lock_wait_once_builds_idempotently_and_restores_it() -> None:
    statements = [_normalized(s) for s in FACTS.index_statements()]

    assert statements[0] == "SET LOCAL lock_timeout = '5s'"
    assert statements[1:-1] == [
        s for name, create in FACTS.INDEXES for s in (_normalized(FACTS.drop_if_invalid(name)), _normalized(create))
    ]
    assert statements[-1] == f"SET LOCAL lock_timeout = '{migrations.LOCK_TIMEOUT}'"
    assert sum(s.startswith("SET LOCAL lock_timeout") for s in statements) == 2  # the bound and the restore
    creates = [s for s in statements if s.startswith("CREATE INDEX IF NOT EXISTS")]
    assert [c.split()[5] for c in creates] == _ALL_INDEXES
    # the columns are the first step's: this step neither adds nor drops one
    assert not any("ADD COLUMN" in s or "DROP COLUMN" in s for s in statements)
    # the covering index of the plain-window list is superseded by the one over the activity fact
    assert "session_dims_started_shared_cov" not in " ".join(statements)


@pytest.mark.parametrize("index", _ALL_INDEXES)
def test_an_invalid_leftover_index_is_dropped_before_the_idempotent_create(index: str) -> None:
    # A failed CREATE INDEX CONCURRENTLY leaves an invalid index; IF NOT EXISTS alone would skip it and report success.
    drop = _normalized(FACTS.drop_if_invalid(index))

    assert f"c.relname = '{index}'" in drop
    assert "NOT i.indisvalid" in drop
    assert f"DROP INDEX {index};" in drop
    statements = [_normalized(s) for s in FACTS.index_statements()]
    create = next(i for i, s in enumerate(statements) if s.startswith(f"CREATE INDEX IF NOT EXISTS {index} "))
    assert statements.index(drop) == create - 1


def test_list_indexes_shared_predicate_is_the_listed_not_private_scope() -> None:
    from codemie.repository.cli_analytics.scope import sql_private

    # The list's scope predicate over alias `x`, with the alias dropped, is exactly NOT (the index predicate).
    private = _normalized(sql_private("x")).replace("x.", "")
    assert _normalized(FACTS.SHARED_PREDICATE).endswith(f"NOT {private}")


def test_list_indexes_are_partial_over_the_facts_and_the_shared_scope() -> None:
    create = {name: _normalized(sql) for name, sql in FACTS.INDEXES}
    shared = _normalized(FACTS.SHARED_PREDICATE)

    assert (
        create["session_dims_started_at"]
        == "CREATE INDEX IF NOT EXISTS session_dims_started_at ON session_dims (started_at)"
    )
    assert create["session_dims_started_shared"].endswith(f"(started_at) WHERE {shared}")
    # the covering index has the shared predicate and the activity fact, so the planner proves both from it; the id rides in the leaf
    assert create["session_dims_active_shared_cov"] == (
        "CREATE INDEX IF NOT EXISTS session_dims_active_shared_cov ON session_dims (started_at) "
        f"INCLUDE (session_id) WHERE has_activity AND {shared}"
    )
    assert create["session_dims_active_started"].endswith("(started_at) WHERE has_activity")
    assert create["session_dims_resumed_last"].endswith("(last_activity_day) WHERE resumed")
    assert create["session_dims_activity_pending"].endswith("(started_at) WHERE has_activity IS NULL")


def test_the_indexes_downgrade_drops_the_indexes_in_reverse() -> None:
    assert [f"DROP INDEX IF EXISTS {name}" for name in reversed(_ALL_INDEXES)] == FACTS.INDEXES_DOWNGRADE
