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

"""The 31-column shape is a published contract, so it is guarded mechanically.

Another team writes this table against contract v2.1. A column renamed, retyped, dropped or
made nullable here is a contract break that no other test in this repository would notice, so
the whole shape is asserted literally rather than derived from the model - a test that derives
its expectation from the thing under test proves only that the thing agrees with itself.

No database is required: the assertions are over the SQLAlchemy table metadata.
"""

from __future__ import annotations

import importlib

import sqlalchemy as sa

from codemie.repository.spend_logs_model import (
    PROJECTED_DIMENSIONS,
    SPEND_LOGS_TABLE,
    LiteLLMSpendHourly,
    LiteLLMSpendLog,
)

TABLE = LiteLLMSpendLog.__table__

#: (column, type name as PostgreSQL renders it, nullable) - transcribed from the contract's DDL.
CONTRACT: tuple[tuple[str, str, bool], ...] = (
    ("request_id", "TEXT", False),
    ("start_time", "TIMESTAMP WITH TIME ZONE", False),
    ("end_time", "TIMESTAMP WITH TIME ZONE", False),
    ("spend", "NUMERIC", False),
    ("status", "TEXT", False),
    ("call_type", "TEXT", True),
    ("model", "TEXT", True),
    ("model_group", "TEXT", True),
    ("custom_llm_provider", "TEXT", True),
    ("prompt_tokens", "INTEGER", False),
    ("completion_tokens", "INTEGER", False),
    ("total_tokens", "INTEGER", False),
    ("request_duration_ms", "INTEGER", True),
    ("cache_hit", "BOOLEAN", True),
    ("session_id", "TEXT", True),
    ("api_key", "TEXT", True),
    ("user_api_key_alias", "TEXT", True),
    ("end_user", "TEXT", True),
    ("codemie_request_id", "TEXT", True),
    ("codemie_run_id", "TEXT", True),
    ("codemie_workflow_id", "TEXT", True),
    ("codemie_origin", "TEXT", True),
    ("attr_source", "TEXT", False),
    ("attr_form", "TEXT", False),
    ("attr_project", "TEXT", True),
    ("attr_category", "TEXT", True),
    ("attr_user_id", "TEXT", True),
    ("attr_principal", "TEXT", True),
    ("attr_channel", "TEXT", True),
    ("cache_read_input_tokens", "BIGINT", True),
    ("cache_creation_input_tokens", "BIGINT", True),
)

PG = sa.dialects.postgresql.dialect()


class TestTheContractShape:
    def test_the_table_is_named_as_published(self):
        assert SPEND_LOGS_TABLE == "litellm_spend_logs"
        assert TABLE.name == "litellm_spend_logs"

    def test_there_are_exactly_31_columns(self):
        assert len(TABLE.columns) == 31
        assert len(CONTRACT) == 31

    def test_every_column_matches_the_contract_exactly(self):
        actual = tuple((c.name, str(c.type.compile(dialect=PG)).upper(), c.nullable) for c in TABLE.columns)
        assert actual == CONTRACT

    def test_no_column_has_been_added_or_removed(self):
        assert {c.name for c in TABLE.columns} == {name for name, _, _ in CONTRACT}


class TestRowIdentity:
    def test_the_primary_key_is_the_pair_not_the_bare_request_id(self):
        """The pair is required for two independent reasons.

        `request_id` changes namespace with the outcome, and PostgreSQL requires a partitioned
        table's primary key to contain every partition column.
        """
        assert [c.name for c in TABLE.primary_key.columns] == ["request_id", "start_time"]

    def test_identifiers_are_lower_snake_case(self):
        for column in TABLE.columns:
            assert column.name == column.name.lower()
            assert " " not in column.name and '"' not in column.name


class TestTheTypesChosenForTheReader:
    def test_spend_is_unconstrained_numeric(self):
        """Not `numeric(18,9)`, which is what the neighbouring money table uses.

        The contract's discriminating acceptance vector is a spend of `0.07573200000000001`,
        whose shortest round-trip decimal needs 16 significant digits. Measured on PostgreSQL
        17: scale 9 stores `0.075732000`, a different number that no longer round-trips to the
        source double. Constraining this column silently corrupts money.
        """
        spend = TABLE.c.spend.type
        assert isinstance(spend, sa.Numeric)
        assert spend.precision is None, "spend must be unconstrained numeric"
        assert spend.scale is None, "a scale would round money at the column"

    def test_spend_is_not_floating_point(self):
        """Summed as float8 the same rows return different totals depending on scan order."""
        assert not isinstance(TABLE.c.spend.type, sa.Float)

    def test_both_timestamps_carry_a_timezone(self):
        """UTC is enforced by the column rather than by every caller remembering it."""
        for name in ("start_time", "end_time"):
            assert TABLE.c[name].type.timezone is True, f"{name} must be timestamptz"

    def test_cache_hit_is_a_real_boolean(self):
        """The source column is text whose ordinary value is the string "None"."""
        assert isinstance(TABLE.c.cache_hit.type, sa.Boolean)


class TestTheClosedSetColumnsAreNotConstrained:
    def test_no_check_constraint_exists_on_the_table(self):
        """A CHECK rejects the uploader's insert, turning a data defect into a coverage gap.

        The contract's rule is the opposite: store an unexpected value verbatim and surface it
        as a defect metric, never map it into a known value and never drop the row.
        """
        checks = [c for c in TABLE.constraints if isinstance(c, sa.CheckConstraint)]
        assert checks == []


class TestTheIndexSetMirrorsThePhysicalDesign:
    #: name -> (columns, partial predicate or None). Each traces to a numbered access pattern.
    EXPECTED = {
        "ix_spend_logs_user_category_time": (["attr_user_id", "attr_category", "start_time"], None),
        "ix_spend_logs_project_category_time": (["attr_project", "attr_category", "start_time"], None),
        "ix_spend_logs_principal_category_time": (
            ["attr_principal", "attr_category", "start_time"],
            "attr_principal IS NOT NULL",
        ),
        "ix_spend_logs_run_id": (["codemie_run_id"], "codemie_run_id IS NOT NULL"),
        "ix_spend_logs_request_id": (["codemie_request_id"], "codemie_request_id IS NOT NULL"),
        "ix_spend_logs_workflow_time": (
            ["codemie_workflow_id", "start_time"],
            "codemie_workflow_id IS NOT NULL",
        ),
        "ix_spend_logs_unattributed": (["start_time"], "attr_form = 'unattributed'"),
        "ix_spend_logs_provenance_time": (["attr_source", "start_time"], "attr_source <> 'explicit'"),
        "ix_spend_logs_time_cover": (["start_time"], None),
    }

    def test_exactly_the_designed_indexes_exist(self):
        """Adding a tenth needs an access pattern, not a hunch: P8 is a continuous write path."""
        assert {i.name for i in TABLE.indexes} == set(self.EXPECTED)

    def test_each_index_has_the_designed_columns_in_the_designed_order(self):
        """Equality columns lead and the range column trails; order is not cosmetic."""
        for index in TABLE.indexes:
            expected_columns, _ = self.EXPECTED[index.name]
            assert [c.name for c in index.columns] == expected_columns, index.name

    def test_the_partial_indexes_carry_their_predicates(self):
        """Partiality is what keeps the sustained write cost at seven indexes rather than ten."""
        for index in TABLE.indexes:
            _, expected_predicate = self.EXPECTED[index.name]
            actual = index.dialect_options["postgresql"].get("where")
            if expected_predicate is None:
                assert actual is None, f"{index.name} should not be partial"
            else:
                assert str(actual) == expected_predicate, index.name

    def test_no_index_is_unique(self):
        """Row identity is the primary key; no other uniqueness is claimed."""
        assert [i.name for i in TABLE.indexes if i.unique] == []

    def test_only_the_time_cover_index_carries_include_columns_in_the_designed_order(self):
        """P11's window-bounded aggregates filter on start_time alone (EPMCDME-15537).

        INCLUDE carries the columns the unscoped reads (summary groups, traffic class, time
        buckets, workflow/execution, project residue) filter or sum, which is what makes those
        scans index-only; a column missing here sends every such read back to the heap.
        Model-dimension and access-scoped or report-filtered reads still visit the heap by design.
        """
        includes = {
            index.name: list(index.dialect_options["postgresql"]["include"])
            for index in TABLE.indexes
            if index.dialect_options["postgresql"].get("include")
        }
        assert includes == {
            "ix_spend_logs_time_cover": [
                "spend",
                "attr_category",
                "codemie_origin",
                "call_type",
                "codemie_workflow_id",
                "codemie_request_id",
                "attr_project",
                "attr_source",
            ]
        }


class TestNoStorageParametersAreSet:
    def test_the_model_sets_no_reloptions(self):
        """PostgreSQL refuses storage parameters on a partitioned parent and they do not cascade.

        Any override becomes a per-partition obligation in perpetuity that the next
        horizon-extension migration will forget.
        """
        for key in ("with_oids", "postgresql_with", "postgresql_fillfactor"):
            assert TABLE.kwargs.get(key) is None


def _hourly_migration():
    """Imported lazily so a broken migration fails only the projection tests, not the ledger contract."""
    return importlib.import_module("external.alembic.versions.c7f15537b2c3_create_litellm_spend_hourly")


def _create_index_sql(index: sa.Index) -> str:
    ddl = sa.schema.CreateIndex(index).compile(dialect=PG, compile_kwargs={"literal_binds": True})
    return str(ddl).replace("\n", " ")


class TestTheHourlyProjectionMirrorsItsMigration:
    """`litellm_spend_hourly` is created by migration c7f15537b2c3 and only mirrored by the model.

    Projected reads compile against the model, so a column or key the model names differently
    from the migration compiles cleanly here and fails only against a live database. The shape
    is therefore asserted literally, and the key and dimensions are tied to the migration's own
    frozen constants.
    """

    #: (column, type name as PostgreSQL renders it, nullable) - transcribed from the migration.
    COLUMNS: tuple[tuple[str, str, bool], ...] = (
        ("hour", "TIMESTAMP WITH TIME ZONE", False),
        ("attr_source", "TEXT", False),
        ("attr_form", "TEXT", False),
        ("attr_user_id", "TEXT", True),
        ("attr_principal", "TEXT", True),
        ("attr_project", "TEXT", True),
        ("attr_category", "TEXT", True),
        ("codemie_origin", "TEXT", True),
        ("call_type", "TEXT", True),
        ("codemie_workflow_id", "TEXT", True),
        ("model_group", "TEXT", True),
        ("end_user", "TEXT", True),
        ("spend", "NUMERIC", False),
        ("row_count", "BIGINT", False),
    )

    KEY_INDEX = "ux_spend_hourly_key"
    KEY_DDL = (
        "CREATE UNIQUE INDEX ux_spend_hourly_key ON litellm_spend_hourly (hour, attr_source, attr_form, "
        "coalesce('v' || attr_user_id, 'n'), coalesce('v' || attr_principal, 'n'), "
        "coalesce('v' || attr_project, 'n'), coalesce('v' || attr_category, 'n'), "
        "coalesce('v' || codemie_origin, 'n'), coalesce('v' || call_type, 'n'), "
        "coalesce('v' || codemie_workflow_id, 'n'), coalesce('v' || model_group, 'n'), "
        "coalesce('v' || end_user, 'n'))"
    )

    #: name -> (columns, partial predicate): the ledger's P1, P2, P3 and P6 at hourly grain.
    READ_INDEXES = {
        "ix_spend_hourly_user_time": (["attr_user_id", "hour"], "attr_user_id IS NOT NULL"),
        "ix_spend_hourly_project_time": (["attr_project", "attr_category", "hour"], "attr_project IS NOT NULL"),
        "ix_spend_hourly_principal_time": (["attr_principal", "hour"], "attr_principal IS NOT NULL"),
        "ix_spend_hourly_workflow_time": (["codemie_workflow_id", "hour"], "codemie_workflow_id IS NOT NULL"),
    }

    def _index(self, name: str) -> sa.Index:
        return next(index for index in LiteLLMSpendHourly.indexes if index.name == name)

    def test_the_table_is_named_as_the_migration_creates_it(self):
        assert LiteLLMSpendHourly.name == "litellm_spend_hourly"

    def test_every_column_matches_the_migration_in_order(self):
        """A renamed, retyped or loosened column compiles here and fails only against a live database."""
        actual = tuple(
            (c.name, str(c.type.compile(dialect=PG)).upper(), c.nullable) for c in LiteLLMSpendHourly.columns
        )
        assert actual == self.COLUMNS

    def test_spend_is_unconstrained_numeric(self):
        """A projected total must equal the ledger's exact sum; a scale would round every hour's bucket."""
        spend = LiteLLMSpendHourly.c.spend.type
        assert isinstance(spend, sa.Numeric)
        assert not isinstance(spend, sa.Float)
        assert spend.precision is None, "spend must be unconstrained numeric"
        assert spend.scale is None, "a scale would round money at the column"

    def test_there_is_no_primary_key(self):
        """Eight key dimensions are nullable, which a primary key forbids; identity is the unique key index."""
        assert list(LiteLLMSpendHourly.primary_key.columns) == []

    def test_the_projected_dimensions_are_the_migrations(self):
        """Projected reads group by these names; one the migration lacks is a column that does not exist."""
        assert PROJECTED_DIMENSIONS == _hourly_migration().DIMENSIONS

    def test_only_the_key_index_is_unique(self):
        """A unique read index would reject legitimate buckets, e.g. two categories of one user in one hour."""
        assert [i.name for i in LiteLLMSpendHourly.indexes if i.unique] == [self.KEY_INDEX]

    def test_the_key_index_compiles_to_the_designed_ddl(self):
        """`coalesce('v' || c, 'n')` keeps NULL, '' and 'x' distinct; a bare nullable column lets NULL keys repeat."""
        assert _create_index_sql(self._index(self.KEY_INDEX)) == self.KEY_DDL

    def test_the_key_index_expressions_are_the_migrations_key(self):
        """The trigger's `ON CONFLICT (KEY)` infers this index, so the model must mirror exactly that key.

        The migration wraps each coalesce in the parentheses CREATE INDEX requires around an
        expression; the compiler omits them, so only those are stripped before comparing.
        """
        ddl = _create_index_sql(self._index(self.KEY_INDEX))
        model_key = ddl[ddl.index("(") + 1 : -1]
        migration_key = _hourly_migration().KEY.replace("(coalesce(", "coalesce(").replace("'n'))", "'n')")
        assert model_key == migration_key

    def test_the_read_indexes_are_partial_on_their_leading_column(self):
        """Each ledger write upserts here and pays for every index, so none is added on a hunch.

        With the key index pinned as the only unique one, this exact set is the whole index set.
        A NULL holder or workflow is never looked up, so indexing those rows only adds write cost.
        """
        actual = {
            index.name: ([c.name for c in index.columns], str(index.dialect_options["postgresql"]["where"]))
            for index in LiteLLMSpendHourly.indexes
            if not index.unique
        }
        assert actual == self.READ_INDEXES

    def test_the_read_indexes_are_the_migrations(self):
        """The model's read indexes must be the ones the migration builds, column for column."""
        expected = {
            name: (columns, f"{leading} IS NOT NULL") for name, columns, leading in _hourly_migration().READ_INDEXES
        }
        actual = {
            index.name: (", ".join(c.name for c in index.columns), str(index.dialect_options["postgresql"]["where"]))
            for index in LiteLLMSpendHourly.indexes
            if not index.unique
        }
        assert actual == expected
