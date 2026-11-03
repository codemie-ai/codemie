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

"""P10's id resolver: the Elasticsearch branch of AD-29's dimension union."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncio

import pytest

from codemie.service.spend.coverage import SpendDimension, TimeRange
from codemie.service.spend.dimension_scope import (
    resolve_metric_store_ids,
    resolve_metric_store_ids_by_value,
)
from codemie.service.spend.ledger import DimensionScope


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


PERIOD = TimeRange(utc(2026, 8, 1), utc(2026, 9, 1))


def _page(ids: list[str], after_key: dict | None = None) -> dict:
    aggregation: dict = {"buckets": [{"key": {"id": id_}, "doc_count": 1} for id_ in ids]}
    if after_key is not None:
        aggregation["after_key"] = after_key
    return {"aggregations": {"ids": aggregation}}


def _value_page(ids_by_value: dict[str, list[str]], after: dict | None = None) -> dict:
    """One page of the grouped composite: a bucket per (dimension value, correlation id) pair.

    `after` is the composite's own exhaustion signal, so a stub omitting it ends the paging loop -
    which is what a short page means to `resolve_metric_store_ids_by_value`.
    """
    buckets = [
        {"key": {"value": value, "id": one_id}, "doc_count": 1} for value, ids in ids_by_value.items() for one_id in ids
    ]
    aggregation: dict = {"buckets": buckets}
    if after is not None:
        aggregation["after_key"] = after
    return {"aggregations": {"pairs": aggregation}}


class _StubMetricsRepository:
    """A stand-in that only has to record the query bodies it was asked and hand back canned pages."""

    def __init__(self, pages: list[dict]) -> None:
        self._pages = list(pages)
        self.queries: list[dict] = []

    async def execute_aggregation_query(self, body: dict, *, request_timeout: int = 60) -> dict:
        self.queries.append(body)
        return self._pages.pop(0)


class TestFieldsPerDimension:
    """Which Elasticsearch field each dimension is looked up by, per AD-29's per-dimension table."""

    @pytest.mark.asyncio
    async def test_assistant_is_looked_up_by_assistant_id(self):
        repo = _StubMetricsRepository([_page(["req-1"])])
        ids = await resolve_metric_store_ids(repo, DimensionScope(SpendDimension.ASSISTANT, "asst-42"), PERIOD)
        assert ids == ("req-1",)
        assert repo.queries[0]["query"]["bool"]["filter"][1] == {"term": {"attributes.assistant_id.keyword": "asst-42"}}

    @pytest.mark.asyncio
    async def test_conversation_is_looked_up_by_conversation_id(self):
        repo = _StubMetricsRepository([_page(["req-1"])])
        await resolve_metric_store_ids(repo, DimensionScope(SpendDimension.CONVERSATION, "conv-7"), PERIOD)
        assert repo.queries[0]["query"]["bool"]["filter"][1] == {
            "term": {"attributes.conversation_id.keyword": "conv-7"}
        }

    @pytest.mark.asyncio
    async def test_execution_is_looked_up_by_execution_id(self):
        repo = _StubMetricsRepository([_page(["req-1"])])
        await resolve_metric_store_ids(repo, DimensionScope(SpendDimension.EXECUTION, "exec-9"), PERIOD)
        assert repo.queries[0]["query"]["bool"]["filter"][1] == {"term": {"attributes.execution_id.keyword": "exec-9"}}

    @pytest.mark.asyncio
    async def test_a_grouped_scope_with_no_value_narrows_by_exists_not_term(self):
        """Mirrors the ledger branch's bare `column.is_not(None)` for a report grouped by every value."""
        repo = _StubMetricsRepository([_page(["req-1", "req-2"])])
        await resolve_metric_store_ids(repo, DimensionScope(SpendDimension.ASSISTANT, None), PERIOD)
        assert repo.queries[0]["query"]["bool"]["filter"][1] == {"exists": {"field": "attributes.assistant_id.keyword"}}


class TestDatasourceIsAlwaysEmpty:
    """AD-29 rejects a datasource id: Elasticsearch has none to query and no correlation attribute either."""

    @pytest.mark.asyncio
    async def test_no_query_is_issued(self):
        repo = _StubMetricsRepository([])
        ids = await resolve_metric_store_ids(repo, DimensionScope(SpendDimension.DATASOURCE, "ds-1"), PERIOD)
        assert ids == ()
        assert repo.queries == []


class TestWorkflowIdAndNameUnion:
    """`workflow` resolves via two lookups, unioned: the id-emitting population always matches
    directly by `scope.value` (`workflow_id`, now emitted per AD-29); the name-based lookup only
    joins when the caller has already disambiguated a name, covering the population that
    predates id emission - a `workflow_name` resolving to anything but exactly one live id must
    yield no bucket assignment from that path, which is why the name query never runs alone."""

    @pytest.mark.asyncio
    async def test_without_a_disambiguated_name_only_the_id_lookup_runs(self):
        """The id-emitting population is still resolved even with no safe name available -
        never silently dropped just because the name path cannot run this call."""
        repo = _StubMetricsRepository([_page(["req-1"])])
        ids = await resolve_metric_store_ids(repo, DimensionScope(SpendDimension.WORKFLOW, "wf-1"), PERIOD)
        assert ids == ("req-1",)
        assert len(repo.queries) == 1
        assert repo.queries[0]["query"]["bool"]["filter"][1] == {"term": {"attributes.workflow_id.keyword": "wf-1"}}

    @pytest.mark.asyncio
    async def test_with_a_disambiguated_name_both_lookups_run_and_ids_are_unioned(self):
        repo = _StubMetricsRepository([_page(["req-1"]), _page(["req-2"])])
        ids = await resolve_metric_store_ids(
            repo,
            DimensionScope(SpendDimension.WORKFLOW, "wf-1"),
            PERIOD,
            resolved_workflow_name="Deploy Pipeline",
        )
        assert ids == ("req-1", "req-2")
        assert len(repo.queries) == 2
        assert repo.queries[0]["query"]["bool"]["filter"][1] == {"term": {"attributes.workflow_id.keyword": "wf-1"}}
        assert repo.queries[1]["query"]["bool"]["filter"][1] == {
            "term": {"attributes.workflow_name.keyword": "Deploy Pipeline"}
        }

    @pytest.mark.asyncio
    async def test_an_id_found_by_both_lookups_is_counted_once(self):
        """The union dedupes - the same row can legitimately carry both a matching id and name."""
        repo = _StubMetricsRepository([_page(["req-1"]), _page(["req-1"])])
        ids = await resolve_metric_store_ids(
            repo,
            DimensionScope(SpendDimension.WORKFLOW, "wf-1"),
            PERIOD,
            resolved_workflow_name="Deploy Pipeline",
        )
        assert ids == ("req-1",)


class TestWindowIsWidenedNotEqualToTheRange:
    """@timestamp is emission time and never assigns a row to a period - the window must be a
    superset of the report range, produced by widening on both ends, never the range verbatim."""

    @pytest.mark.asyncio
    async def test_default_emission_lag_widens_both_ends_by_24_hours(self):
        repo = _StubMetricsRepository([_page([])])
        await resolve_metric_store_ids(repo, DimensionScope(SpendDimension.ASSISTANT, "a"), PERIOD)
        time_filter = repo.queries[0]["query"]["bool"]["filter"][0]["range"]["@timestamp"]
        assert time_filter["gte"] == (PERIOD.start - timedelta(hours=24)).isoformat()
        assert time_filter["lt"] == (PERIOD.end + timedelta(hours=24)).isoformat()

    @pytest.mark.asyncio
    async def test_a_configured_emission_lag_is_read_once_and_passed_in_not_a_caller_argument(self):
        repo = _StubMetricsRepository([_page([])])
        await resolve_metric_store_ids(
            repo, DimensionScope(SpendDimension.ASSISTANT, "a"), PERIOD, emission_lag=timedelta(hours=6)
        )
        time_filter = repo.queries[0]["query"]["bool"]["filter"][0]["range"]["@timestamp"]
        assert time_filter["gte"] == (PERIOD.start - timedelta(hours=6)).isoformat()
        assert time_filter["lt"] == (PERIOD.end + timedelta(hours=6)).isoformat()


class TestNeverTruncatesAlwaysPages:
    """Exceeding a page never narrows or silently truncates - the remainder is fetched."""

    @pytest.mark.asyncio
    async def test_a_full_page_is_followed_by_another_request_using_its_after_key(self):
        repo = _StubMetricsRepository(
            [
                _page(["req-1", "req-2"], after_key={"id": "req-2"}),
                _page(["req-3"]),
            ]
        )
        ids = await resolve_metric_store_ids(repo, DimensionScope(SpendDimension.ASSISTANT, "a"), PERIOD, page_size=2)
        assert ids == ("req-1", "req-2", "req-3")
        assert len(repo.queries) == 2
        assert repo.queries[1]["aggs"]["ids"]["composite"]["after"] == {"id": "req-2"}

    @pytest.mark.asyncio
    async def test_a_full_page_with_no_after_key_still_stops_rather_than_looping_forever(self):
        """An empty aggregations envelope (the index-not-found fallback) exhausts the same way."""
        repo = _StubMetricsRepository([_page(["req-1", "req-2"])])
        ids = await resolve_metric_store_ids(repo, DimensionScope(SpendDimension.ASSISTANT, "a"), PERIOD, page_size=2)
        assert ids == ("req-1", "req-2")
        assert len(repo.queries) == 1

    @pytest.mark.asyncio
    async def test_an_exhausted_index_not_found_fallback_resolves_to_no_ids(self):
        repo = _StubMetricsRepository([{"hits": {"hits": [], "total": {"value": 0}}, "aggregations": {}}])
        ids = await resolve_metric_store_ids(repo, DimensionScope(SpendDimension.ASSISTANT, "a"), PERIOD)
        assert ids == ()


class TestMoneyIsNeverTouched:
    """AD-29/CL-48: money_spent is never a numeric operand here - this module has none at all."""

    def test_no_money_field_is_named_in_any_executable_line(self):
        """Asserted over **code**, not over prose.

        A plain text search fails on the module's own docstring, which names the field in order to
        explain why it is forbidden - so the test would force the explanation to be deleted to stay
        green, removing the one thing that stops the rule being undone later. Docstrings and
        comments are therefore stripped first, and what remains is what actually runs.
        """
        import ast
        import inspect

        from codemie.service.spend import dimension_scope

        tree = ast.parse(inspect.getsource(dimension_scope))
        for node in ast.walk(tree):
            # Drop every docstring; comments never reach the AST at all.
            holds_a_docstring = (
                isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
            )
            if holds_a_docstring:
                node.body = node.body[1:] or [ast.Pass()]

        code = ast.unparse(ast.fix_missing_locations(tree))
        assert "money" not in code.lower(), "this module must never name a money field in code"

    def test_the_resolver_returns_ids_and_never_an_amount(self):
        """The structural half of the same rule: every returned value is an id string."""
        repo = _StubMetricsRepository([_page(["req-1"])])
        ids = asyncio.run(resolve_metric_store_ids(repo, DimensionScope(SpendDimension.ASSISTANT, "a"), PERIOD))
        assert all(isinstance(one, str) for one in ids)


class TestTheGroupedResolverAnswersManyValuesInOneQuery:
    """`resolve_metric_store_ids_by_value` - the same rules, asked about a named set of values.

    The failure this class exists for is the one that looks like a success: resolving value by value
    returns the right ids and merely moves a thousand round trips from PostgreSQL to Elasticsearch,
    so nothing fails and nothing is gained.
    """

    @pytest.mark.asyncio
    async def test_a_whole_value_set_costs_one_query(self):
        repo = _StubMetricsRepository([_value_page({"asst-1": ["req-1"], "asst-2": ["req-2", "req-3"]})])
        ids = await resolve_metric_store_ids_by_value(repo, SpendDimension.ASSISTANT, ["asst-1", "asst-2"], PERIOD)

        assert ids == {"asst-1": ("req-1",), "asst-2": ("req-2", "req-3")}
        assert len(repo.queries) == 1

    @pytest.mark.asyncio
    async def test_the_requested_values_narrow_the_query_not_an_aggregation_include(self):
        """Narrowing in the query is what leaves the aggregation with no bucket count to guess at.

        An `include` on a `terms` aggregation would need a `size`, and a `size` is a truncation
        point: this module's docstring says `N` is *"never a cap on the total returned"*.
        """
        repo = _StubMetricsRepository([_value_page({})])
        requested = [f"a{n}" for n in range(25)]
        await resolve_metric_store_ids_by_value(repo, SpendDimension.ASSISTANT, requested, PERIOD)

        assert repo.queries[0]["query"]["bool"]["filter"][1] == {
            "terms": {"attributes.assistant_id.keyword": requested}
        }
        assert "include" not in repo.queries[0]["aggs"]["pairs"]["composite"]

    @pytest.mark.asyncio
    async def test_the_pairs_come_from_a_two_source_composite_on_value_and_correlation_id(self):
        repo = _StubMetricsRepository([_value_page({})])
        await resolve_metric_store_ids_by_value(repo, SpendDimension.ASSISTANT, ["a1"], PERIOD, page_size=1234)

        composite = repo.queries[0]["aggs"]["pairs"]["composite"]
        assert composite["size"] == 1234
        assert composite["sources"] == [
            {"value": {"terms": {"field": "attributes.assistant_id.keyword"}}},
            {"id": {"terms": {"field": "attributes.request_id.keyword"}}},
        ]

    @pytest.mark.asyncio
    async def test_a_value_with_more_ids_than_one_page_is_paged_not_truncated(self):
        """The reason this read is a composite at all. A nested `terms` has no `after_key`, so a
        value holding more than `N` ids would lose the remainder **silently** - and AD-29 calls `N`
        a paging threshold, never a truncation point.
        """
        repo = _StubMetricsRepository(
            [
                _value_page({"a1": ["req-1", "req-2"]}, after={"value": "a1", "id": "req-2"}),
                _value_page({"a1": ["req-3"]}),
            ]
        )
        ids = await resolve_metric_store_ids_by_value(repo, SpendDimension.ASSISTANT, ["a1"], PERIOD, page_size=2)

        assert ids == {"a1": ("req-1", "req-2", "req-3")}
        assert len(repo.queries) == 2
        assert repo.queries[1]["aggs"]["pairs"]["composite"]["after"] == {"value": "a1", "id": "req-2"}

    @pytest.mark.asyncio
    async def test_a_value_repeated_by_the_caller_is_asked_about_once(self):
        repo = _StubMetricsRepository([_value_page({"a1": ["req-1"]})])
        ids = await resolve_metric_store_ids_by_value(repo, SpendDimension.ASSISTANT, ["a1", "a1"], PERIOD)

        assert repo.queries[0]["query"]["bool"]["filter"][1] == {"terms": {"attributes.assistant_id.keyword": ["a1"]}}
        assert ids == {"a1": ("req-1",)}

    @pytest.mark.asyncio
    async def test_the_same_id_seen_on_two_pages_is_counted_once(self):
        """`dict.fromkeys` per value, for the single-value path's reason: a duplicated id would go
        into an `IN` clause twice."""
        repo = _StubMetricsRepository(
            [
                _value_page({"a1": ["req-1"]}, after={"value": "a1", "id": "req-1"}),
                _value_page({"a1": ["req-1"]}),
            ]
        )
        ids = await resolve_metric_store_ids_by_value(repo, SpendDimension.ASSISTANT, ["a1"], PERIOD, page_size=1)

        assert ids == {"a1": ("req-1",)}

    @pytest.mark.asyncio
    async def test_the_window_is_widened_here_too_and_never_the_report_range_verbatim(self):
        """The single-value path's rule, which a second resolver is free to forget: `@timestamp` is
        emission time, so under-widening moves a row into the residue silently."""
        repo = _StubMetricsRepository([_value_page({})])
        await resolve_metric_store_ids_by_value(
            repo, SpendDimension.ASSISTANT, ["a1"], PERIOD, emission_lag=timedelta(hours=6)
        )

        time_filter = repo.queries[0]["query"]["bool"]["filter"][0]["range"]["@timestamp"]
        assert time_filter["gte"] == (PERIOD.start - timedelta(hours=6)).isoformat()
        assert time_filter["lt"] == (PERIOD.end + timedelta(hours=6)).isoformat()

    @pytest.mark.asyncio
    async def test_a_value_the_store_knows_nothing_about_is_absent_rather_than_empty_or_everything(self):
        """Absent means *no ids* to every caller, and a caller reading it as *all ids* would turn one
        value's bucket into the whole table."""
        repo = _StubMetricsRepository([_value_page({"a1": ["req-1"]})])
        ids = await resolve_metric_store_ids_by_value(repo, SpendDimension.ASSISTANT, ["a1", "a2"], PERIOD)

        assert ids == {"a1": ("req-1",)}
        assert "a2" not in ids

    @pytest.mark.asyncio
    async def test_a_dimension_with_no_id_field_resolves_to_no_ids_for_every_value(self):
        """`datasource` has no id anywhere, so this is an empty mapping and not an error - the same
        measured zero `_lookups`' empty list produces for a single scope."""
        repo = _StubMetricsRepository([])
        ids = await resolve_metric_store_ids_by_value(repo, SpendDimension.DATASOURCE, ["ds-1", "ds-2"], PERIOD)

        assert ids == {}
        assert repo.queries == []

    @pytest.mark.asyncio
    async def test_no_values_issues_no_query(self):
        """Nothing was asked about, so there is nothing for a query to answer."""
        repo = _StubMetricsRepository([])
        assert await resolve_metric_store_ids_by_value(repo, SpendDimension.ASSISTANT, [], PERIOD) == {}
        assert repo.queries == []

    @pytest.mark.asyncio
    async def test_workflow_groups_by_its_id_field_and_never_by_name(self):
        """The stated limitation: a grouped read has disambiguated no name, so the name-based lookup
        must not run and the pre-id-emission population lands in the P11 residue instead."""
        repo = _StubMetricsRepository([_value_page({"wf-1": ["req-1"]})])
        ids = await resolve_metric_store_ids_by_value(repo, SpendDimension.WORKFLOW, ["wf-1"], PERIOD)

        assert ids == {"wf-1": ("req-1",)}
        assert len(repo.queries) == 1
        sources = repo.queries[0]["aggs"]["pairs"]["composite"]["sources"]
        assert sources[0] == {"value": {"terms": {"field": "attributes.workflow_id.keyword"}}}
        assert "workflow_name" not in str(repo.queries[0])

    @pytest.mark.asyncio
    async def test_an_index_not_found_fallback_resolves_to_no_ids_rather_than_raising(self):
        repo = _StubMetricsRepository([{"hits": {"hits": [], "total": {"value": 0}}, "aggregations": {}}])
        assert await resolve_metric_store_ids_by_value(repo, SpendDimension.ASSISTANT, ["a1"], PERIOD) == {}
