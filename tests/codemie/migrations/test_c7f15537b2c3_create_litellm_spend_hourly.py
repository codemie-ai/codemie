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

"""Unit tests for the c7f15537b2c3 migration: the SQL that creates and drops `litellm_spend_hourly`.

The projection's real-database behaviour is proven by a separate local oracle; these tests pin the
emitted statements - their order, the trigger shapes and the `SECURITY DEFINER` hardening - so a
refactor of the string building cannot silently change what PostgreSQL is asked to do.
"""

import importlib
import re
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

LOCK_LEDGER = "LOCK TABLE codemie.litellm_spend_logs IN SHARE ROW EXCLUSIVE MODE"
SAFE_SEARCH_PATH = "SET search_path = pg_catalog, pg_temp"
#: Grouping positions of the hour plus the eleven dimensions.
POSITIONS = "1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12"
NULLABLE_DIMENSIONS = (
    "attr_user_id",
    "attr_principal",
    "attr_project",
    "attr_category",
    "codemie_origin",
    "call_type",
    "codemie_workflow_id",
    "model_group",
    "end_user",
)
KEY = "hour, attr_source, attr_form, " + ", ".join(f"(coalesce('v' || {c}, 'n'))" for c in NULLABLE_DIMENSIONS)
DIMENSION_LIST = "attr_source, attr_form, " + ", ".join(NULLABLE_DIMENSIONS)
HOUR_OF_START = "date_bin('1 hour', start_time, TIMESTAMPTZ '1970-01-01 00:00:00+00')"
#: The signed rows a trigger event contributes: a new row adds its money and itself, an old row takes both back.
PLUS_ROWS = f"SELECT start_time, {DIMENSION_LIST}, spend, 1 AS n FROM new_rows"
MINUS_ROWS = f"SELECT start_time, {DIMENSION_LIST}, -spend AS spend, -1 AS n FROM old_rows"
ADD_DELTA = "DO UPDATE SET spend = h.spend + EXCLUDED.spend, row_count = h.row_count + EXCLUDED.row_count;"
DROP_EMPTIED_KEYS = (
    "DELETE FROM codemie.litellm_spend_hourly WHERE row_count = 0 AND spend = 0 "
    f"AND hour IN (SELECT {HOUR_OF_START} FROM old_rows);"
)


class _FakeConnection:
    """Answers the two questions the migration asks before it emits anything."""

    def __init__(self, isolation: str, schema: str):
        self.dialect = postgresql.dialect()
        self._answers = {"SHOW transaction_isolation": isolation, "SELECT current_schema()": schema}

    def execute(self, statement):
        answer = self._answers[str(statement)]
        return SimpleNamespace(scalar_one=lambda: answer)


class _OpRecorder:
    """Stand-in for alembic's `op`: serves the fake bind and records each statement, whitespace-collapsed."""

    def __init__(self, isolation: str, schema: str):
        self.statements: list[str] = []
        self._connection = _FakeConnection(isolation, schema)

    def get_bind(self):
        return self._connection

    def execute(self, statement):
        self.statements.append(" ".join(str(statement).split()))


def _migration(monkeypatch, isolation: str = "read committed", schema: str = "codemie"):
    module = importlib.import_module("external.alembic.versions.c7f15537b2c3_create_litellm_spend_hourly")
    recorder = _OpRecorder(isolation, schema)
    monkeypatch.setattr(module, "op", recorder)
    return module, recorder


def _upgrade(monkeypatch, schema: str = "codemie") -> list[str]:
    module, recorder = _migration(monkeypatch, schema=schema)
    module.upgrade()
    return recorder.statements


def _downgrade(monkeypatch) -> list[str]:
    module, recorder = _migration(monkeypatch)
    module.downgrade()
    return recorder.statements


def _position(statements: list[str], prefix: str) -> int:
    return next(index for index, statement in enumerate(statements) if statement.startswith(prefix))


def _function(statements: list[str], name: str) -> str:
    return statements[_position(statements, f"CREATE FUNCTION codemie.{name}(")]


def _apply_branches(statements: list[str]) -> dict[str, str]:
    """The apply function's body per trigger event, split on its `TG_OP` dispatch."""
    match = re.search(
        r"IF TG_OP = 'INSERT' THEN (?P<INSERT>.*?) ELSIF TG_OP = 'UPDATE' THEN (?P<UPDATE>.*?) "
        r"ELSIF TG_OP = 'DELETE' THEN (?P<DELETE>.*?) ELSE (?P<TRUNCATE>.*?) END IF; RETURN NULL;",
        _function(statements, "litellm_spend_hourly_apply"),
    )
    assert match is not None
    return match.groupdict()


class TestRevision:
    def test_revision_chains_onto_the_covering_index_migration(self):
        module = importlib.import_module("external.alembic.versions.c7f15537b2c3_create_litellm_spend_hourly")
        assert module.revision == "c7f15537b2c3"
        assert module.down_revision == "c6f15537a1b2"


class TestUpgradeBackfill:
    def test_ledger_lock_is_the_very_first_statement(self, monkeypatch):
        """Every write committed before the lock is in the backfill and every later one fires the trigger."""
        assert _upgrade(monkeypatch)[0] == LOCK_LEDGER

    def test_table_is_created_and_filled_before_indexing_then_analyzed(self, monkeypatch):
        """One bulk load and one sort per index instead of per-row index upkeep; ANALYZE must see the indexes."""
        statements = _upgrade(monkeypatch)
        create = _position(statements, "CREATE TABLE codemie.litellm_spend_hourly (")
        backfill = _position(statements, "INSERT INTO codemie.litellm_spend_hourly (")
        indexes = [i for i, s in enumerate(statements) if s.startswith(("CREATE UNIQUE INDEX", "CREATE INDEX"))]
        analyze = statements.index("ANALYZE codemie.litellm_spend_hourly")

        assert create < backfill < min(indexes)
        assert "FROM codemie.litellm_spend_logs" in statements[backfill]
        assert analyze > max(indexes)

    def test_table_keeps_exact_money_and_nullable_dimensions(self, monkeypatch):
        """An unconstrained `numeric` sums the ledger's money without rounding it; a NULL dimension is a value the
        reads group by, so only the hour, the source and the form may be required."""
        assert (
            "CREATE TABLE codemie.litellm_spend_hourly (hour timestamptz NOT NULL, attr_source text NOT NULL, "
            "attr_form text NOT NULL, attr_user_id text NULL, attr_principal text NULL, attr_project text NULL, "
            "attr_category text NULL, codemie_origin text NULL, call_type text NULL, codemie_workflow_id text NULL, "
            "model_group text NULL, end_user text NULL, spend numeric NOT NULL, row_count bigint NOT NULL)"
        ) in _upgrade(monkeypatch)

    def test_read_indexes_lead_with_their_scope_and_leave_out_rows_without_it(self, monkeypatch):
        """Each serves reads scoped by its leading column over a range of hours; a row without that column can never
        match such a read, so the index leaves it out."""
        read_indexes = [s for s in _upgrade(monkeypatch) if s.startswith("CREATE INDEX")]
        assert read_indexes == [
            "CREATE INDEX ix_spend_hourly_user_time ON codemie.litellm_spend_hourly (attr_user_id, hour) WHERE attr_user_id IS NOT NULL",
            "CREATE INDEX ix_spend_hourly_project_time ON codemie.litellm_spend_hourly (attr_project, attr_category, hour) WHERE attr_project IS NOT NULL",
            "CREATE INDEX ix_spend_hourly_principal_time ON codemie.litellm_spend_hourly (attr_principal, hour) WHERE attr_principal IS NOT NULL",
            "CREATE INDEX ix_spend_hourly_workflow_time ON codemie.litellm_spend_hourly (codemie_workflow_id, hour) WHERE codemie_workflow_id IS NOT NULL",
        ]

    def test_upgrade_refuses_repeatable_read_before_emitting_anything(self, monkeypatch):
        """Under one snapshot the backfill could miss writes committed while it waited for the lock."""
        module, recorder = _migration(monkeypatch, isolation="repeatable read")
        with pytest.raises(RuntimeError, match="READ COMMITTED"):
            module.upgrade()
        assert recorder.statements == []

    def test_schema_needing_quotes_is_quoted_everywhere(self, monkeypatch):
        """The functions qualify every object by schema, so an unquoted odd schema name would break them all; the
        trigger guard compares the bare name, because that is what `TG_TABLE_SCHEMA` holds."""
        statements = _upgrade(monkeypatch, schema="Code Mie")
        emitted = " ".join(statements)

        assert statements[0] == 'LOCK TABLE "Code Mie".litellm_spend_logs IN SHARE ROW EXCLUSIVE MODE'
        assert "IF TG_TABLE_SCHEMA <> 'Code Mie' OR TG_TABLE_NAME <> 'litellm_spend_logs' THEN" in emitted
        assert "Code Mie" not in re.sub(r"\"Code Mie\"\.|'Code Mie'", "", emitted)


class TestApplyFunctionAndTriggers:
    def test_apply_function_is_a_hardened_security_definer(self, monkeypatch):
        """It writes a table the uploader has no privilege on: only the catalog may resolve and PUBLIC may not call it."""
        statements = _upgrade(monkeypatch)
        apply = _function(statements, "litellm_spend_hourly_apply")

        assert f"RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER {SAFE_SEARCH_PATH} AS $fn$" in apply
        assert "REVOKE EXECUTE ON FUNCTION codemie.litellm_spend_hourly_apply() FROM PUBLIC" in statements

    def test_apply_function_first_refuses_any_table_but_this_schemas_ledger(self, monkeypatch):
        """A role holding EXECUTE through default privileges could attach it to a table of its own and forge deltas
        into a projection it cannot write; the check must run before any branch writes."""
        apply = _function(_upgrade(monkeypatch), "litellm_spend_hourly_apply")
        assert (
            "AS $fn$ BEGIN IF TG_TABLE_SCHEMA <> 'codemie' OR TG_TABLE_NAME <> 'litellm_spend_logs' THEN "
            "RAISE EXCEPTION 'litellm_spend_hourly_apply maintains %.litellm_spend_logs only, not %.%', 'codemie', "
            "TG_TABLE_SCHEMA, TG_TABLE_NAME; END IF; IF TG_OP = 'INSERT' THEN"
        ) in apply

    def test_exactly_four_statement_triggers_on_the_ledger(self, monkeypatch):
        """Transition tables allow one event per trigger; TRUNCATE has none to reference."""
        triggers = [s for s in _upgrade(monkeypatch) if s.startswith("CREATE TRIGGER")]
        assert triggers == [
            "CREATE TRIGGER litellm_spend_hourly_after_insert AFTER INSERT ON codemie.litellm_spend_logs REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION codemie.litellm_spend_hourly_apply()",
            "CREATE TRIGGER litellm_spend_hourly_after_update AFTER UPDATE ON codemie.litellm_spend_logs REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION codemie.litellm_spend_hourly_apply()",
            "CREATE TRIGGER litellm_spend_hourly_after_delete AFTER DELETE ON codemie.litellm_spend_logs REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION codemie.litellm_spend_hourly_apply()",
            "CREATE TRIGGER litellm_spend_hourly_after_truncate AFTER TRUNCATE ON codemie.litellm_spend_logs FOR EACH STATEMENT EXECUTE FUNCTION codemie.litellm_spend_hourly_apply()",
        ]

    def test_delta_upsert_skips_empty_deltas_orders_writes_and_adds_on_the_unique_key(self, monkeypatch):
        """Unchanged re-deliveries net to nothing and write nothing; ordered writes lock keys in one order; ON CONFLICT
        must name the unique index's exact expressions or PostgreSQL rejects the statement, and must add the delta -
        overwriting would replace an hour's total with one statement's share of it."""
        statements = _upgrade(monkeypatch)
        apply = _function(statements, "litellm_spend_hourly_apply")
        upsert = (
            f"GROUP BY {POSITIONS} HAVING sum(n) <> 0 OR sum(spend) <> 0 ORDER BY {POSITIONS} "
            f"ON CONFLICT ({KEY}) {ADD_DELTA}"
        )

        assert f"CREATE UNIQUE INDEX ux_spend_hourly_key ON codemie.litellm_spend_hourly ({KEY})" in statements
        assert apply.count(upsert) == 3  # the INSERT, UPDATE and DELETE branches

    def test_insert_branch_adds_the_new_rows_only(self, monkeypatch):
        """An INSERT has no old rows: subtracting any would take back money that was never projected."""
        insert = _apply_branches(_upgrade(monkeypatch))["INSERT"]

        assert f"SELECT {HOUR_OF_START}, {DIMENSION_LIST}, sum(spend), sum(n) FROM ({PLUS_ROWS}) AS delta " in insert
        assert "old_rows" not in insert

    def test_update_branch_moves_each_row_from_its_old_key_to_its_new_one_then_drops_emptied_keys(self, monkeypatch):
        """A changed row takes its old money and count back and adds the new; a key it left can net to zero, and an
        all-zero row would show as a projected group the ledger no longer has."""
        update = _apply_branches(_upgrade(monkeypatch))["UPDATE"]

        assert f"FROM ({PLUS_ROWS} UNION ALL {MINUS_ROWS}) AS delta " in update
        assert update.endswith(f"{ADD_DELTA} {DROP_EMPTIED_KEYS}")

    def test_delete_branch_subtracts_the_old_rows_only_then_drops_emptied_keys(self, monkeypatch):
        """A DELETE has no new rows; what it takes back can empty a key, which must then go as on UPDATE."""
        delete = _apply_branches(_upgrade(monkeypatch))["DELETE"]

        assert f"FROM ({MINUS_ROWS}) AS delta " in delete
        assert "new_rows" not in delete
        assert delete.endswith(f"{ADD_DELTA} {DROP_EMPTIED_KEYS}")

    def test_truncate_branch_empties_the_projection(self, monkeypatch):
        """TRUNCATE has no transition tables to subtract, and an empty ledger projects to an empty table."""
        assert _apply_branches(_upgrade(monkeypatch))["TRUNCATE"] == "TRUNCATE codemie.litellm_spend_hourly;"


class TestFunctionBodies:
    @pytest.mark.parametrize(
        "function", ["litellm_spend_hourly_apply", "litellm_spend_hourly_rebuild", "litellm_spend_hourly_drift"]
    )
    def test_every_ledger_and_projection_reference_is_schema_qualified(self, monkeypatch, function):
        """With search_path = pg_catalog, pg_temp a bare name resolves to nothing - or to a planted temp table."""
        body = _function(_upgrade(monkeypatch), function).split("$fn$")[1]
        code = re.sub(r"'[^']*'", "''", body)  # error messages name the table but do not reference it

        assert re.search(r"codemie\.litellm_spend_(?:hourly|logs)\b", code)
        assert re.findall(r"(?<!codemie\.)\blitellm_spend_(?:hourly|logs)\b", code) == []

    @pytest.mark.parametrize("function", ["litellm_spend_hourly_rebuild", "litellm_spend_hourly_drift"])
    def test_ops_functions_run_as_invoker_with_the_safe_search_path_and_whole_hour_bounds(self, monkeypatch, function):
        """A partial hour would rebuild or compare only part of a projected row."""
        statement = _function(_upgrade(monkeypatch), function)

        assert f"{SAFE_SEARCH_PATH} AS $fn$" in statement
        assert "SECURITY DEFINER" not in statement
        assert "from_hour >= to_hour" in statement
        for bound in ("from_hour", "to_hour"):
            assert f"date_bin('1 hour', {bound}, TIMESTAMPTZ '1970-01-01 00:00:00+00') <> {bound}" in statement

    def test_rebuild_refuses_to_run_outside_read_committed(self, monkeypatch):
        """Its delete and re-insert must each see every committed ledger write, as the backfill did."""
        rebuild = _function(_upgrade(monkeypatch), "litellm_spend_hourly_rebuild")
        assert "IF current_setting('transaction_isolation') <> 'read committed' THEN RAISE EXCEPTION" in rebuild


class TestDowngrade:
    def test_downgrade_locks_both_tables_up_front_then_drops_in_dependency_order(self, monkeypatch):
        """DROP TRIGGER needs ACCESS EXCLUSIVE on the ledger: taking it on both tables first, in one order, leaves no
        lock upgrade for a projected read to deadlock against. The triggers depend on the apply function, so they
        go before it; the table goes last."""
        assert _downgrade(monkeypatch) == [
            "LOCK TABLE codemie.litellm_spend_logs, codemie.litellm_spend_hourly IN ACCESS EXCLUSIVE MODE",
            "DROP TRIGGER litellm_spend_hourly_after_insert ON codemie.litellm_spend_logs",
            "DROP TRIGGER litellm_spend_hourly_after_update ON codemie.litellm_spend_logs",
            "DROP TRIGGER litellm_spend_hourly_after_delete ON codemie.litellm_spend_logs",
            "DROP TRIGGER litellm_spend_hourly_after_truncate ON codemie.litellm_spend_logs",
            "DROP FUNCTION codemie.litellm_spend_hourly_drift(timestamptz, timestamptz)",
            "DROP FUNCTION codemie.litellm_spend_hourly_rebuild(timestamptz, timestamptz)",
            "DROP FUNCTION codemie.litellm_spend_hourly_apply()",
            "DROP TABLE codemie.litellm_spend_hourly",
        ]
