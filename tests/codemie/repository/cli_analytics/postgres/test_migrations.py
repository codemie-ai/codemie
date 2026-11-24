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
from sqlalchemy.exc import InvalidRequestError

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
    assert targets[0] == "a1c1a0000001" and targets[-1] == "a1c1a0000022"  # the first and the head
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
SUBAGENT_LISTING = _load_revision("a1c1a0000018_cli_analytics_subagent_listing.py")
RESET_ZERO_LINES = _load_revision("a1c1a0000019_cli_analytics_reset_zero_lines_activity.py")


def test_subagent_listing_revision_adds_the_partial_index_and_the_flag() -> None:
    assert (SUBAGENT_LISTING.revision, SUBAGENT_LISTING.down_revision) == ("a1c1a0000018", "a1c1a0000017")
    column, backfill = (_normalized(s) for s in SUBAGENT_LISTING.UPGRADE)
    # hook_events is partitioned: no CONCURRENTLY on the parent; the partitions' indexes are built that way.
    assert _normalized(SUBAGENT_LISTING.PARENT_INDEX) == (
        "CREATE INDEX IF NOT EXISTS hook_events_subagent_start ON ONLY hook_events (agent_id) "
        "WHERE event_type = 'agent.subagent.start'"
    )
    assert "ONLY" not in _normalized(SUBAGENT_LISTING.PLAIN_INDEX)
    assert column == "ALTER TABLE session_dims ADD COLUMN IF NOT EXISTS is_subagent boolean"
    assert backfill.startswith(
        "UPDATE session_dims d SET is_subagent = true WHERE d.session_id IN ( SELECT t.session_id"
    )
    assert "t.is_subagent IS DISTINCT FROM true" in backfill and backfill.endswith("LIMIT :batch)")
    assert SUBAGENT_LISTING.DOWNGRADE == [
        "DROP INDEX IF EXISTS hook_events_subagent_start",
        "ALTER TABLE session_dims DROP COLUMN IF EXISTS is_subagent",
    ]


def test_the_subagent_backfill_applies_the_rule_of_the_start_event_the_code_applies() -> None:
    from codemie.repository.cli_analytics.postgres.hook_row_sql import names_subagent_session

    assert _normalized(names_subagent_session("sh")) in _normalized(SUBAGENT_LISTING.BACKFILL)


class _RecordingConnection:
    """A connection that records what a revision does to it, in order, and answers the index build's catalog probes.

    Like SQLAlchemy's Connection, a statement autobegins a Transaction object, also under AUTOCOMMIT, and the
    isolation level may not change while one is open (InvalidRequestError)."""

    def __init__(self, partitions: list[list[tuple[str, str]]] | None = None, flagged: list[int] | None = None) -> None:
        self.log: list[str] = []
        self._flagged = list(flagged or [0])
        self._partitions = partitions
        self._passes = 0
        self.default_isolation_level = "READ COMMITTED"
        self._in_transaction = True  # the runner hands the revision a connection with a transaction open

    def execute(self, statement, *args):
        sql = _normalized(str(statement))
        self.log.append(sql)
        self._in_transaction = True
        result = MagicMock()
        if sql == _normalized(SUBAGENT_LISTING.BACKFILL):
            result.rowcount = self._flagged.pop(0)
        elif sql == _normalized(SUBAGENT_LISTING.PARTITION_COUNT):
            result.scalar.return_value = 0 if self._partitions is None else 2
        elif sql == _normalized(SUBAGENT_LISTING.UNINDEXED_PARTITIONS):
            rows = self._partitions[min(self._passes, len(self._partitions) - 1)]
            self._passes += 1
            result.all.return_value = rows
            result.first.return_value = rows[0] if rows else None
        return result

    def commit(self) -> None:
        self.log.append("COMMIT")
        self._in_transaction = False

    def execution_options(self, **options):
        if self._in_transaction:
            raise InvalidRequestError(
                "isolation_level may not be altered unless rollback() or commit() is called first"
            )
        self.log.append(f"ISOLATION {options['isolation_level']}")
        return self


def _upgrade_subagent_listing(connection: _RecordingConnection) -> list[str]:
    fake_op = MagicMock()
    fake_op.get_bind.return_value = connection
    fake_op.execute.side_effect = connection.execute
    with patch.object(SUBAGENT_LISTING, "op", fake_op):
        SUBAGENT_LISTING.upgrade()
    return connection.log


def test_the_subagent_revision_commits_the_column_before_the_slow_steps_without_autocommit_block() -> None:
    log = _upgrade_subagent_listing(_RecordingConnection())

    assert log == [
        "SET LOCAL lock_timeout = '5s'",
        _normalized(SUBAGENT_LISTING.ADD_COLUMN),
        "COMMIT",  # the runner's transaction is external: the revision ends it itself
        "ISOLATION AUTOCOMMIT",
        _normalized(SUBAGENT_LISTING.PARTITION_COUNT),
        _normalized(SUBAGENT_LISTING.PLAIN_INDEX),
        "COMMIT",  # ends the Transaction object the autocommit statements autobegan, so the level may change
        "ISOLATION READ COMMITTED",
        f"SET LOCAL lock_timeout = '{migrations.LOCK_TIMEOUT}'",
        _normalized(SUBAGENT_LISTING.BACKFILL),
        "COMMIT",  # a batch is a transaction of its own
    ]


def test_the_subagent_backfill_runs_in_committed_batches_until_one_is_short() -> None:
    full = SUBAGENT_LISTING.BACKFILL_BATCH
    connection = _RecordingConnection(flagged=[full, full, 7])

    log = _upgrade_subagent_listing(connection)

    backfill = _normalized(SUBAGENT_LISTING.BACKFILL)
    batch = [f"SET LOCAL lock_timeout = '{migrations.LOCK_TIMEOUT}'", backfill, "COMMIT"]
    assert log[-9:] == batch * 3  # the lock wait is restored for every batch: SET LOCAL ends with its commit
    assert connection._flagged == []


def test_the_subagent_revision_restores_the_isolation_level_when_the_index_build_fails() -> None:
    connection = _RecordingConnection()
    original = connection.execute

    def fail_on_index(statement, *args):
        if "CREATE INDEX" in str(statement):
            raise RuntimeError("lock timeout")
        return original(statement, *args)

    connection.execute = fail_on_index
    with pytest.raises(RuntimeError, match="lock timeout"):
        _upgrade_subagent_listing(connection)

    assert connection.log[-1] == "ISOLATION READ COMMITTED"
    assert _normalized(SUBAGENT_LISTING.BACKFILL) not in connection.log


def test_the_subagent_index_of_a_partitioned_table_is_built_per_partition_and_attached() -> None:
    connection = _RecordingConnection(partitions=[[("hook_events_p1", "hook_events_p1_subagent_start")], []])

    SUBAGENT_LISTING._build_index(connection)

    assert (
        connection.log[1:]
        == [
            _normalized(SUBAGENT_LISTING.PARENT_INDEX),
            _normalized(SUBAGENT_LISTING.UNINDEXED_PARTITIONS),
            "DROP INDEX CONCURRENTLY IF EXISTS hook_events_p1_subagent_start",  # an invalid leftover of a failed build
            "CREATE INDEX CONCURRENTLY hook_events_p1_subagent_start ON hook_events_p1 "
            "(agent_id) WHERE event_type = 'agent.subagent.start'",
            "ALTER INDEX hook_events_subagent_start ATTACH PARTITION hook_events_p1_subagent_start",
            _normalized(SUBAGENT_LISTING.UNINDEXED_PARTITIONS),
        ]
    )


def test_the_subagent_index_build_gives_up_when_partitions_keep_appearing() -> None:
    connection = _RecordingConnection(partitions=[[("hook_events_p1", "hook_events_p1_subagent_start")]])

    with pytest.raises(RuntimeError, match="kept appearing"):
        SUBAGENT_LISTING._build_index(connection)

    assert (
        connection.log.count("ALTER INDEX hook_events_subagent_start ATTACH PARTITION hook_events_p1_subagent_start")
        == 3
    )


def test_reset_zero_lines_revision_follows_the_subagent_listing_with_no_ddl() -> None:
    assert (RESET_ZERO_LINES.revision, RESET_ZERO_LINES.down_revision) == ("a1c1a0000019", "a1c1a0000018")
    assert RESET_ZERO_LINES.UPGRADE == [RESET_ZERO_LINES.RESET]
    assert not any(kw in _normalized(RESET_ZERO_LINES.RESET) for kw in ("CREATE TABLE", "CREATE INDEX", "ALTER TABLE"))


def test_the_reset_selects_only_sessions_whose_sole_activity_row_is_zero_value_lines() -> None:
    reset = _normalized(RESET_ZERO_LINES.RESET)

    assert reset.startswith("UPDATE session_dims sd SET has_activity = NULL, last_activity_day = NULL")
    assert "resumed" not in reset  # value-blind by design; the old rule never made it wrong
    assert "t.has_activity" in reset  # the guard that makes a retry skip rows already reset
    assert "EXISTS (SELECT 1 FROM lines_daily l WHERE l.session_id = t.session_id)" in reset
    assert (
        "NOT EXISTS ( SELECT 1 FROM lines_daily l WHERE l.session_id = t.session_id "
        "AND (l.lines_added <> 0 OR l.lines_removed <> 0) )" in reset
    )
    for table in ("turns_daily td", "tool_facts_daily tf", "cost_daily c", "session_usage u"):
        alias = table.split()[1]
        assert f"NOT EXISTS (SELECT 1 FROM {table} WHERE {alias}.session_id = t.session_id)" in reset
    assert reset.endswith("LIMIT :batch)")


def test_the_reset_runs_in_committed_batches_under_the_runners_lock_wait_until_one_is_short() -> None:
    full = RESET_ZERO_LINES.RESET_BATCH
    connection = _RecordingConnection(flagged=[full, full, 7])
    # `_RecordingConnection.execute` only special-cases SUBAGENT_LISTING's own statements; any other statement
    # (this revision's RESET) falls through to its default MagicMock result, whose `.rowcount` is itself a
    # MagicMock unless told otherwise, so the batch sizes are set directly instead.
    reset_sql = _normalized(RESET_ZERO_LINES.RESET)
    counts = iter(connection._flagged)

    def execute(statement, *args):
        sql = _normalized(str(statement))
        connection.log.append(sql)
        result = MagicMock()
        if sql == reset_sql:
            result.rowcount = next(counts)
        return result

    connection.execute = execute
    fake_op = MagicMock()
    fake_op.get_bind.return_value = connection
    fake_op.execute.side_effect = connection.execute
    with patch.object(RESET_ZERO_LINES, "op", fake_op):
        RESET_ZERO_LINES.upgrade()

    batch = [f"SET LOCAL lock_timeout = '{migrations.LOCK_TIMEOUT}'", reset_sql, "COMMIT"]
    assert connection.log == batch * 3  # a batch that resets fewer than RESET_BATCH rows is the last


def test_the_reset_downgrade_is_a_documented_no_op() -> None:
    assert RESET_ZERO_LINES.downgrade() is None
    assert RESET_ZERO_LINES.downgrade.__doc__  # explains why: which rows were reset is not recoverable


_INSERT_TARGET = re.compile(r"INSERT INTO (\w+)(?: AS \w+)? \(([^)]*)\)")


def _declared_columns() -> dict[str, set[str]]:
    """The columns of each table once all revisions ran, read from the text of their statements."""
    declared: dict[str, set[str]] = {}
    for statement in [
        *INITIAL_SCHEMA.UPGRADE,
        *SCHEMA_EXTENSION.UPGRADE,
        *SESSION_TITLE.UPGRADE,
        *SESSION_ROUTING.UPGRADE,
        *SUBAGENT_LISTING.UPGRADE,
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
            declared.setdefault(altered.group(1), set()).update(
                re.findall(r"ADD COLUMN (?:IF NOT EXISTS )?(\w+)", statement)
            )
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
    assert heads == {"a1c1a0000022"}  # the ranking and Efficiency rollups follow the zero-lines reset


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


SORT_KEYS = _load_revision("a1c1a0000012_cli_analytics_session_sort_keys.py")
_SORT_COLUMNS = {
    "cache_read_total": "bigint",
    "api_call_total": "bigint",
    "classifier_names": "text[]",
    "models_used": "text[]",
    "kind_masks": "int[]",
    "cost_key": "numeric",
    "rates_version": "text",
}
_SORT_INDEX_NAMES = ["session_dims_fw_win", "session_dims_rates_version"]


def test_the_sort_keys_are_one_revision_after_the_activity_facts_with_one_head() -> None:
    assert SORT_KEYS.revision == "a1c1a0000012"
    assert SORT_KEYS.down_revision == "a1c1a0000011"

    versions = Path(migrations.ALEMBIC_DIR) / "versions"
    revisions = {}
    for path in versions.glob("a1c1a*.py"):
        module = _load_revision(path.name)
        revisions[module.revision] = module.down_revision
    heads = set(revisions) - {down for down in revisions.values() if down}
    assert heads == {"a1c1a0000022"}


def test_the_column_step_adds_nullable_columns_without_default_and_a_disabled_control_row() -> None:
    statements = [_normalized(s) for s in SORT_KEYS.column_statements()]
    adds = [s for s in statements if "ALTER TABLE session_dims ADD COLUMN" in s]

    assert len(adds) == len(_SORT_COLUMNS)
    for column, sql_type in _SORT_COLUMNS.items():
        # only a missing column is altered: ALTER ... IF NOT EXISTS locks the table even when the column exists
        assert any(
            "IF NOT EXISTS (SELECT 1 FROM pg_attribute" in s and f"ADD COLUMN {column} {sql_type};" in s for s in adds
        )
    assert not any("NOT NULL" in s or "DEFAULT" in s for s in adds)
    joined = " ".join(statements)
    assert "session_sort_keys_control (enabled boolean NOT NULL DEFAULT false, operator_disabled boolean" in joined
    assert "INSERT INTO session_sort_keys_control (enabled) SELECT false WHERE NOT EXISTS" in joined
    assert statements[0] == "SET LOCAL lock_timeout = '5s'"
    assert sum(s.startswith("SET LOCAL lock_timeout") for s in statements) == 1  # the index step restores the runner's


def test_the_sort_keys_upgrade_commits_the_columns_before_the_index_build_and_the_downgrade_drops_in_reverse() -> None:
    events: list[str] = []
    bind = MagicMock()
    bind.commit.side_effect = lambda: events.append("COMMIT")
    with (
        patch.object(SORT_KEYS.op, "execute", side_effect=events.append),
        patch.object(SORT_KEYS.op, "get_bind", return_value=bind),
    ):
        SORT_KEYS.upgrade()
        events.append("--")
        SORT_KEYS.downgrade()

    # the columns and the control table, a commit of them, then the index: a lock timeout of the build cannot take them back
    assert events == [
        *SORT_KEYS.column_statements(),
        "COMMIT",
        *SORT_KEYS.index_statements(),
        "--",
        *SORT_KEYS.DOWNGRADE,
    ]
    assert SORT_KEYS.DOWNGRADE[0] == "DROP INDEX IF EXISTS session_dims_rates_version"
    assert SORT_KEYS.DOWNGRADE[1] == "DROP INDEX IF EXISTS session_dims_fw_win"
    assert SORT_KEYS.DOWNGRADE[2] == "DROP TABLE IF EXISTS session_sort_keys_control"
    assert SORT_KEYS.DOWNGRADE[3].startswith("ALTER TABLE session_dims DROP COLUMN IF EXISTS")
    for column in _SORT_COLUMNS:
        assert f"DROP COLUMN IF EXISTS {column}" in SORT_KEYS.DOWNGRADE[3]


def test_the_sort_index_predicate_is_a_byte_for_byte_copy_of_the_activity_facts_one() -> None:
    assert SORT_KEYS.SHARED_PREDICATE == FACTS.SHARED_PREDICATE
    partial = dict(SORT_KEYS.INDEXES)["session_dims_fw_win"]
    assert f" WHERE has_activity AND {FACTS.SHARED_PREDICATE}" in partial


def test_the_sort_index_step_bounds_the_lock_wait_once_builds_idempotently_and_restores_it() -> None:
    statements = [_normalized(s) for s in SORT_KEYS.index_statements()]

    assert statements[0] == "SET LOCAL lock_timeout = '5s'"
    assert statements[1:-1] == [
        s
        for name, create in SORT_KEYS.INDEXES
        for s in (_normalized(SORT_KEYS.drop_if_invalid(name)), _normalized(create))
    ]
    assert statements[-1] == f"SET LOCAL lock_timeout = '{migrations.LOCK_TIMEOUT}'"
    assert sum(s.startswith("SET LOCAL lock_timeout") for s in statements) == 2  # the bound and the restore
    creates = [s for s in statements if s.startswith("CREATE INDEX IF NOT EXISTS")]
    assert [c.split()[5] for c in creates] == _SORT_INDEX_NAMES
    joined = " ".join(statements)
    assert "CONCURRENTLY" not in joined and "fillfactor" not in joined.lower()


@pytest.mark.parametrize("index", _SORT_INDEX_NAMES)
def test_an_invalid_sort_index_is_dropped_before_the_idempotent_create(index: str) -> None:
    drop = _normalized(SORT_KEYS.drop_if_invalid(index))

    assert f"c.relname = '{index}'" in drop and "NOT i.indisvalid" in drop and f"DROP INDEX {index};" in drop
    statements = [_normalized(s) for s in SORT_KEYS.index_statements()]
    create = next(i for i, s in enumerate(statements) if s.startswith(f"CREATE INDEX IF NOT EXISTS {index} "))
    assert statements.index(drop) == create - 1


def test_the_framework_window_and_the_stamp_indexes_are_built_the_unused_key_indexes_are_not() -> None:
    create = {name: _normalized(sql) for name, sql in SORT_KEYS.INDEXES}

    # the window index carries what the framework and the stored ctx statements read from a session of the window
    assert list(create) == ["session_dims_fw_win", "session_dims_rates_version"]
    assert (
        "(started_at) INCLUDE (session_id, classifier_names, rates_version, cache_read_total, api_call_total, "
        "cost_key, models_used, kind_masks)" in create["session_dims_fw_win"]
    )
    # the unstamped sessions (set S) are read from the stamp alone: IS NULL, and a version other than the current one
    assert create["session_dims_rates_version"].endswith("ON session_dims (rates_version)")
    assert not hasattr(SORT_KEYS, "CREATE_CTX_KEY") and not hasattr(SORT_KEYS, "CREATE_COST_KEY")


RANKING_AND_EFFICIENCY = _load_revision("a1c1a0000022_cli_analytics_ranking_and_efficiency_rollup.py")


def _normalised(statements) -> list[str]:
    return [" ".join(s.split()) for s in statements]


def test_the_rollup_revision_creates_the_tools_and_models_tables_with_their_keys() -> None:
    created = " ".join(_normalised(RANKING_AND_EFFICIENCY.CREATE_STATEMENTS))

    assert "CREATE TABLE IF NOT EXISTS user_day_tools_rollup" in created
    assert "PRIMARY KEY (start_day, sub_class, origin_class, kind, name)" in created
    assert "calls bigint NOT NULL" in created and "successes bigint NOT NULL" in created
    assert "CREATE TABLE IF NOT EXISTS user_day_tools_models" in created
    assert "PRIMARY KEY (start_day, sub_class, origin_class, model)" in created
    assert "total_tokens bigint NOT NULL" in created


def test_the_rollup_revision_downgrade_drops_the_tools_and_models_tables() -> None:
    dropped = _normalised(RANKING_AND_EFFICIENCY.DOWNGRADE_STATEMENTS)

    assert "DROP TABLE IF EXISTS user_day_tools_rollup" in dropped
    assert "DROP TABLE IF EXISTS user_day_tools_models" in dropped
