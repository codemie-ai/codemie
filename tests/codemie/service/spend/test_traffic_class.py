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

"""The four traffic classes, and the three substitutions that would empty a chart silently.

Each test names a predicate that looks right and is wrong. All three failures draw a *shorter line*
rather than an error, which is why they are pinned as compiled SQL rather than inferred from a figure.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.dialects import postgresql

from codemie.repository.spend_logs_repository import SpendLogsRepository
from codemie.service.spend.coverage import TimeRange
from codemie.service.spend.traffic_class import (
    ORIGINS_BY_CLASS,
    UNION_CLASSES,
    TrafficClass,
    TrafficOrigin,
    traffic_class_predicate,
    unclassified_predicate,
)

PERIOD = TimeRange(datetime(2026, 6, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc))

#: The column qualifier every rendered fragment carries, needed wherever a pin spans two of them.
TABLE = "litellm_spend_logs"

#: What selects each class on a row that named no origin - the pre-origin selectors, verbatim.
NATIVE: dict[TrafficClass, str] = {
    TrafficClass.CLI: "attr_category = 'cli'",
    TrafficClass.WORKFLOW: "codemie_workflow_id IS NOT NULL",
    TrafficClass.DATASOURCE: "call_type IN ('embedding', 'aembedding')",
    TrafficClass.ASSISTANT: "codemie_request_id IN ('req-a')",
}


def literals(values) -> str:
    """The values as an `IN` list renders them, so a pin can name a whole origin set at once."""
    return ", ".join(f"'{value}'" for value in sorted(values))


def sql(clause) -> str:
    return str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).replace("\n", " ")


async def rendered(read, *args) -> str:
    """The SQL one repository read would issue, with no database behind it."""
    captured = {}

    class _Session:
        async def execute(self, statement):
            captured["sql"] = sql(statement)

            class _R:
                def one(self_inner):
                    return [0] * (len(TrafficClass) + 1)

                def all(self_inner):
                    return []

            return _R()

    await read(_Session(), *args)
    return captured["sql"]


def class_filters(rendered_sql: str) -> set[str]:
    """The `FILTER (WHERE ...)` predicates a class-split read selected on, as a set.

    Compared between the two grains rather than trusted to the shared helper that builds them,
    because the guarantee has to survive somebody inlining that helper back into either method.
    """
    return {fragment.split("), 0) AS")[0] for fragment in rendered_sql.split("FILTER (WHERE ")[1:]}


class TestTheNativeClasses:
    def test_cli_keys_on_attr_category_and_never_attr_channel(self) -> None:
        """`attr_channel` is NULL on every post-rollout row, so a CLI bucket keyed on it empties as the
        rollout proceeds - and an empty bucket reads as no CLI spend."""
        rendered = sql(traffic_class_predicate(TrafficClass.CLI))
        assert "attr_category = 'cli'" in rendered
        assert "attr_channel" not in rendered

    def test_workflow_keys_on_the_column_being_populated(self) -> None:
        assert "codemie_workflow_id IS NOT NULL" in sql(traffic_class_predicate(TrafficClass.WORKFLOW))

    def test_datasource_keys_on_the_call_type_the_provider_populates(self) -> None:
        """The class was a permanent measured zero while it resolved through correlation ids: no
        datasource document carries one, so its id set was empty in every window over real money."""
        rendered = sql(traffic_class_predicate(TrafficClass.DATASOURCE))
        assert "call_type IN ('embedding', 'aembedding')" in rendered
        assert "codemie_request_id" not in rendered

    def test_no_native_class_consults_the_id_set(self) -> None:
        """They have their own column; taking ids into account would shrink them to the resolved subset."""
        for cls in (TrafficClass.CLI, TrafficClass.WORKFLOW, TrafficClass.DATASOURCE):
            assert sql(traffic_class_predicate(cls)) == sql(traffic_class_predicate(cls, ("req-1", "req-2")))


class TestTheUnionClass:
    """One class still resolves through the metric store, and the table says which."""

    def test_only_the_assistant_class_is_declared_as_needing_the_metric_store(self) -> None:
        """Datasource left this table when it became native. Leaving it here would keep a residue
        being read for a class whose selector no longer has an unresolved remainder."""
        assert set(UNION_CLASSES) == {TrafficClass.ASSISTANT}

    def test_the_id_set_is_its_whole_selector(self) -> None:
        rendered = sql(traffic_class_predicate(TrafficClass.ASSISTANT, ("req-1",)))
        assert "codemie_request_id IN ('req-1')" in rendered

    def test_the_assistant_class_over_no_ids_matches_nothing_and_never_everything(self) -> None:
        """The dangerous inversion: a class with no ids must contribute zero, never the whole table.

        With no ids the id branch is `false()` and drops out whole, leaving the rows that named an
        assistant origin - and nothing else. An implementation turning the empty branch into *no
        term* rather than *no rows* would sum every origin-less row in the window into one series.
        """
        rendered = sql(traffic_class_predicate(TrafficClass.ASSISTANT))
        assert rendered.startswith("litellm_spend_logs.codemie_origin IN (")
        assert "codemie_request_id" not in rendered, "no ids, so the id branch names no correlation id"
        assert "codemie_origin IS NULL" not in rendered, "the origin-less branch selects nothing without ids"
        assert "IS NOT NULL" not in rendered


class TestTheUnclassifiedResidue:
    """The complement of the four selectors, and every NULL it must admit.

    Its failure is the quietest one here: written as `NOT (a OR b OR c OR d)`, an unattributed row
    makes the disjunction NULL, the negation NULL, and a `WHERE` admits no NULL - so the very rows
    this residue exists to count fall out of it and the chart reports a complete window.
    """

    def test_every_conjunct_admits_the_null_it_must(self) -> None:
        rendered = sql(unclassified_predicate(("req-a",)))

        assert "attr_category IS DISTINCT FROM 'cli'" in rendered
        assert "codemie_workflow_id IS NULL" in rendered
        assert "call_type IS NULL OR" in rendered
        assert "codemie_request_id IS NULL OR" in rendered

    def test_an_empty_id_set_leaves_the_correlation_conjunct_always_true(self) -> None:
        """Nothing resolved, so no row is excluded on that ground - and the other three conjuncts are
        what still keeps the CLI, workflow and embedding rows out of the residue."""
        rendered = sql(unclassified_predicate(()))

        assert "codemie_request_id" not in rendered, "an always-true conjunct drops out, which is the truth here"
        assert "attr_category IS DISTINCT FROM 'cli'" in rendered
        assert "codemie_workflow_id IS NULL" in rendered
        assert "call_type IS NULL OR" in rendered

    def test_a_populated_id_set_excludes_exactly_what_it_resolved(self) -> None:
        rendered = sql(unclassified_predicate(("a",)))

        assert "codemie_request_id NOT IN ('a')" in rendered
        assert "1 = 1" not in rendered


class TestEachSelectorReadsTheOriginFirst:
    """The origin branch per class, and the native branch it may not reach past.

    A row that says what it was for is classified by what it said; a row that says nothing is
    classified by its columns exactly as it was before the column existed. The two failures here are
    both silent: an origin set that leaked a neighbouring class's word doubles that money across two
    series, and a native branch left unguarded claims a row whose sender already filed it elsewhere.
    """

    @pytest.mark.parametrize("traffic_class", list(TrafficClass))
    def test_a_class_selects_exactly_the_origins_filed_under_it(self, traffic_class) -> None:
        rendered = sql(traffic_class_predicate(traffic_class, ("req-a",)))
        mine = {origin.value for origin in ORIGINS_BY_CLASS[traffic_class]}

        assert f"codemie_origin IN ({literals(mine)})" in rendered

    @pytest.mark.parametrize("traffic_class", list(TrafficClass))
    def test_no_class_borrows_another_classes_origin(self, traffic_class) -> None:
        """One origin in two selectors is one row's money drawn in two series, both plausible."""
        rendered = sql(traffic_class_predicate(traffic_class, ("req-a",)))
        others = {
            origin.value
            for other, origins in ORIGINS_BY_CLASS.items()
            for origin in origins
            if other is not traffic_class
        }

        for value in others:
            assert f"'{value}'" not in rendered, value

    @pytest.mark.parametrize("traffic_class", list(TrafficClass))
    def test_the_native_branch_is_reached_only_where_no_origin_was_sent(self, traffic_class) -> None:
        """`IS NULL` is what keeps the branches exclusive: an unguarded native branch would claim a
        row that named a different class, and the two series would both count it."""
        rendered = sql(traffic_class_predicate(traffic_class, ("req-a",)))

        assert f"codemie_origin IS NULL AND {TABLE}.{NATIVE[traffic_class]}" in rendered

    @pytest.mark.parametrize("traffic_class", list(TrafficClass))
    def test_a_row_with_no_origin_is_classified_exactly_as_it_was_before(self, traffic_class) -> None:
        """The regression pin. Every row written before the sending release carries no origin, so the
        four native selectors are still the whole of how that traffic is bucketed."""
        assert NATIVE[traffic_class] in sql(traffic_class_predicate(traffic_class, ("req-a",)))

    @pytest.mark.parametrize("traffic_class", list(TrafficClass))
    def test_a_proxied_non_cli_call_is_claimed_by_no_class(self, traffic_class) -> None:
        """`proxy` is the one origin filed under nothing, and it lands in the residue as it does today."""
        assert f"'{TrafficOrigin.PROXY.value}'" not in sql(traffic_class_predicate(traffic_class, ("req-a",)))


class TestAnOriginWordThisVocabularyDoesNotHold:
    """A value no class claims is unclassified **even where a native column would have claimed it**.

    The sender said what the request was for. Re-reading `codemie_workflow_id` or `call_type` behind a
    word we do not recognise would file the row under a class its sender did not name and the defect
    would never surface - the residue exists to make exactly that visible, so an unknown word belongs
    in it rather than in a class chosen by a column.
    """

    @staticmethod
    def _origin_disjunct(resolved_ids: tuple[str, ...] = ("req-a",)) -> str:
        """The half of the residue that judges a row on its origin alone."""
        return sql(unclassified_predicate(resolved_ids)).split(f" OR {TABLE}.codemie_origin IS NULL")[0]

    def test_it_is_selected_on_the_origin_alone_with_no_native_column_consulted(self) -> None:
        disjunct = self._origin_disjunct()

        assert f"{TABLE}.codemie_origin IS NOT NULL" in disjunct
        for column in ("attr_category", "codemie_workflow_id", "call_type", "codemie_request_id"):
            assert column not in disjunct, column

    def test_every_origin_some_class_claims_is_excluded_from_it(self) -> None:
        """The list is the four classes' sets unioned; an origin missing from it would be counted
        twice - once in its own series and once again in the residue drawn beside it."""
        claimed = {origin.value for origins in ORIGINS_BY_CLASS.values() for origin in origins}

        assert f"codemie_origin NOT IN ({literals(claimed)})" in self._origin_disjunct()

    def test_a_proxied_non_cli_call_is_the_known_word_that_lands_here(self) -> None:
        assert f"'{TrafficOrigin.PROXY.value}'" not in self._origin_disjunct()

    def test_the_native_complement_stays_under_the_null_origin_guard(self) -> None:
        """Otherwise a row that named its class would be counted in that class and in the residue."""
        rendered = sql(unclassified_predicate(("req-a",)))
        native_half = rendered.split(f" OR {TABLE}.codemie_origin IS NULL AND ")[1]

        assert "attr_category IS DISTINCT FROM 'cli'" in native_half
        assert "codemie_workflow_id IS NULL" in native_half
        assert "call_type IS NULL OR" in native_half
        assert "codemie_request_id IS NULL OR" in native_half


class TestTheBucketedRead:
    async def _run(self, width: str = "week"):
        captured = {}

        class _Session:
            async def execute(self, statement):
                captured["sql"] = sql(statement)

                class _R:
                    def all(self_inner):
                        return []

                return _R()

        await SpendLogsRepository().sum_by_time_bucket(_Session(), PERIOD, width)
        return captured["sql"]

    @pytest.mark.asyncio
    async def test_it_is_one_statement_grouping_on_the_time_bucket(self) -> None:
        rendered = await self._run()
        assert "date_bin(" in rendered
        assert "GROUP BY" in rendered

    @pytest.mark.asyncio
    async def test_every_class_gets_its_own_filtered_aggregate(self) -> None:
        """One scan, four figures - and each filter is the same predicate the single-class form uses."""
        rendered = await self._run()
        assert rendered.count("FILTER (WHERE") == len(TrafficClass)
        assert "attr_category = 'cli'" in rendered
        assert "codemie_workflow_id IS NOT NULL" in rendered

    @pytest.mark.asyncio
    async def test_the_range_predicate_is_still_the_half_open_one(self) -> None:
        """A bucketed read is not exempt from the time bound; an unbounded scan of this table is invalid."""
        rendered = await self._run()
        assert "start_time >=" in rendered and "start_time <" in rendered


class TestTheWholeRangeClassRead:
    """The same read without the time grouping - one figure per class over one window.

    Pinned as SQL beside its bucketed sibling because the failure being guarded is that the two
    *diverge*: a window total drawn from a filter list maintained separately would disagree with the
    chart printed next to it, and both numbers would look right on their own.
    """

    async def _run(self, resolved_ids=None):
        return await rendered(SpendLogsRepository().sum_for_traffic_classes, PERIOD, resolved_ids)

    @pytest.mark.asyncio
    async def test_it_is_one_statement_with_no_grouping_at_all(self) -> None:
        """There is nothing to group by: one filtered aggregate per class over the range is one row."""
        rendered = await self._run()
        assert "GROUP BY" not in rendered
        assert "date_bin" not in rendered

    @pytest.mark.asyncio
    async def test_every_class_gets_its_own_filtered_aggregate(self) -> None:
        rendered = await self._run()
        assert rendered.count("FILTER (WHERE") == len(TrafficClass)
        assert "attr_category = 'cli'" in rendered
        assert "codemie_workflow_id IS NOT NULL" in rendered

    @pytest.mark.asyncio
    async def test_the_range_predicate_is_still_the_half_open_one(self) -> None:
        rendered = await self._run()
        assert "start_time >=" in rendered and "start_time <" in rendered
        assert "BETWEEN" not in rendered.upper()

    @pytest.mark.asyncio
    async def test_a_class_absent_from_the_id_set_matches_nothing_rather_than_everything(self) -> None:
        """The dangerous inversion, at the plumbing layer: an absent mapping entry is an empty id
        set, never "no filter for this class" - which would sum the whole table into it."""
        rendered = await self._run({TrafficClass.ASSISTANT: ("req-a",)})
        assert "codemie_request_id IN ('req-a')" in rendered

        no_ids = await self._run({})
        assert "codemie_request_id" not in no_ids, "the assistant class's id branch must match nothing"
        assert "call_type IN ('embedding', 'aembedding')" in no_ids, "the datasource class is native and unaffected"

    @pytest.mark.asyncio
    async def test_its_class_filters_are_the_bucketed_read_own_filters(self) -> None:
        """The assertion that stops the drift: same filters, so the two grains cannot disagree.

        Compared as SQL rather than trusted to a shared helper, because the guarantee must survive
        somebody inlining the helper back into either method.
        """
        ids = {TrafficClass.ASSISTANT: ("req-a",)}
        repository = SpendLogsRepository()
        whole_range = class_filters(await rendered(repository.sum_for_traffic_classes, PERIOD, ids))
        bucketed = class_filters(await rendered(repository.sum_by_time_bucket, PERIOD, "week", ids))

        assert whole_range == bucketed
        assert len(whole_range) == len(TrafficClass)
