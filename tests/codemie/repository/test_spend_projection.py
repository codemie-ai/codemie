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

"""How a windowed ledger read renders over raw rows or over the hourly projection, pinned as compiled SQL.

Each failure guarded here is silent. A period split into whole hours and edges that overlap or leave a
gap counts money twice or not at all. A predicate translated only partly onto the projection either
compares the wrong rows or pulls the ledger back in as an implicit cross join. And a metric-store id set
bound one parameter per id is refused by asyncpg past 32,767 ids. No database is needed: the exactness
of the figures is pinned by the database oracle, these tests pin the statements' shape.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import and_, column, exists, literal_column, not_, or_, select, table
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import asyncpg

from codemie.repository import spend_projection
from codemie.repository.spend_logs_model import LiteLLMSpendLog as Row
from codemie.repository.spend_logs_repository import _id_free, predicate_for_holder
from codemie.repository.spend_projection import (
    _HOURLY_COLUMNS,
    Aggregate,
    Measure,
    ReadShape,
    ShapeOf,
    _onto,
    _Untranslatable,
    identities_statement,
    projected_statement,
    raw_edges,
    raw_statement,
    read_statement,
    whole_hours,
)
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.spend.coverage import TimeRange
from codemie.service.spend.ledger import PersonalHolder, ProjectHolder, ProjectMemberHolder, bucket_origin
from codemie.service.spend.traffic_class import request_resolved, request_unresolved

IDS = ("a", "b")


def at(hour: int, minute: int = 0) -> datetime:
    """One UTC instant on the fixture day."""
    return datetime(2026, 8, 1, hour, minute, tzinfo=timezone.utc)


GROUP_BY = ("attr_user_id", "attr_project")
ALIGNED = TimeRange(at(10), at(13))
UNALIGNED = TimeRange(at(10, 30), at(14, 15))


def sql(clause) -> str:
    """The clause as PostgreSQL would see it, with literals inlined so it can be asserted on."""
    return str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).replace("\n", " ")


def id_bearing_read(ids: tuple[str, ...]) -> ShapeOf:
    """A read with a total, a matched and a residue column, built the way the metric-store reads build it."""

    def shape_of(applied: bool) -> ReadShape:
        applied_ids = ids if applied else ()
        return ReadShape(
            where=Row.attr_project.in_(("p1", "p2")),
            aggregates=(
                Aggregate(label="total"),
                Aggregate(Measure.ROWS, request_resolved(applied_ids), "matched_rows"),
                Aggregate(selector=request_unresolved(applied_ids), label="residue"),
            ),
        )

    return shape_of


def fixed(shape: ReadShape) -> ShapeOf:
    """A read whose shape applies no metric-store id set, so both images are the same."""
    return lambda _applied: shape


class TestWholeHoursAndRawEdgesTileThePeriod:
    def test_an_aligned_period_is_all_whole_hours_with_no_edges(self):
        """An aligned period is answered from the projection alone - no raw row is read."""
        assert whole_hours(ALIGNED) == ALIGNED
        assert raw_edges(ALIGNED, ALIGNED) == []

    def test_an_unaligned_period_has_one_edge_at_each_end(self):
        """The edges and the whole hours meet exactly, so no row is counted twice and none falls between."""
        hours = whole_hours(UNALIGNED)

        assert hours == TimeRange(at(11), at(14))
        assert raw_edges(UNALIGNED, hours) == [TimeRange(at(10, 30), at(11)), TimeRange(at(14), at(14, 15))]

    def test_a_sub_hour_period_has_no_whole_hour(self):
        """Raw rows are then the whole answer, so there is nothing for the projection to add."""
        assert whole_hours(TimeRange(at(10, 10), at(10, 50))) is None

    def test_an_hour_long_period_straddling_a_boundary_has_no_whole_hour(self):
        """An hour's length is not an hour of the projection: 10:30-11:30 holds no aligned hour."""
        assert whole_hours(TimeRange(at(10, 30), at(11, 30))) is None

    def test_exactly_one_aligned_hour_is_one_whole_hour(self):
        """The smallest period the projection can answer is one projection row's hour."""
        period = TimeRange(at(10), at(11))

        assert whole_hours(period) == period
        assert raw_edges(period, period) == []

    def test_a_future_ended_period_is_not_clamped_to_the_clock(self):
        """No clock is consulted: the projection is written in the ledger's own transaction, so hours
        still to come are read from it like any past hour - they simply hold no rows yet."""
        this_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        period = TimeRange(this_hour - timedelta(hours=2), this_hour + timedelta(hours=3, minutes=20))
        hours = whole_hours(period)

        assert hours == TimeRange(this_hour - timedelta(hours=2), this_hour + timedelta(hours=3))
        assert raw_edges(period, hours) == [TimeRange(this_hour + timedelta(hours=3), period.end)]


class TestOntoTranslatesAPredicateWholeOrNotAtAll:
    def test_ledger_columns_are_replaced_by_the_mapped_columns(self):
        """A ledger column left behind would make the projected branch an implicit cross join."""
        translated = sql(_onto(and_(Row.attr_project == "p1", Row.attr_category == "platform"), _HOURLY_COLUMNS))

        assert (
            translated == "litellm_spend_hourly.attr_project = 'p1' AND litellm_spend_hourly.attr_category = 'platform'"
        )
        assert "litellm_spend_logs" not in translated

    def test_a_ledger_column_the_mapping_lacks_raises(self):
        """The projection carries no run id; dropping the conjunct instead would widen the read."""
        with pytest.raises(_Untranslatable, match="litellm_spend_logs.codemie_run_id"):
            _onto(and_(Row.attr_user_id == "u1", Row.codemie_run_id == "r1"), _HOURLY_COLUMNS)

    def test_a_column_of_another_table_raises(self):
        """Only ledger columns have a counterpart on the projection; any other table's would be joined in."""
        users = table("users", column("id"))

        with pytest.raises(_Untranslatable, match="users.id"):
            _onto(and_(Row.attr_user_id == "u1", users.c.id == "u1"), _HOURLY_COLUMNS)

    @pytest.mark.parametrize("columns", [_HOURLY_COLUMNS, None], ids=["onto the projection", "kept on the ledger"])
    def test_a_subquery_raises(self, columns):
        """A subquery reads its own FROM, which no column mapping reaches: kept, it would pull the ledger into
        a projected branch; and the raw branches, which map nothing, would otherwise pass it through unseen."""
        with pytest.raises(_Untranslatable, match="subquery"):
            _onto(and_(Row.attr_project == "p1", exists(select(Row.attr_project))), columns)

    @pytest.mark.parametrize("columns", [_HOURLY_COLUMNS, None], ids=["onto the projection", "kept on the ledger"])
    @pytest.mark.parametrize("tableless", [column("x"), literal_column("x")], ids=["column", "literal_column"])
    def test_a_column_of_no_table_raises(self, columns, tableless):
        """A bare name binds to whatever the FROM in scope offers, so it would read a different row's value in
        each branch of the window - or a column the projection only carries as a sum or an hour."""
        with pytest.raises(_Untranslatable, match="column x of no table"):
            _onto(and_(Row.attr_project == "p1", tableless == "y"), columns)

    def test_request_ids_are_collected_only_from_correlation_id_value_lists(self):
        """These are the ids the resolved image tests; a project value list gathered with them would hide an
        applied id that `resolved_ids` misses, and a `NOT IN` left out would hide the residue's own ids."""
        applied: set[str] = set()

        _onto(
            and_(
                Row.attr_project.in_(("p1", "p2")),
                Row.codemie_request_id.in_(("a", "b")),
                Row.codemie_request_id.not_in(("c",)),
            ),
            request_ids=applied,
        )

        assert applied == {"a", "b", "c"}

    def test_nested_boolean_structure_survives_translation(self):
        """Only the columns change: every connective, NULL-safe comparison and escaped prefix is kept."""
        clause = and_(
            or_(Row.attr_source == "explicit", not_(Row.attr_form == "principal")),
            Row.codemie_origin.is_distinct_from("cli"),
            not_(Row.attr_user_id.startswith("p_", autoescape=True)),
        )

        translated = sql(_onto(clause, _HOURLY_COLUMNS))

        assert translated == sql(clause).replace("litellm_spend_logs.", "litellm_spend_hourly.")
        assert "IS DISTINCT FROM 'cli'" in translated
        assert "ESCAPE '/'" in translated

    @pytest.mark.parametrize(
        ("clause", "expected"),
        [
            (Row.attr_project.in_(("a", "b")), "litellm_spend_hourly.attr_project = ANY (ARRAY['a', 'b'])"),
            (Row.attr_project.not_in(("a", "b")), "litellm_spend_hourly.attr_project != ALL (ARRAY['a', 'b'])"),
        ],
    )
    def test_a_value_list_binds_as_one_array(self, clause, expected):
        """An expanding list binds one parameter per value; an array binds one however long the list."""
        assert sql(_onto(clause, _HOURLY_COLUMNS)) == expected

    def test_without_a_mapping_columns_stay_on_the_ledger_but_lists_become_arrays(self):
        """The raw correction branches read the ledger yet carry the id set, so they need the array too."""
        clause = and_(Row.attr_category == "platform", Row.codemie_request_id.in_(IDS))

        assert sql(_onto(clause)) == (
            "litellm_spend_logs.attr_category = 'platform' "
            "AND litellm_spend_logs.codemie_request_id = ANY (ARRAY['a', 'b'])"
        )


class TestProjectedStatementFallsBackToRawWhole:
    def test_a_period_without_a_whole_hour_is_answered_raw(self):
        """Raw rows answer such a period whole, so a projected statement would only add a union."""
        assert projected_statement(TimeRange(at(10, 10), at(10, 50)), id_bearing_read(())) is None

    def test_a_personal_holder_is_answered_from_the_projection(self):
        """A personal figure keys on the charged customer, which the projection carries as `end_user`."""
        holder = PersonalHolder("user-1", BudgetCategory.PLATFORM, None)
        shape = ReadShape(where=predicate_for_holder(holder), aggregates=(Aggregate(label="spent"),))

        statement = sql(projected_statement(ALIGNED, fixed(shape)))
        assert "FROM litellm_spend_hourly" in statement
        assert "(litellm_spend_hourly.end_user NOT LIKE 'codemie:project:' || '%%' ESCAPE '/')" in statement
        assert "litellm_spend_hourly.end_user != ALL (ARRAY['N/A', ''])" in statement

    @pytest.mark.parametrize(
        "holder",
        [
            ProjectHolder("acme", BudgetCategory.PLATFORM),
            ProjectMemberHolder("acme", BudgetCategory.PLATFORM, "user-1"),
        ],
        ids=["project", "member"],
    )
    def test_a_project_holder_is_answered_from_the_projection_without_no_customer_rows(self, holder):
        shape = ReadShape(where=predicate_for_holder(holder), aggregates=(Aggregate(label="spent"),))

        statement = sql(projected_statement(ALIGNED, fixed(shape)))
        assert "FROM litellm_spend_hourly" in statement
        assert "litellm_spend_hourly.end_user != ALL (ARRAY['N/A', ''])" in statement

    def test_a_named_execution_is_answered_raw(self):
        """A read keyed on one correlation id cannot be answered from rows that carry none."""
        shape = ReadShape(where=Row.codemie_request_id == "x", aggregates=(Aggregate(label="spent"),))

        assert projected_statement(ALIGNED, fixed(shape)) is None

    @pytest.mark.parametrize(
        ("step", "origin_offset"),
        [(timedelta(minutes=15), timedelta(0)), (timedelta(hours=1), timedelta(minutes=30))],
        ids=["quarter-hour step", "half-past origin"],
    )
    def test_a_width_not_made_of_whole_hours_is_answered_raw(self, monkeypatch, step, origin_offset):
        """A bucket boundary inside an hour would file a whole projection hour under one bucket while the raw
        rows of that hour belong to two, so every bucket on the chart would be off with nothing raising."""
        hour_origin = bucket_origin("hour")
        monkeypatch.setattr(spend_projection, "bucket_step", lambda _width: step)
        monkeypatch.setattr(spend_projection, "bucket_origin", lambda _width: hour_origin + origin_offset)

        assert projected_statement(UNALIGNED, id_bearing_read(IDS), IDS, "hour") is None


class TestProjectedStatementWithoutIds:
    def test_an_aligned_read_never_touches_the_ledger(self):
        """With no edge and no id set, every figure comes from the projection's whole hours."""
        rendered = sql(projected_statement(ALIGNED, id_bearing_read(())))

        assert "FROM litellm_spend_hourly" in rendered
        assert "litellm_spend_logs" not in rendered
        assert "UNION ALL" not in rendered

    def test_an_unaligned_read_adds_one_raw_branch_per_edge_and_no_correction(self):
        """An empty id set names no correlation id, so no matched row needs cancelling or re-adding."""
        rendered = sql(projected_statement(UNALIGNED, id_bearing_read(())))

        assert "FROM litellm_spend_hourly" in rendered
        assert rendered.count(" UNION ALL ") == 2
        assert "litellm_spend_logs.start_time >= '2026-08-01 10:30:00+00:00'" in rendered
        assert "litellm_spend_logs.start_time < '2026-08-01 14:15:00+00:00'" in rendered
        assert "-litellm_spend_logs.spend" not in rendered
        assert "true AS resolved" not in rendered


def ledger_range(start: str, end: str) -> str:
    """The half-open bounds a raw branch carries, from `start` to `end` on the fixture day."""
    return (
        f"litellm_spend_logs.start_time >= '2026-08-01 {start}:00+00:00' "
        f"AND litellm_spend_logs.start_time < '2026-08-01 {end}:00+00:00'"
    )


def where_of(branch: str) -> str:
    """A union branch's own WHERE clause: every branch has exactly one, and the last closes the window."""
    return branch.split(" WHERE ", 1)[1].removesuffix(") AS window_rows").strip()


LEDGER_PROJECTS = "litellm_spend_logs.attr_project = ANY (ARRAY['p1', 'p2'])"
MATCHED_IDS = "litellm_spend_logs.codemie_request_id = ANY (ARRAY['a', 'b'])"


class TestProjectedStatementWithIds:
    """Each union branch pinned to the time range and image it reads, for a 10:30-14:15 period.

    A projection branch one hour too long, or a correction pair bounded by the whole hours instead of the
    period, still returns plausible figures - only the exact bounds tell the double count apart.
    """

    @pytest.fixture
    def branches(self) -> list[str]:
        return sql(projected_statement(UNALIGNED, id_bearing_read(IDS), IDS)).split(" UNION ALL ")

    def test_the_window_is_five_branches(self, branches):
        """Whole hours, two edges, then the cancelled and the resolved image of the matched rows."""
        assert len(branches) == 5

    def test_the_projection_branch_reads_exactly_the_whole_hours(self, branches):
        """Hour 14 read here as well as by the 14:00-14:15 edge would count its first quarter twice."""
        assert "FROM litellm_spend_hourly" in branches[0]
        assert where_of(branches[0]) == (
            "litellm_spend_hourly.hour >= '2026-08-01 11:00:00+00:00' "
            "AND litellm_spend_hourly.hour < '2026-08-01 14:00:00+00:00' "
            "AND litellm_spend_hourly.attr_project = ANY (ARRAY['p1', 'p2'])"
        )

    @pytest.mark.parametrize(
        ("index", "start", "end"), [(1, "10:30", "11:00"), (2, "14:00", "14:15")], ids=["leading", "trailing"]
    )
    def test_each_edge_branch_reads_exactly_its_partial_hour_unresolved(self, branches, index, start, end):
        """An edge is the only source of its sub-hour rows, counted once, under the unresolved shape."""
        branch = branches[index]

        assert branch.startswith("SELECT litellm_spend_logs.spend AS spend, 1 AS row_count, ")
        assert "CAST(NULL AS TEXT) AS codemie_request_id, false AS resolved  FROM litellm_spend_logs " in branch
        assert where_of(branch) == f"{ledger_range(start, end)} AND {LEDGER_PROJECTS}"

    def test_the_cancelled_branch_subtracts_the_unresolved_image_over_the_whole_period(self, branches):
        """A matched row was counted unresolved by whichever branch covers its instant - projection or edge -
        so its cancellation must span the full period, not only the whole hours."""
        branch = branches[3]

        assert branch.startswith("SELECT -litellm_spend_logs.spend AS spend, -1 AS row_count, ")
        assert "CAST(NULL AS TEXT) AS codemie_request_id, false AS resolved  FROM litellm_spend_logs " in branch
        assert where_of(branch) == f"{ledger_range('10:30', '14:15')} AND {MATCHED_IDS} AND {LEDGER_PROJECTS}"

    def test_the_resolved_branch_re_adds_the_same_rows_with_their_correlation_id(self, branches):
        """The same rows as the cancelled branch, so each matched row nets to exactly its resolved image."""
        branch = branches[4]

        assert branch.startswith("SELECT litellm_spend_logs.spend AS spend, 1 AS row_count, ")
        assert "litellm_spend_logs.codemie_request_id AS codemie_request_id, true AS resolved  FROM " in branch
        assert where_of(branch) == f"{ledger_range('10:30', '14:15')} AND {MATCHED_IDS} AND {LEDGER_PROJECTS}"

    def test_each_filter_judges_an_image_by_its_own_selector(self, branches):
        """Swapped, the residue would keep every resolved row and the matched count would read unresolved
        rows by an id they do not carry. The unresolved images here are `true` and `false`, which fold."""
        aggregates = branches[0].split(" FROM (", 1)[0].strip()

        assert aggregates == (
            "SELECT coalesce(sum(window_rows.spend), 0) AS total, "
            "coalesce(sum(window_rows.row_count) FILTER (WHERE false OR window_rows.resolved "
            "AND window_rows.codemie_request_id = ANY (ARRAY['a', 'b'])), 0) AS matched_rows, "
            "coalesce(sum(window_rows.spend) FILTER (WHERE NOT window_rows.resolved OR window_rows.resolved "
            "AND (window_rows.codemie_request_id IS NULL "
            "OR window_rows.codemie_request_id != ALL (ARRAY['a', 'b']))), 0) AS residue"
        )

    def test_the_projection_branch_names_no_ledger_column(self):
        """A ledger column in the first branch would cross-join the ledger into every projection row."""
        first_branch = sql(projected_statement(UNALIGNED, id_bearing_read(IDS), IDS)).split(" UNION ALL ")[0]

        assert "FROM litellm_spend_hourly" in first_branch
        assert "litellm_spend_hourly.attr_project = ANY (ARRAY['p1', 'p2'])" in first_branch
        assert "litellm_spend_logs" not in first_branch

    def test_forty_thousand_ids_bind_as_a_handful_of_parameters(self):
        """asyncpg refuses a statement past 32,767 parameters; one array per id set keeps the count flat."""
        many_ids = tuple(f"request-{n}" for n in range(40_000))

        many = projected_statement(UNALIGNED, id_bearing_read(many_ids), many_ids).compile(dialect=asyncpg.dialect())
        two = projected_statement(UNALIGNED, id_bearing_read(IDS), IDS).compile(dialect=asyncpg.dialect())

        assert len(many.positiontup) < 30
        assert len(many.positiontup) == len(two.positiontup)


class TestProjectedStatementOutput:
    @pytest.mark.parametrize("width", ["3 hours", "hour", "day", "week"])
    def test_a_bucketed_read_drops_buckets_with_no_net_rows(self, width):
        """Raw grouping returns no bucket without a row, so a bucket left only by a cancelled pair must go."""
        rendered = sql(projected_statement(UNALIGNED, id_bearing_read(IDS), IDS, width))

        assert "GROUP BY date_bin(" in rendered
        assert "window_rows.start_time" in rendered.split("GROUP BY")[1]
        assert "HAVING sum(window_rows.row_count) > 0" in rendered
        assert rendered.endswith("ORDER BY bucket")

    @pytest.mark.parametrize("width", [None, "day"])
    def test_a_grouped_projected_read_groups_window_rows_and_drops_netted_groups(self, width):
        """A key group left only by a cancelled pair holds no row, and the raw read would not return it."""
        shape = ReadShape(where=None, aggregates=(Aggregate(label="total"),), group_by=GROUP_BY)

        statement = projected_statement(UNALIGNED, fixed(shape), (), width)
        rendered = sql(statement)

        assert list(statement.selected_columns.keys()) == [*(["bucket"] if width else []), *GROUP_BY, "total"]
        assert "window_rows.attr_user_id, window_rows.attr_project" in rendered.split("GROUP BY")[1]
        assert "HAVING sum(window_rows.row_count) > 0" in rendered
        assert rendered.endswith("ORDER BY bucket" if width else "> 0")

    def test_labels_and_column_order_follow_the_shape(self):
        """A caller reads either statement's rows the same way, so both must name the same columns."""
        projected = projected_statement(UNALIGNED, id_bearing_read(IDS), IDS, "day")
        raw = raw_statement(UNALIGNED, id_bearing_read(IDS)(True), "day")

        assert list(projected.selected_columns.keys()) == ["bucket", "total", "matched_rows", "residue"]
        assert list(projected.selected_columns.keys()) == list(raw.selected_columns.keys())

    @pytest.mark.parametrize(
        "second_when_applied",
        [
            (Aggregate(Measure.ROWS, label="rows"), Aggregate(selector=request_resolved(IDS), label="matched")),
            (Aggregate(label="rows"),),
            (Aggregate(Measure.ROWS, label="matched"),),
        ],
        ids=["an extra column", "another measure", "another label"],
    )
    def test_aggregates_that_depend_on_the_ids_are_refused(self, second_when_applied):
        """The two images are paired by position; a column that shifts with the ids, or changes what it counts
        or what it is called, would add one image's money to the other's rows under one label."""

        def shape_of(applied: bool) -> ReadShape:
            second = second_when_applied if applied else (Aggregate(Measure.ROWS, label="rows"),)
            return ReadShape(where=None, aggregates=(Aggregate(label="total"), *second))

        with pytest.raises(ValueError, match="must not depend on the metric store's ids"):
            projected_statement(UNALIGNED, shape_of, IDS)

    @pytest.mark.parametrize("resolved_ids", [("a",), ()], ids=["one id missing", "none given"])
    def test_ids_the_read_applies_beyond_resolved_ids_are_refused(self, resolved_ids):
        """A matched row outside `resolved_ids` is never cancelled and re-added, so it would be counted under
        its unresolved image - in the residue - while the raw read counts it as matched."""
        with pytest.raises(ValueError, match="resolved_ids must cover every correlation id the read applies"):
            projected_statement(UNALIGNED, id_bearing_read(IDS), resolved_ids)


class TestReadStatementPicksTheSource:
    def test_a_period_with_whole_hours_reads_the_projection(self):
        assert "FROM litellm_spend_hourly" in sql(read_statement(ALIGNED, id_bearing_read(IDS), IDS))

    def test_a_read_the_projection_cannot_answer_is_exactly_the_raw_statement(self):
        """A fallback is today's read, not merely one free of the projection."""
        no_whole_hour = TimeRange(at(10, 10), at(10, 50))

        assert sql(read_statement(no_whole_hour, id_bearing_read(IDS), IDS)) == sql(
            raw_statement(no_whole_hour, id_bearing_read(IDS)(True))
        )

    def test_each_image_is_built_once_when_the_projection_falls_back(self):
        """A projected attempt builds the resolved image; the raw fallback reuses it rather than building again."""
        built: list[bool] = []
        shape = ReadShape(where=Row.codemie_request_id == "x", aggregates=(Aggregate(label="spent"),))

        def counting(applied: bool) -> ReadShape:
            built.append(applied)
            return shape

        read_statement(ALIGNED, counting)

        assert sorted(built) == [False, True]


class TestRawStatement:
    def test_it_reads_only_the_ledger(self):
        """The raw read is the statement the repository always issued; it never consults the projection."""
        rendered = sql(raw_statement(UNALIGNED, id_bearing_read(IDS)(True)))

        assert "FROM litellm_spend_logs" in rendered
        assert "litellm_spend_hourly" not in rendered

    def test_a_width_groups_and_orders_by_the_bucket(self):
        """A chart read needs one row per bucket, in bucket order; a period read needs exactly one row."""
        bucketed = sql(raw_statement(UNALIGNED, id_bearing_read(IDS)(True), "day"))
        whole = sql(raw_statement(UNALIGNED, id_bearing_read(IDS)(True)))

        assert "GROUP BY date_bin(" in bucketed
        assert bucketed.endswith("ORDER BY bucket")
        assert "GROUP BY" not in whole

    @pytest.mark.parametrize("width", [None, "day"])
    def test_a_grouped_raw_read_groups_by_the_named_columns(self, width):
        """One statement answers every key: the key columns lead the aggregates and join the grouping."""
        shape = ReadShape(where=None, aggregates=(Aggregate(label="total"),), group_by=GROUP_BY)
        keys = "litellm_spend_logs.attr_user_id, litellm_spend_logs.attr_project"

        statement = raw_statement(UNALIGNED, shape, width)
        rendered = sql(statement)

        assert list(statement.selected_columns.keys()) == [*(["bucket"] if width else []), *GROUP_BY, "total"]
        if width is None:
            assert rendered.endswith(f"GROUP BY {keys}")
        else:
            assert rendered.endswith(f"{keys} ORDER BY bucket")
            assert "GROUP BY date_bin(" in rendered

    def test_a_read_without_group_keys_is_not_grouped(self):
        """No key names the one-row period read the repository has always issued."""
        shape = ReadShape(where=None, aggregates=(Aggregate(label="total"),), group_by=())

        assert " ".join(sql(raw_statement(ALIGNED, shape)).split()) == (
            "SELECT coalesce(sum(litellm_spend_logs.spend), 0) AS total FROM litellm_spend_logs "
            "WHERE litellm_spend_logs.start_time >= '2026-08-01 10:00:00+00:00' "
            "AND litellm_spend_logs.start_time < '2026-08-01 13:00:00+00:00'"
        )

    def test_a_selector_less_aggregate_is_bare(self):
        """`None` means every admitted row - never an always-true filter that could later be narrowed unseen."""
        shape = ReadShape(where=None, aggregates=(Aggregate(label="total"), Aggregate(Measure.ROWS, label="rows")))

        rendered = sql(raw_statement(ALIGNED, shape))

        assert rendered.startswith("SELECT coalesce(sum(litellm_spend_logs.spend), 0) AS total, count(*) AS rows ")
        assert "FILTER" not in rendered


class TestOneShapeForBothImages:
    """A read applying no metric-store id passes one shape as both images; the id pass is then pure cost.

    It traverses every predicate once more than the statement needs - for a hundred-holder batch the largest
    part of the build - to check ids the shape cannot apply on the projection anyway.
    """

    @pytest.fixture
    def id_passes(self, monkeypatch) -> list[int]:
        calls: list[int] = []
        translate = spend_projection._onto

        def counting(clause, columns=None, *, request_ids=None):
            if request_ids is not None:
                calls.append(1)
            return translate(clause, columns, request_ids=request_ids)

        monkeypatch.setattr(spend_projection, "_onto", counting)
        return calls

    def test_the_repositorys_id_free_read_is_projected_without_the_id_pass(self, id_passes):
        """Through `_id_free` and `read_statement`, the path every holder batch takes."""
        holder = PersonalHolder("user-1", BudgetCategory.PLATFORM, None)
        shape = ReadShape(where=predicate_for_holder(holder), aggregates=(Aggregate(label="spent"),))

        assert "FROM litellm_spend_hourly" in sql(read_statement(UNALIGNED, _id_free(shape)))
        assert id_passes == []

    def test_a_read_with_two_images_still_has_its_ids_checked(self, id_passes):
        projected_statement(UNALIGNED, id_bearing_read(IDS), IDS)

        assert id_passes

    def test_one_shape_adds_no_correction_branches_even_beside_resolved_ids(self):
        """Its two images are equal, so subtracting one and adding the other back would net to nothing."""
        shape = ReadShape(where=Row.attr_project.in_(("p1", "p2")), aggregates=(Aggregate(label="spent"),))

        rendered = sql(projected_statement(UNALIGNED, fixed(shape), IDS))

        assert rendered.count(" UNION ALL ") == 2
        assert MATCHED_IDS not in rendered

    def test_an_id_atom_in_one_shape_renders_the_read_raw(self):
        """Refused rather than projected: the projection carries no correlation id to test it against."""
        shape = ReadShape(where=request_resolved(IDS), aggregates=(Aggregate(label="spent"),))

        assert projected_statement(UNALIGNED, fixed(shape)) is None


class TestTheIdentitiesOfAPeriod:
    """The (user id, principal) pairs a period's rows carry, read before a batch prices its person holders.

    A pair missing here zeroes a holder that spent, so the whole period must be covered - every whole hour
    and both edges - and a superset is harmless: it only prices a holder the batch then sums to zero.
    """

    def test_without_the_projection_the_raw_rows_of_the_period_are_read(self):
        rendered = sql(identities_statement(UNALIGNED, projection=False))

        assert rendered.startswith("SELECT DISTINCT litellm_spend_logs.attr_user_id, litellm_spend_logs.attr_principal")
        assert "litellm_spend_hourly" not in rendered
        assert ledger_range("10:30", "14:15") in rendered

    def test_a_period_holding_no_whole_hour_is_read_raw(self):
        assert "litellm_spend_hourly" not in sql(identities_statement(TimeRange(at(10, 10), at(10, 50)), True))

    def test_whole_hours_come_from_the_projection_and_each_edge_from_raw_rows(self):
        rendered = sql(identities_statement(UNALIGNED, projection=True))
        branches = rendered.split(" UNION ALL ")

        assert rendered.startswith("SELECT DISTINCT window_identities.attr_user_id, window_identities.attr_principal")
        assert len(branches) == 3
        assert (
            "litellm_spend_hourly.hour >= '2026-08-01 11:00:00+00:00' AND litellm_spend_hourly.hour < '2026-08-01 14:00:00+00:00'"
            in branches[0]
        )
        assert ledger_range("10:30", "11:00") in branches[1]
        assert ledger_range("14:00", "14:15") in branches[2]
