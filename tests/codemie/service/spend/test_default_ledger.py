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

"""Today's non-LiteLLM behaviour behind the port.

The rule this file exists for cannot be validated by observation, only by test: no deployment is
currently mixed, so the double-count guard never fires in the field.
"""

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from codemie.service.analytics.metric_names import MetricName
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.spend import default_ledger as default_ledger_module
from codemie.service.spend.coverage import SpendDimension
from codemie.service.monitoring.metrics_constants import SPEND_LEDGER_READ_FAILURES_METRIC
from codemie.service.spend.coverage import SpendResult
from codemie.service.spend.default_ledger import _USER_ID_FIELD, FILTER_BATCH_SIZE, MONEY_FIELD, DefaultSpendLedger
from codemie.service.spend.ledger import (
    AccessScope,
    ComposedScope,
    DimensionScope,
    PersonalHolder,
    PersonTotalHolder,
    ProjectGroupHolder,
    ProjectHolder,
    ProjectMemberHolder,
    ReportFilter,
    UserTerm,
    bucket_starts,
)
from codemie.service.spend.legacy_ledger import legacy_metric_names
from codemie.service.spend.routing import LLMCallClass
from codemie.service.spend.spend_group import SpendGroup
from codemie.service.spend.traffic_class import TrafficClass
from tests.codemie.service.spend.periods import PERIOD

USER = "aaaaaaaa-1111-1111-1111-111111111111"
OTHER_USER = "bbbbbbbb-2222-2222-2222-222222222222"
FAILING_USER = "cccccccc-3333-3333-3333-333333333333"


class _StubMetrics:
    """Records the bodies it was asked for, and answers with a fixed money total.

    `settled` and `unsettled` are the two coverage counts the store answers beside the sum. The
    defaults are the shape every deployment produces today - every document carries its cost - so a
    test that says nothing about settlement gets the figure it always got.
    """

    def __init__(self, money: float = 0.0, count: int = 0, settled: int = 1, unsettled: int = 0):
        self.bodies: list[dict] = []
        self._money = money
        self._count = count
        self._settled = settled
        self._unsettled = unsettled

    async def execute_aggregation_query(self, body: dict, **_kwargs) -> dict:
        self.bodies.append(body)
        return {
            "aggregations": {
                "money": {"value": self._money},
                "money_settled": {"value": self._settled},
                "money_unsettled": {"doc_count": self._unsettled},
                "calls": {"value": self._count},
            }
        }

    def metric_names_asked_for(self) -> list[str]:
        names = []
        for body in self.bodies:
            for clause in body["query"]["bool"]["filter"]:
                if "terms" in clause and "metric_name.keyword" in clause["terms"]:
                    names.extend(clause["terms"]["metric_name.keyword"])
        return names


def ledger(
    *call_classes, money: float = 0.0, count: int = 0, settled: int = 1, unsettled: int = 0
) -> tuple[DefaultSpendLedger, _StubMetrics]:
    repo = _StubMetrics(money=money, count=count, settled=settled, unsettled=unsettled)
    return DefaultSpendLedger(call_classes, repository=repo), repo


class TestTheDoubleCountGuard:
    """AD-27: the only place double counting is reachable in this design."""

    def test_an_unscoped_ledger_cannot_be_constructed(self):
        """An unscoped instance would sum LiteLLM-routed traffic as well as native traffic."""
        with pytest.raises(ValueError):
            DefaultSpendLedger([], repository=_StubMetrics())

    def test_an_embeddings_only_ledger_never_reads_a_chat_metric(self):
        """The mixed deployment: chat goes to LiteLLM, embeddings stay native.

        If this ledger read chat metrics it would report every chat call a second time - once from
        the spend-logs ledger and once from here - and each figure would look right alone.
        """
        led, repo = ledger(LLMCallClass.EMBEDDINGS)
        asyncio.run(led.aggregate(PERIOD))

        asked = repo.metric_names_asked_for()
        assert asked == ["datasource_tokens_usage"]
        assert "conversation_assistant_usage" not in asked
        assert "workflow_execution_total" not in asked

    def test_a_chat_only_ledger_never_reads_the_embedding_metric(self):
        led, repo = ledger(LLMCallClass.CHAT)
        asked = (asyncio.run(led.aggregate(PERIOD)), repo.metric_names_asked_for())[1]
        assert "datasource_tokens_usage" not in asked

    def test_no_ledger_ever_reads_the_litellm_proxy_metric(self):
        """That metric IS LiteLLM-routed traffic; summing it here is the double count itself."""
        for classes in ([LLMCallClass.CHAT], [LLMCallClass.EMBEDDINGS], list(LLMCallClass)):
            led, repo = ledger(*classes)
            asyncio.run(led.aggregate(PERIOD))
            assert "codemie_litellm_proxy_usage" not in repo.metric_names_asked_for()

    def test_a_wholly_non_litellm_ledger_reads_both_classes(self):
        led, repo = ledger(*LLMCallClass)
        asyncio.run(led.aggregate(PERIOD))
        asked = set(repo.metric_names_asked_for())
        assert "datasource_tokens_usage" in asked
        assert "conversation_assistant_usage" in asked


class TestTheTimeBoundIsHalfOpen:
    def test_the_upper_bound_is_exclusive(self):
        """A closed upper bound lands a boundary document in two adjacent period totals."""
        led, repo = ledger(LLMCallClass.CHAT)
        asyncio.run(led.aggregate(PERIOD))

        ranges = [c["range"]["@timestamp"] for c in repo.bodies[0]["query"]["bool"]["filter"] if "range" in c]
        assert len(ranges) == 1
        assert "gte" in ranges[0] and "lt" in ranges[0]
        assert "lte" not in ranges[0]


class TestWhatThisStoreCanAndCannotIdentify:
    """The metric store records no budget category, and four of five holders are keyed on one."""

    @pytest.mark.parametrize(
        "holder",
        [
            PersonalHolder(user_id=USER, category=BudgetCategory.PLATFORM, aliases=frozenset()),
            ProjectHolder(project="acme", category=BudgetCategory.PLATFORM),
            ProjectMemberHolder(project="acme", category=BudgetCategory.PLATFORM, user_id=USER),
            ProjectGroupHolder(projects=frozenset({"acme"}), category=BudgetCategory.PLATFORM),
        ],
    )
    def test_a_category_keyed_holder_gets_no_figure_and_never_a_zero(self, holder):
        """A category-blind total would be a WRONG number; an absent one is honest.

        Zero would say the holder spent nothing, which is a different claim from "this store cannot
        determine what they spent".
        """
        led, repo = ledger(LLMCallClass.CHAT, money=123.45)
        result = asyncio.run(led.consumed_for_holder(holder, PERIOD))

        assert result.figure is None
        assert result.unavailable == PERIOD, "a no-figure result must name the range it covers"
        assert repo.bodies == [], "no query should be issued for a scope this store cannot express"

    def test_the_person_total_is_answerable_and_matches_by_id_or_alias(self):
        led, repo = ledger(LLMCallClass.CHAT, money=17.5)
        holder = PersonTotalHolder(user_id=USER, aliases=frozenset({"alice", "alice@example.com"}))
        result = asyncio.run(led.consumed_for_holder(holder, PERIOD))

        assert result.figure == Decimal("17.5")
        assert not result.partial
        rendered = str(repo.bodies[0])
        assert USER in rendered
        assert "alice@example.com" in rendered

    def test_an_unresolvable_alias_set_is_a_partial_not_an_unavailable(self):
        """The period is fully covered; what is missing is a dimension, not a period."""
        led, _repo = ledger(LLMCallClass.CHAT, money=5)
        result = asyncio.run(led.consumed_for_holder(PersonTotalHolder(user_id=USER, aliases=None), PERIOD))

        assert result.figure == Decimal("5")
        assert result.unavailable is None
        portion = result.partial[SpendDimension.IDENTITY_BRIDGE]
        assert portion.spend is None and portion.rows is None, "the one portion that is not computable"


LLM_MODEL_FIELD = "attributes.llm_model.keyword"
EMBEDDINGS_MODEL_FIELD = "attributes.embeddings_model.keyword"


def model_filter(value: str | None) -> dict:
    """`SpendDimension.MODEL` as the adapter must spell it, written once for every test that asks.

    Spelled out here rather than called through from the adapter: a helper that invoked the
    production builder would assert only that the read agrees with itself, and the partition -
    `llm_model` wherever a document carries one, `embeddings_model` only where it carries none - is
    exactly what a plain OR over the two fields gets wrong.
    """
    if value is None:
        return {
            "bool": {
                "should": [
                    {"exists": {"field": LLM_MODEL_FIELD}},
                    {"exists": {"field": EMBEDDINGS_MODEL_FIELD}},
                ],
                "minimum_should_match": 1,
            }
        }
    return {
        "bool": {
            "should": [
                {"term": {LLM_MODEL_FIELD: value}},
                {
                    "bool": {
                        "must_not": [{"exists": {"field": LLM_MODEL_FIELD}}],
                        "filter": [{"term": {EMBEDDINGS_MODEL_FIELD: value}}],
                    }
                },
            ],
            "minimum_should_match": 1,
        }
    }


class TestTheDimensionScopes:
    @pytest.mark.parametrize(
        "dimension,clause",
        [
            (SpendDimension.ASSISTANT, {"term": {"attributes.assistant_id.keyword": "x-1"}}),
            (SpendDimension.CONVERSATION, {"term": {"attributes.conversation_id.keyword": "x-1"}}),
            (SpendDimension.WORKFLOW, {"term": {"attributes.workflow_id.keyword": "x-1"}}),
            (SpendDimension.EXECUTION, {"term": {"attributes.execution_id.keyword": "x-1"}}),
            # The one dimension that is not an id and not one field either: a document is keyed by
            # the model it was billed under, which is `llm_model` wherever it has one.
            (SpendDimension.MODEL, model_filter("x-1")),
        ],
    )
    def test_a_dimension_with_an_attribute_is_filtered_on_it(self, dimension, clause):
        led, repo = ledger(LLMCallClass.CHAT, money=1)
        asyncio.run(led.aggregate(PERIOD, DimensionScope(dimension, "x-1")))
        assert clause in repo.bodies[0]["query"]["bool"]["filter"]

    @pytest.mark.parametrize("read", ["aggregate", "request_count"])
    @pytest.mark.parametrize(
        "dimension,clause",
        [
            (SpendDimension.ASSISTANT, {"exists": {"field": "attributes.assistant_id.keyword"}}),
            (SpendDimension.MODEL, model_filter(None)),
        ],
    )
    def test_a_grouped_scope_narrows_by_existence_rather_than_by_nothing(self, read, dimension, clause):
        """*Group by X, no particular X* built with no clause at all is this adapter's whole total.

        The money read and the count read are both pinned, because they build their filter lists
        apart and a widening in either one is a number that looks exactly like a right one.
        """
        led, repo = ledger(LLMCallClass.CHAT, money=99, count=7)

        asyncio.run(getattr(led, read)(PERIOD, DimensionScope(dimension, None)))

        assert clause in repo.bodies[0]["query"]["bool"]["filter"]

    def test_the_datasource_dimension_has_no_attribute_and_gets_no_figure(self):
        """No emission carries a datasource id, so the scope cannot be expressed at all."""
        led, repo = ledger(LLMCallClass.EMBEDDINGS, money=99)
        result = asyncio.run(led.aggregate(PERIOD, DimensionScope(SpendDimension.DATASOURCE, "ds-1")))
        assert result.figure is None
        assert repo.bodies == []

    def test_a_grouped_project_scope_is_absent_rather_than_the_platform_total(self):
        """What the missing `_DIMENSION_FIELD` entry for `project` stops, pinned on the figure.

        Not a widening - a grouped scope narrows by `exists` like every other one. The project
        attribute reaches a document only when the emitting call site carried one, so *group by
        project, no particular project* answered here would be a sum over whichever documents
        happened to arrive with the attribute, presented as the platform figure the caller asked for.
        The absence is what stops it, which is why the entry is missing rather than
        present-and-special-cased.
        """
        led, repo = ledger(LLMCallClass.CHAT, money=99)

        result = asyncio.run(led.aggregate(PERIOD, DimensionScope(SpendDimension.PROJECT, None)))

        assert result.figure is None, "99 here would be an attribute-blind sum answering a project question"
        assert result.unavailable == PERIOD
        assert repo.bodies == []

    def test_resolved_ids_are_accepted_and_ignored(self):
        """Every row here IS a metric-store row, so there is no column-less branch to reconcile."""
        led, repo = ledger(LLMCallClass.CHAT, money=3)
        with_ids = asyncio.run(led.aggregate(PERIOD, None, ("req-1", "req-2")))
        without = asyncio.run(led.aggregate(PERIOD))
        assert with_ids.figure == without.figure
        assert repo.bodies[0] == repo.bodies[1]


class _RowSetMetrics:
    """Answers every grain from **one** row set, by applying the body's own filters to it.

    A stub answering a fixed total per call makes the drift test vacuous: it agrees with itself even
    for an implementation filtering on the wrong attribute, or on none at all. This one is a
    field-keyed row store, so the grouped figures and the ungrouped total can only agree if both
    reads asked the same question of the same rows.
    """

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.bodies: list[dict] = []

    async def execute_aggregation_query(self, body: dict) -> dict:
        self.bodies.append(body)
        selected = [row for row in self.rows if self._matches(row, body["query"]["bool"]["filter"])]
        return {"aggregations": self._answer(selected, body["aggs"])}

    @classmethod
    def _answer(cls, rows: list[dict], aggs: dict) -> dict:
        """A `filters` aggregation as named buckets, each answering its own sub-aggregations in turn."""
        grouped = next((name for name, agg in aggs.items() if "filters" in agg), None)
        if grouped is None:
            return cls._coverage(rows)
        return {
            grouped: {
                "buckets": {
                    key: cls._answer(
                        [r for r in rows if cls._matches(r, spec["bool"]["filter"])], aggs[grouped]["aggs"]
                    )
                    for key, spec in aggs[grouped]["filters"]["filters"].items()
                }
            }
        }

    @classmethod
    def _coverage(cls, rows: list[dict]) -> dict:
        """One bucket as the store answers it: the sum, and the two counts that qualify it.

        A row carrying no `MONEY_FIELD` is a document emitted while its cost was unsettled. The store's
        `sum` skips it and its `value_count` does not count it, so both are reproduced here rather than
        hand-written per test - a stub that summed the missing key instead would make the very state
        under test unreachable.
        """
        settled = [row for row in rows if MONEY_FIELD in row]
        return {
            "money": {"value": cls._money(settled)},
            "money_settled": {"value": len(settled)},
            "money_unsettled": {"doc_count": len(rows) - len(settled)},
        }

    @staticmethod
    def _money(rows: list[dict]) -> float:
        # A float, as Elasticsearch answers a `sum` with, so the read under test does its own
        # `Decimal(str(...))` conversion rather than being handed a Decimal it never sees in the field.
        return float(sum((row[MONEY_FIELD] for row in rows), Decimal("0")))

    @classmethod
    def _matches(cls, row: dict, clauses: list[dict]) -> bool:
        return all(cls._clause(row, clause) for clause in clauses)

    @classmethod
    def _bool(cls, row: dict, body: dict) -> bool:
        """`must` and `filter` as an AND, `must_not` as its negation, `should` against its own
        `minimum_should_match`.

        The shape this reads for is `_person_filters`' alias union, and `AccessScope`'s own boundary
        clause has it too. Both are **nested** `bool`s rather than clauses flattened into the outer
        `filter` list, precisely so their `OR` stays parenthesised inside that `AND` - so a stub that
        ignored them, or evaluated them as one flat conjunction, would pass an implementation that
        had flattened them and admitted rows on one `should` alone.

        `must_not` beside `filter` is what makes the model partition testable at all: its second
        branch is *carries no `llm_model` **and** this `embeddings_model`*, and a stub dropping the
        negation would evaluate it as a plain OR over the two fields - which is precisely the
        double-counting implementation the partition exists to refuse.
        """
        matched = sum(1 for clause in body.get("should", ()) if cls._matches(row, [clause]))
        if matched < body.get("minimum_should_match", 0):
            return False
        if any(cls._matches(row, [clause]) for clause in body.get("must_not", ())):
            return False
        return all(cls._matches(row, [clause]) for clause in (*body.get("must", ()), *body.get("filter", ())))

    @classmethod
    def _clause(cls, row: dict, clause: dict) -> bool:
        ((kind, body),) = clause.items()
        if kind == "bool":
            return cls._bool(row, body)
        if kind == "term":
            ((field, value),) = body.items()
            return row.get(field) == value
        if kind == "terms":
            ((field, values),) = body.items()
            return row.get(field) in values
        if kind == "exists":
            return body["field"] in row
        if kind == "range":
            bounds = body["@timestamp"]
            return datetime.fromisoformat(bounds["gte"]) <= row["@timestamp"] < datetime.fromisoformat(bounds["lt"])
        # Raised rather than ignored: a clause this stub cannot read would silently widen every bucket
        # it appears in, and the drift test would then pass on a query selecting the wrong rows.
        raise AssertionError(f"the row-set stub cannot evaluate a {kind!r} clause")


ASSISTANT_FIELD = "attributes.assistant_id.keyword"
MID_PERIOD = datetime(2026, 8, 15, tzinfo=timezone.utc)


def assistant_row(assistant: str, money: str, *, metric: str = "conversation_assistant_usage") -> dict:
    return {
        "metric_name.keyword": metric,
        "@timestamp": MID_PERIOD,
        ASSISTANT_FIELD: assistant,
        MONEY_FIELD: Decimal(money),
    }


def unsettled_row(assistant: str, *, metric: str = "conversation_assistant_usage") -> dict:
    """A document emitted while its cost was unsettled: **no money attribute at all.**

    Not a zero one - that is exactly the fabrication the four monitoring emitters were gated to stop
    (AD-28), and nothing re-emits the document once the ledger settles.
    """
    row = assistant_row(assistant, "0", metric=metric)
    del row[MONEY_FIELD]
    return row


class TestTheGroupedDimensionRead:
    """One figure per named value, from the vocabulary `aggregate` already reads a single value with."""

    def test_every_requested_value_is_present_and_one_with_no_rows_is_a_measured_zero(self):
        """A value dropped from the mapping makes a caller's table row vanish rather than read zero."""
        repo = _RowSetMetrics([assistant_row("asst-1", "1.5")])
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=repo)

        result = asyncio.run(led.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, ["asst-1", "asst-2"]))

        assert set(result) == {"asst-1", "asst-2"}
        assert result["asst-1"].figure == Decimal("1.5")
        # There is no coverage floor here, so an empty bucket means nothing was recorded rather than
        # nothing is known - the same footing the batched holder read puts an empty bucket on.
        assert result["asst-2"].figure == Decimal("0")
        assert result["asst-2"].unavailable is None

    def test_the_boundary_and_the_filter_both_reach_the_outer_query(self):
        """What the dashboard asked about qualifies every value's bucket at once, as on the holder batch:
        without it a filtered table's row reads its value's spend in every project (RD-D1)."""
        repo = _RowSetMetrics([])
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=repo)
        access = AccessScope(unrestricted=False, user_id=USER, plain_projects=("proj-a",))
        report_filter = ReportFilter(users=(UserTerm(user_id=USER, aliases=frozenset()),), projects=("proj-b",))

        asyncio.run(
            led.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, ["asst-1"], None, access, report_filter)
        )

        outer = repo.bodies[0]["query"]["bool"]["filter"]
        boundary = access.elasticsearch_filter(
            user_id_field="attributes.user_id.keyword", project_field="attributes.project.keyword"
        )
        assert boundary in outer
        assert {"terms": {"attributes.user_id.keyword": [USER]}} in outer
        assert {"terms": {"attributes.project.keyword": ["proj-b"]}} in outer

    def test_one_query_answers_every_value(self):
        """A round trip per value is a thousand for a thousand-row table, and that acquires a cache."""
        repo = _RowSetMetrics([])
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=repo)

        asyncio.run(led.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, [f"asst-{n}" for n in range(25)]))

        assert len(repo.bodies) == 1
        assert len(repo.bodies[0]["aggs"]["values"]["filters"]["filters"]) == 25

    def test_a_value_the_caller_repeated_yields_one_bucket_and_one_entry(self):
        repo = _RowSetMetrics([assistant_row("asst-1", "2")])
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=repo)

        result = asyncio.run(led.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, ["asst-1", "asst-1"]))

        assert result == {"asst-1": result["asst-1"]}
        assert list(repo.bodies[0]["aggs"]["values"]["filters"]["filters"]) == ["value_0"]

    def test_no_values_issues_no_query(self):
        repo = _RowSetMetrics([])
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=repo)

        assert asyncio.run(led.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, [])) == {}
        assert repo.bodies == []

    @pytest.mark.parametrize(
        "dimension",
        [SpendDimension.DATASOURCE, SpendDimension.PROJECT, SpendDimension.IDENTITY_BRIDGE],
    )
    def test_a_dimension_with_no_attribute_gets_no_figure_per_value_and_never_a_zero(self, dimension):
        """A zero would be a positive claim that each of these values cost nothing.

        `datasource` has no id on any emission; the other two have no `_DIMENSION_FIELD` entry, so
        there is nothing to build a bucket on - the same absence `aggregate` reports for a scope.
        """
        repo = _RowSetMetrics([assistant_row("asst-1", "99")])
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=repo)

        result = asyncio.run(led.aggregate_by_dimension(PERIOD, dimension, ["v-1", "v-2"]))

        assert set(result) == {"v-1", "v-2"}
        assert all(entry.figure is None for entry in result.values())
        assert all(entry.unavailable == PERIOD for entry in result.values())
        assert repo.bodies == [], "a scope this store cannot express costs no query"

    def test_the_double_count_guard_narrows_the_grouped_read_identically(self):
        """A grouped figure over a wider metric set cannot sum to the ungrouped one it is checked against."""
        led, repo = ledger(LLMCallClass.EMBEDDINGS)
        asyncio.run(led.aggregate(PERIOD))
        grouped_repo = _RowSetMetrics([])
        grouped = DefaultSpendLedger([LLMCallClass.EMBEDDINGS], repository=grouped_repo)
        asyncio.run(grouped.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, ["asst-1"]))

        def metric_names(body):
            return next(c["terms"]["metric_name.keyword"] for c in body["query"]["bool"]["filter"] if "terms" in c)

        assert metric_names(grouped_repo.bodies[0]) == metric_names(repo.bodies[0]) == ["datasource_tokens_usage"]
        assert "codemie_litellm_proxy_usage" not in metric_names(grouped_repo.bodies[0])

    def test_ids_by_value_is_accepted_and_ignored(self):
        """Those ids were resolved from this very store, so there is no second branch to reconcile."""
        repo = _RowSetMetrics([assistant_row("asst-1", "4")])
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=repo)

        with_ids = asyncio.run(
            led.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, ["asst-1"], {"asst-1": ("req-1",)})
        )
        without = asyncio.run(led.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, ["asst-1"]))

        assert with_ids["asst-1"].figure == without["asst-1"].figure == Decimal("4")
        assert repo.bodies[0] == repo.bodies[1]

    def test_the_compiled_body_is_pinned(self):
        """The whole body, because every part of it is load-bearing and none of it is asserted twice."""
        repo = _RowSetMetrics([])
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=repo)

        asyncio.run(led.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, ["asst-1", "asst-2"]))

        assert repo.bodies[0] == {
            "size": 0,
            "query": {
                "bool": {
                    "filter": [
                        {
                            "terms": {
                                "metric_name.keyword": [
                                    "agent_execution_total",
                                    "conversation_assistant_usage",
                                    "workflow_execution_total",
                                ]
                            }
                        },
                        {
                            "range": {
                                "@timestamp": {"gte": "2026-08-01T00:00:00+00:00", "lt": "2026-09-01T00:00:00+00:00"}
                            }
                        },
                    ]
                }
            },
            "aggs": {
                "values": {
                    "filters": {
                        "filters": {
                            "value_0": {"bool": {"filter": [{"term": {ASSISTANT_FIELD: "asst-1"}}]}},
                            "value_1": {"bool": {"filter": [{"term": {ASSISTANT_FIELD: "asst-2"}}]}},
                        }
                    },
                    "aggs": {
                        "money": {"sum": {"field": "attributes.money_spent"}},
                        "money_settled": {"value_count": {"field": "attributes.money_spent"}},
                        "money_unsettled": {
                            "filter": {"bool": {"must_not": [{"exists": {"field": "attributes.money_spent"}}]}}
                        },
                    },
                }
            },
        }


class TestTheGroupedFigureCannotDriftFromTheUngroupedOne:
    """The one invariant that exists only **between** two reads, so neither file can pin it alone."""

    def test_the_values_summed_equal_aggregate_for_the_same_range(self):
        """Every row in the range carries one of the requested values, so the split must be exhaustive.

        The stub derives both grains from one row set, so a grouped read narrowing the range, dropping
        the metric-name guard or filtering on another attribute disagrees with the total here.
        """
        rows = [assistant_row("asst-1", "1.5"), assistant_row("asst-1", "0.25"), assistant_row("asst-2", "2")]
        repo = _RowSetMetrics(rows)
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=repo)

        total = asyncio.run(led.aggregate(PERIOD))
        per_value = asyncio.run(led.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, ["asst-1", "asst-2"]))

        assert total.figure == Decimal("3.75")
        assert sum((entry.figure for entry in per_value.values()), Decimal("0")) == total.figure

    def test_one_value_grouped_equals_aggregate_for_that_scope(self):
        """The per-key half of the same guarantee: one bucket must be the single-scope read's own answer."""
        rows = [assistant_row("asst-1", "1.5"), assistant_row("asst-2", "2")]
        repo = _RowSetMetrics(rows)
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=repo)

        scoped = asyncio.run(led.aggregate(PERIOD, DimensionScope(SpendDimension.ASSISTANT, "asst-1")))
        grouped = asyncio.run(led.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, ["asst-1"]))

        assert grouped["asst-1"].figure == scoped.figure == Decimal("1.5")

    def test_a_row_outside_the_range_is_in_neither_grain(self):
        """A grouped read that widened the bound would show money the total does not, and vice versa."""
        outside = assistant_row("asst-1", "5") | {"@timestamp": datetime(2026, 9, 1, tzinfo=timezone.utc)}
        repo = _RowSetMetrics([assistant_row("asst-1", "1.5"), outside])
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=repo)

        total = asyncio.run(led.aggregate(PERIOD))
        grouped = asyncio.run(led.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, ["asst-1"]))

        assert grouped["asst-1"].figure == total.figure == Decimal("1.5")


class TestPerRequestCostIsNotAnswerableHere:
    def test_it_is_unavailable_and_not_pending(self):
        """`pending` promises a figure shortly and drives a re-read of a store that will never answer.

        Nothing is lost: on this path the cost was computed locally at call time and is already on
        the record, so no record reaches this method needing to be filled in.
        """
        led, _repo = ledger(LLMCallClass.CHAT)
        cost = asyncio.run(led.cost_for_request(run_id="run-1"))
        # Predicates, not the same-named CONSTRUCTORS: `cost.unavailable()` would build a new cost
        # and be truthy whatever this one's state is, so the assertion would pass vacuously.
        assert cost.is_unavailable
        assert not cost.is_pending
        assert cost.figure is None


class TestMoneyNeverArrivesThroughABinaryFloat:
    def test_the_total_is_built_from_the_string_form(self):
        """Binding a float straight into Decimal carries the float's representation error into money."""
        led, _repo = ledger(LLMCallClass.CHAT, money=0.07573200000000001)
        result = asyncio.run(led.aggregate(PERIOD))
        assert result.figure == Decimal("0.07573200000000001")


def coverage(total: float, *, settled: int, unsettled: int) -> dict:
    """One money bucket as the store answers it, written out for the reads that take no row set."""
    return {
        "money": {"value": total},
        "money_settled": {"value": settled},
        "money_unsettled": {"doc_count": unsettled},
    }


class _FixedMetrics:
    """Answers one prepared response, whatever it is asked, and records the bodies."""

    def __init__(self, response: dict) -> None:
        self.response = response
        self.bodies: list[dict] = []

    async def execute_aggregation_query(self, body: dict, **_kwargs) -> dict:
        self.bodies.append(body)
        return self.response


class TestAnUnsettledCostIsNotAZero:
    """AD-28's write-side gate, closed on the read side - **as a guard, not as a live fix.**

    The four monitoring emitters omit `attributes.money_spent` entirely while a cost is unsettled, and
    an Elasticsearch `sum` skips exactly those documents: what it returns is the *settled remainder* of
    the window, with nothing in the number to say so. `base_monitoring_service`'s own comment names this
    ledger as the reason the gate matters beyond a dashboard.

    **No current configuration can put such a document in front of this adapter.** Every source of an
    unsettled cost is gated on `uses_litellm` for its own call class, `composition` hands this adapter
    only the classes that predicate is False for, and `money_spent_unavailable` is set nowhere in the
    source. The first test pins that consequence - the guard changes no figure that exists today. The
    rest pin what it does once the state becomes reachable, which is what wiring
    `money_spent_unavailable` to a readiness read, or an emitter gaining a `None` cost path not gated on
    routing, would do.
    """

    def test_a_window_whose_documents_all_carry_money_is_unchanged(self):
        """The no-op property: with nothing unsettled, the guard is not reachable and not felt."""
        rows = [assistant_row("asst-1", "1.5"), assistant_row("asst-1", "0.25"), assistant_row("asst-2", "2")]
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=_RowSetMetrics(rows))

        result = asyncio.run(led.aggregate(PERIOD))

        assert result.figure == Decimal("3.75")
        assert result.is_exact, "an unqualified figure, exactly as before the guard existed"

    def test_a_measured_zero_stays_an_exact_zero(self):
        """A free call is a real $0.00 and must not be swept up with the absent figures."""
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=_RowSetMetrics([assistant_row("asst-1", "0")]))

        result = asyncio.run(led.aggregate(PERIOD))

        assert result.figure == Decimal("0")
        assert result.is_exact

    def test_a_scope_with_no_documents_at_all_is_still_a_measured_zero(self):
        """No coverage floor here, so *no call recorded* means nothing was spent - unchanged."""
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=_RowSetMetrics([]))

        result = asyncio.run(led.aggregate(PERIOD))

        assert result.figure == Decimal("0")
        assert result.is_exact

    def test_nothing_settled_is_no_figure_and_never_a_zero(self):
        """The state the guard exists for: documents were recorded and the `sum` skipped every one.

        Its `0.0` measures nothing at all, so publishing it claims the window cost nothing - the one
        thing an absent figure never means.
        """
        rows = [unsettled_row("asst-1"), unsettled_row("asst-1")]
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=_RowSetMetrics(rows))

        result = asyncio.run(led.aggregate(PERIOD))

        assert result.figure is None, "the settled remainder of nothing is not a figure"
        assert result.figure != Decimal("0")
        assert result.unavailable == PERIOD, "a no-figure result must name the range it covers"

    def test_the_sum_is_never_the_test_for_whether_a_figure_exists(self):
        """Both windows sum to `0.0`; one is a measurement and the other is the absence of one.

        `value_count` is what separates them, which is why `sum == 0` is not the condition anywhere.
        """
        measured = DefaultSpendLedger([LLMCallClass.CHAT], repository=_RowSetMetrics([assistant_row("asst-1", "0")]))
        absent = DefaultSpendLedger([LLMCallClass.CHAT], repository=_RowSetMetrics([unsettled_row("asst-1")]))

        assert asyncio.run(measured.aggregate(PERIOD)).figure == Decimal("0")
        assert asyncio.run(absent.aggregate(PERIOD)).figure is None

    def test_a_partly_settled_window_keeps_the_settled_money(self):
        """Some settled, some not: the settled money is real, so withholding it is the larger error.

        It is a **subtotal**, and this result cannot say so: `SpendResult`'s `partial` is keyed by
        `SpendDimension` and an unsettled cost is a settlement state rather than a dimension, so
        flagging it needs vocabulary the port does not have. Unreachable today, and reported rather
        than papered over with a dimension that would state a false cause.
        """
        rows = [assistant_row("asst-1", "1.5"), unsettled_row("asst-1")]
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=_RowSetMetrics(rows))

        result = asyncio.run(led.aggregate(PERIOD))

        assert result.figure == Decimal("1.5"), "the settled half, not a zero and not an absence"

    def test_a_scoped_read_reports_the_same_absence(self):
        """The dimension-scoped branch reads money through the same helper, so it cannot diverge."""
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=_RowSetMetrics([unsettled_row("asst-1")]))

        result = asyncio.run(led.aggregate(PERIOD, DimensionScope(SpendDimension.ASSISTANT, "asst-1")))

        assert result.figure is None
        assert result.unavailable == PERIOD

    def test_one_value_with_nothing_settled_does_not_blank_its_siblings(self):
        """The grouped path is per bucket, so an absence is confined to the value that has one."""
        rows = [assistant_row("asst-1", "2"), unsettled_row("asst-2"), unsettled_row("asst-2")]
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=_RowSetMetrics(rows))

        result = asyncio.run(led.aggregate_by_dimension(PERIOD, SpendDimension.ASSISTANT, ["asst-1", "asst-2", "x"]))

        assert result["asst-1"].figure == Decimal("2")
        assert result["asst-2"].figure is None
        assert result["asst-2"].unavailable == PERIOD
        # Still a measured zero: no document was recorded for it at all, which is the other reading.
        assert result["x"].figure == Decimal("0")

    def test_a_person_whose_documents_never_settled_gets_no_figure(self):
        led, _repo = ledger(LLMCallClass.CHAT, money=0.0, settled=0, unsettled=3)
        holder = PersonTotalHolder(user_id=USER, aliases=frozenset({"alice"}))

        result = asyncio.run(led.consumed_for_holder(holder, PERIOD))

        assert result.figure is None
        assert result.unavailable == PERIOD

    def test_an_absent_figure_carries_no_identity_bridge_label(self):
        """`SpendResult`'s own rule: no portion was admitted, so there is nothing to qualify.

        The label is dropped rather than raised on - the whole period is already reported absent, and
        a result carrying both would not validate.
        """
        led, _repo = ledger(LLMCallClass.CHAT, money=0.0, settled=0, unsettled=2)

        result = asyncio.run(led.consumed_for_holder(PersonTotalHolder(user_id=USER, aliases=None), PERIOD))

        assert result.figure is None
        assert not result.partial

    def test_the_batched_holder_read_reports_the_same_absence(self):
        """One helper feeds both holder paths, so a batched figure cannot be a zero the single one is not."""
        repo = _FixedMetrics(
            {"aggregations": {"holders": {"buckets": {"holder_0": coverage(0.0, settled=0, unsettled=4)}}}}
        )
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=repo)
        holder = PersonTotalHolder(user_id=USER, aliases=frozenset({"alice"}))

        result = asyncio.run(led.consumed_for_holders([holder], PERIOD))[holder]

        assert result.figure is None
        assert result.unavailable == PERIOD

    def test_a_composed_scope_reports_the_same_absence(self):
        led, _repo = ledger(LLMCallClass.CHAT, money=0.0, settled=0, unsettled=1)

        result = asyncio.run(
            led.aggregate_composed(PERIOD, ComposedScope(dimension=DimensionScope(SpendDimension.ASSISTANT, "a-1")))
        )

        assert result.figure is None
        assert result.unavailable == PERIOD


class TestTheCoverageMeasurementItself:
    def test_it_travels_with_every_money_read(self):
        """A sum asked for without its counts is a figure nothing can certify."""
        led, repo = ledger(LLMCallClass.CHAT, money=1)
        asyncio.run(led.aggregate(PERIOD))

        assert repo.bodies[0]["aggs"] == {
            "money": {"sum": {"field": MONEY_FIELD}},
            "money_settled": {"value_count": {"field": MONEY_FIELD}},
            "money_unsettled": {"filter": {"bool": {"must_not": [{"exists": {"field": MONEY_FIELD}}]}}},
        }

    def test_the_unsettled_count_needs_no_cost_bearing_narrowing(self):
        """`money_coverage_aggs` takes a `cost_bearing` term for a `match_all` scope. This is not one.

        Every read here is already restricted to `_metric_names()`, so the session, webhook and tool
        documents that never carry money are outside the query and cannot be counted as unsettled -
        which is what would brand every window a subtotal, an alarm always on.
        """
        led, repo = ledger(LLMCallClass.CHAT, money=1)
        asyncio.run(led.aggregate(PERIOD))

        unsettled = repo.bodies[0]["aggs"]["money_unsettled"]["filter"]["bool"]
        assert set(unsettled) == {"must_not"}, "no second narrowing term is needed or wanted"
        assert repo.metric_names_asked_for() == [
            "agent_execution_total",
            "conversation_assistant_usage",
            "workflow_execution_total",
        ]

    def test_the_aggregation_names_match_the_analytics_spelling(self):
        """Copied rather than imported, so a test is what stops the two spellings drifting.

        The same guarantee `test_access_scope` gives for the project and user-id fields.
        """
        from codemie.service.analytics.handlers.money_coverage import SETTLED_AGG, UNSETTLED_AGG
        from codemie.service.spend.default_ledger import _SETTLED_AGG, _UNSETTLED_AGG

        assert _SETTLED_AGG == SETTLED_AGG
        assert _UNSETTLED_AGG == UNSETTLED_AGG


# ---------------------------------------------------------------------------
# The two whole-window reads a dashboard makes
# ---------------------------------------------------------------------------

CLI_METRIC = "codemie_litellm_proxy_usage"
CLI_FLAG_FIELD = "attributes.cli_request"
DATASOURCE_METRIC = "datasource_tokens_usage"

#: The pre-floor name set, built by the function production builds it with.
#:
#: **The precondition is the window, not a preference** - below `F` there are no `litellm_spend_logs`
#: rows, so nothing else reports that traffic and the exclusion would drop money rather than guard
#: anything. Read from `legacy_metric_names` rather than from `SPENDING_METRICS` alone, because the
#: union with the port's money table is what puts `agent_execution_total` - money in no metric group -
#: into the set: a test pinning the summary cards against the narrower set would pin a total
#: production never prints, and would miss that the cards do not foot below the floor.
LEGACY_NAMES = frozenset(legacy_metric_names(LLMCallClass))


def legacy_ledger(repo, *call_classes: LLMCallClass) -> DefaultSpendLedger:
    """The adapter as `composition` builds it below the coverage floor."""
    return DefaultSpendLedger(call_classes or (LLMCallClass.CHAT,), repository=repo, metric_names=LEGACY_NAMES)


def money_row(metric: str, money: str, **attributes) -> dict:
    """One money-bearing document of a named metric, inside the shared period."""
    return {"metric_name.keyword": metric, "@timestamp": MID_PERIOD, MONEY_FIELD: Decimal(money), **attributes}


def cli_row(money: str) -> dict:
    """A proxied call the CLI made - the flag every pre-cutover CLI surface filtered on."""
    return money_row(CLI_METRIC, money, **{CLI_FLAG_FIELD: True})


class _HistogramMetrics:
    """Answers a date histogram with prepared buckets, and records the bodies it was asked for.

    The buckets are prepared rather than binned from a row set on purpose: a stub re-implementing the
    binning would agree with the read under test *by construction*, and the alignment between the walk
    and the histogram - the one failure here that nothing raises on - would go untested. What the
    query asks for is asserted directly instead, and what comes back is the store's own answer shape.
    """

    def __init__(self, buckets: dict[datetime, dict] | None = None) -> None:
        self.buckets = dict(buckets or {})
        self.bodies: list[dict] = []

    async def execute_aggregation_query(self, body: dict, **_kwargs) -> dict:
        self.bodies.append(body)
        name = next(agg for agg, spec in body["aggs"].items() if "date_histogram" in spec)
        return {
            "aggregations": {
                name: {
                    "buckets": [
                        # `key` in epoch milliseconds beside `key_as_string`, exactly as Elasticsearch
                        # answers a date histogram - the read must take the former.
                        {"key": int(start.timestamp() * 1000), "key_as_string": start.isoformat(), **bucket}
                        for start, bucket in self.buckets.items()
                    ]
                }
            }
        }

    def histogram(self) -> dict:
        return self.bodies[0]["aggs"]["time_buckets"]["date_histogram"]

    def outer_filter(self) -> list[dict]:
        return self.bodies[0]["query"]["bool"]["filter"]


@pytest.fixture
def logs(monkeypatch):
    """Record what the adapter logged: the project's logging does not propagate to `caplog`."""
    recorded = SimpleNamespace(info=[], warning=[], debug=[])
    monkeypatch.setattr(
        default_ledger_module,
        "logger",
        SimpleNamespace(info=recorded.info.append, warning=recorded.warning.append, debug=recorded.debug.append),
    )
    return recorded


class TestTheBucketedSplitIsTheCliSeriesAndThreeAbsences:
    """The weekly chart below the floor: one line this store has a word for, three it does not."""

    @pytest.mark.parametrize(
        "width,fixed_interval,offset",
        [
            # Three hours tiles from the epoch itself, so nothing is shifted.
            ("3 hours", "10800s", "0s"),
            # A week tiles from the first Monday of the epoch - four days on - which is the instant
            # the walk floors to and `date_bin` bins from. A histogram left to pick its own boundary
            # would start every week on a Thursday and share no key with either.
            ("week", "604800s", "345600s"),
        ],
    )
    def test_the_histogram_is_binned_from_the_walks_own_stride_and_origin(self, width, fixed_interval, offset):
        repo = _HistogramMetrics()
        led = legacy_ledger(repo)

        asyncio.run(led.aggregate_by_time_bucket(PERIOD, width))

        assert repo.histogram()["field"] == "@timestamp", "grouping and bounding must read one date"
        assert repo.histogram()["fixed_interval"] == fixed_interval
        assert repo.histogram()["offset"] == offset

    def test_the_empty_buckets_are_asked_for_over_the_whole_walk(self):
        """Without these two the store returns only what it found, and a gap is indistinguishable.

        The bounds are the walk's own first and last start rather than the requested range, and this
        window is the case that separates them: a week bins `period.start` back to the Monday before
        it, so a range-bounded histogram is never asked about that first bucket and the measured zero
        the read substitutes for it is a guess.
        """
        repo = _HistogramMetrics()
        led = legacy_ledger(repo)

        asyncio.run(led.aggregate_by_time_bucket(PERIOD, "week"))

        walked = bucket_starts(PERIOD, "week")
        assert walked[0] < PERIOD.start, "the case a range-bounded histogram leaves outside itself"
        assert repo.histogram()["min_doc_count"] == 0
        assert repo.histogram()["extended_bounds"] == {
            "min": int(walked[0].timestamp() * 1000),
            "max": int(walked[-1].timestamp() * 1000),
        }

    def test_every_walked_bucket_is_present_for_every_class(self):
        """A chart handed a missing key draws a gap where the truth is a zero."""
        repo = _HistogramMetrics()
        led = legacy_ledger(repo)

        result = asyncio.run(led.aggregate_by_time_bucket(PERIOD, "week"))

        walked = bucket_starts(PERIOD, "week")
        assert len(walked) == 6, "the first start lies before the range: a week bins to its Monday"
        assert set(result) == {(bucket, cls) for bucket in walked for cls in TrafficClass}

    def test_the_cli_series_carries_the_money_and_the_other_three_stay_absent(self):
        """Three classes derived from metric names would be three confident wrong lines."""
        walked = bucket_starts(PERIOD, "week")
        repo = _HistogramMetrics({walked[1]: coverage(2.5, settled=2, unsettled=0)})
        led = legacy_ledger(repo)

        result = asyncio.run(led.aggregate_by_time_bucket(PERIOD, "week"))

        assert result[(walked[1], TrafficClass.CLI)].figure == Decimal("2.5")
        for cls in (TrafficClass.WORKFLOW, TrafficClass.ASSISTANT, TrafficClass.DATASOURCE):
            assert result[(walked[1], cls)].figure is None
            assert result[(walked[1], cls)].unavailable == PERIOD

    def test_a_bucket_the_store_returned_nothing_for_is_a_measured_zero(self):
        """`extended_bounds` spans the walk, so an unreturned key was asked for and found empty."""
        walked = bucket_starts(PERIOD, "week")
        repo = _HistogramMetrics({walked[0]: coverage(1.0, settled=1, unsettled=0)})
        led = legacy_ledger(repo)

        result = asyncio.run(led.aggregate_by_time_bucket(PERIOD, "week"))

        assert result[(walked[3], TrafficClass.CLI)].figure == Decimal("0")
        assert result[(walked[3], TrafficClass.CLI)].unavailable is None

    def test_a_bucket_where_nothing_settled_is_absent_and_a_partly_settled_one_keeps_its_money(self):
        """The `sum` skipped every document of the first, so its `0.0` measures nothing."""
        walked = bucket_starts(PERIOD, "week")
        repo = _HistogramMetrics(
            {
                walked[1]: coverage(0.0, settled=0, unsettled=2),
                walked[2]: coverage(4.0, settled=1, unsettled=1),
            }
        )
        led = legacy_ledger(repo)

        result = asyncio.run(led.aggregate_by_time_bucket(PERIOD, "week"))

        assert result[(walked[1], TrafficClass.CLI)].figure is None
        assert result[(walked[1], TrafficClass.CLI)].unavailable == PERIOD
        assert result[(walked[2], TrafficClass.CLI)].figure == Decimal("4"), "settled money is real"

    def test_the_read_is_the_cli_predicate_the_pre_cutover_surfaces_filtered_on(self):
        repo = _HistogramMetrics()
        led = legacy_ledger(repo)

        asyncio.run(led.aggregate_by_time_bucket(PERIOD, "week"))

        assert {"term": {"metric_name.keyword": CLI_METRIC}} in repo.outer_filter()
        assert {"term": {CLI_FLAG_FIELD: True}} in repo.outer_filter()

    def test_the_boundary_and_the_filter_both_reach_the_outer_query(self):
        """A boundary says what the caller may see; a filter says what they asked for."""
        repo = _HistogramMetrics()
        led = legacy_ledger(repo)
        access = AccessScope(unrestricted=False, user_id=USER, plain_projects=("proj-a",))
        report_filter = ReportFilter(users=(UserTerm(user_id=USER, aliases=frozenset()),), projects=("proj-b",))

        asyncio.run(led.aggregate_by_time_bucket(PERIOD, "week", None, access, report_filter))

        boundary = access.elasticsearch_filter(
            user_id_field="attributes.user_id.keyword", project_field="attributes.project.keyword"
        )
        assert boundary in repo.outer_filter(), "the boundary's OR stays parenthesised inside the AND"
        assert {"terms": {"attributes.user_id.keyword": [USER]}} in repo.outer_filter()
        assert {"terms": {"attributes.project.keyword": ["proj-b"]}} in repo.outer_filter()

    def test_without_the_proxy_metric_no_query_is_issued_and_every_key_is_absent(self):
        """A sum over this adapter's own rows would report `$0.00` for money one filter away."""
        led, repo = ledger(LLMCallClass.CHAT, money=99)

        result = asyncio.run(led.aggregate_by_time_bucket(PERIOD, "week"))

        assert repo.bodies == [], "money this adapter may not report costs no query"
        assert all(entry.figure is None for entry in result.values())
        assert all(entry.unavailable == PERIOD for entry in result.values())

    def test_a_bucket_the_walk_cannot_place_is_reported_rather_than_dropped(self, logs):
        """Dropping one is otherwise silent and reads downstream as a bucket that held nothing."""
        stray = datetime(2026, 7, 30, tzinfo=timezone.utc)
        repo = _HistogramMetrics({stray: coverage(7.0, settled=1, unsettled=0)})
        led = legacy_ledger(repo)

        result = asyncio.run(led.aggregate_by_time_bucket(PERIOD, "week"))

        assert stray not in {bucket for bucket, _ in result}
        assert len(logs.warning) == 1
        assert repr(stray) in logs.warning[0], "the warning names the buckets, so the money can be found"


class TestTheSummaryCards:
    """One scan, four figures, and the omission rule that keeps a card from being flagged wrongly."""

    @staticmethod
    def _cards() -> tuple[dict, _RowSetMetrics]:
        repo = _RowSetMetrics(
            [
                money_row("conversation_assistant_usage", "1.5"),
                money_row(DATASOURCE_METRIC, "2"),
                cli_row("3"),
                # Money in the pre-floor name set and in no metric group, so in the total and in no
                # card: the reason the four figures do not foot.
                money_row("agent_execution_total", "0.5"),
            ]
        )
        led = legacy_ledger(repo, LLMCallClass.CHAT, LLMCallClass.EMBEDDINGS)
        return asyncio.run(led.aggregate_by_spend_group(PERIOD)), repo

    def test_all_four_cards_come_from_one_scan_and_the_total_exceeds_platform_plus_cli(self):
        """The cards do not foot below the floor, and the remainder is nameable rather than an error.

        `legacy_metric_names` unions the surfaces' spending metrics with the port's money table, and
        the union carries `agent_execution_total` - money in no metric group, so counted by the total
        and by no card. `EMBEDDING` is a slice of `PLATFORM` besides, which is why `spend_group`
        forbids a consumer differencing any two of these.
        """
        result, repo = self._cards()

        assert list(result) == [SpendGroup.TOTAL, SpendGroup.PLATFORM, SpendGroup.CLI, SpendGroup.EMBEDDING]
        assert result[SpendGroup.TOTAL].figure == Decimal("7")
        assert result[SpendGroup.PLATFORM].figure == Decimal("3.5")
        assert result[SpendGroup.CLI].figure == Decimal("3")
        assert result[SpendGroup.EMBEDDING].figure == Decimal("2")
        assert result[SpendGroup.TOTAL].figure == (
            result[SpendGroup.PLATFORM].figure + result[SpendGroup.CLI].figure + Decimal("0.5")
        )
        assert len(repo.bodies) == 1, "four cards on one screen cannot be compared if they are four scans"

    def test_each_card_is_the_filter_that_card_was_always_built_from(self):
        _result, repo = self._cards()

        assert repo.bodies[0]["aggs"]["spend_groups"]["filters"]["filters"] == {
            # No clause: `_query` already bounds the read by the name set, which is the guard.
            "total": {"bool": {"filter": []}},
            "platform": {
                "bool": {
                    "filter": [
                        {"terms": {"metric_name.keyword": MetricName.to_list_from_group(MetricName.PLATFORM_METRICS)}}
                    ]
                }
            },
            "cli": {
                "bool": {
                    "filter": [
                        {"term": {"metric_name.keyword": CLI_METRIC}},
                        {"term": {CLI_FLAG_FIELD: True}},
                    ]
                }
            },
            "embedding": {"bool": {"filter": [{"term": {"metric_name.keyword": DATASOURCE_METRIC}}]}},
        }

    def test_the_total_is_this_adapters_name_set_and_never_the_whole_index(self):
        """A `match_all` total would sum the LiteLLM-routed traffic the spend-logs ledger reports."""
        repo = _RowSetMetrics([money_row("conversation_assistant_usage", "1.5"), money_row("cli_session_total", "100")])
        led = legacy_ledger(repo, LLMCallClass.CHAT)

        result = asyncio.run(led.aggregate_by_spend_group(PERIOD))

        assert result[SpendGroup.TOTAL].figure == Decimal("1.5"), "100 here would be a document outside the guard"
        assert {"terms": {"metric_name.keyword": sorted(LEGACY_NAMES)}} in repo.bodies[0]["query"]["bool"]["filter"]

    def test_an_embeddings_only_adapter_omits_the_cli_card_rather_than_flagging_it(self):
        """CLI traffic is chat traffic: it was never this adapter's to report, absent or otherwise."""
        repo = _RowSetMetrics([money_row(DATASOURCE_METRIC, "2")])
        led = legacy_ledger(repo, LLMCallClass.EMBEDDINGS)

        result = asyncio.run(led.aggregate_by_spend_group(PERIOD))

        assert set(result) == {SpendGroup.TOTAL, SpendGroup.PLATFORM, SpendGroup.EMBEDDING}
        assert result[SpendGroup.EMBEDDING].figure == Decimal("2")

    def test_a_chat_adapter_without_the_proxy_metric_answers_the_cli_card_absent(self):
        """A group its classes carry but it cannot read is absent - the fabricated `$0.00` refused."""
        repo = _RowSetMetrics([money_row("conversation_assistant_usage", "1.5")])
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=repo)

        result = asyncio.run(led.aggregate_by_spend_group(PERIOD))

        assert set(result) == {SpendGroup.TOTAL, SpendGroup.PLATFORM, SpendGroup.CLI}
        assert result[SpendGroup.CLI].figure is None
        assert result[SpendGroup.CLI].unavailable == PERIOD
        assert SpendGroup.EMBEDDING not in result, "embeddings are another adapter's to report"

    def test_an_embeddings_adapter_without_the_datasource_metric_answers_the_embedding_card_absent(self):
        """The CLI gate asked of the other card: an owned card it cannot read is absent, not a zero.

        The rows are there and the money is real; what the adapter may not do is read a name outside
        its own set, so a sum over its own rows would publish `$0.00` for money one filter away.
        """
        repo = _RowSetMetrics([money_row(DATASOURCE_METRIC, "2")])
        led = DefaultSpendLedger(
            [LLMCallClass.EMBEDDINGS],
            repository=repo,
            metric_names=frozenset({"conversation_assistant_usage"}),
        )

        result = asyncio.run(led.aggregate_by_spend_group(PERIOD))

        assert result[SpendGroup.EMBEDDING].figure is None
        assert result[SpendGroup.EMBEDDING].unavailable == PERIOD
        assert "embedding" not in repo.bodies[0]["aggs"]["spend_groups"]["filters"]["filters"]

    def test_a_window_where_nothing_settled_answers_no_figure_for_every_card_it_answers(self):
        """The `sum` skipped every document, so its `0.0` is not a measurement of anything."""
        repo = _RowSetMetrics([unsettled_row("asst-1")])
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=repo)

        result = asyncio.run(led.aggregate_by_spend_group(PERIOD))

        assert all(entry.figure is None for entry in result.values())
        assert all(entry.unavailable == PERIOD for entry in result.values())

    def test_the_boundary_and_the_filter_both_reach_the_outer_query(self):
        repo = _RowSetMetrics([])
        led = legacy_ledger(repo, LLMCallClass.CHAT)
        access = AccessScope(unrestricted=False, user_id=USER, plain_projects=("proj-a",))
        report_filter = ReportFilter(users=(UserTerm(user_id=USER, aliases=frozenset()),), projects=("proj-b",))

        asyncio.run(led.aggregate_by_spend_group(PERIOD, access=access, report_filter=report_filter))

        outer = repo.bodies[0]["query"]["bool"]["filter"]
        boundary = access.elasticsearch_filter(
            user_id_field="attributes.user_id.keyword", project_field="attributes.project.keyword"
        )
        assert boundary in outer, "one clause in the shared filter, never one per card"
        assert {"terms": {"attributes.user_id.keyword": [USER]}} in outer
        assert {"terms": {"attributes.project.keyword": ["proj-b"]}} in outer


def user_filter(user: str) -> ReportFilter:
    return ReportFilter(users=(UserTerm(user_id=user, aliases=frozenset()),))


class _OutageForUser:
    """A `_RowSetMetrics` whose store raises on any search naming one user and answers every other as usual."""

    def __init__(self, repo: _RowSetMetrics, user: str, error: Exception) -> None:
        self._repo = repo
        self._user = user
        self.error = error

    async def execute_aggregation_query(self, body: dict) -> dict:
        if self._user in json.dumps(body, default=str):
            raise self.error
        return await self._repo.execute_aggregation_query(body)


PROJECT_FIELD = "attributes.project.keyword"


def person(user: str) -> UserTerm:
    return UserTerm(user_id=user, aliases=frozenset())


def clause_set(clauses: list[dict]) -> set[str]:
    return {json.dumps(clause, sort_keys=True, default=str) for clause in clauses}


def row_buckets(body: dict) -> dict[str, dict]:
    return body["aggs"]["rows"]["filters"]["filters"]


class TestTheSummaryCardsPerFilter:
    """The batched card read on this store answers each filter exactly as its single read does."""

    ROWS = [
        money_row("conversation_assistant_usage", "1.5", **{_USER_ID_FIELD: USER}),
        money_row("conversation_assistant_usage", "2", **{_USER_ID_FIELD: OTHER_USER}),
        money_row("conversation_assistant_usage", "4", **{_USER_ID_FIELD: FAILING_USER}),
    ]

    #: Two people over two projects, with CLI and embedding money, and one document of `OTHER_USER`'s in
    #: `proj-b` whose cost never settled - so a row can be whole, a subtotal, or no figure at all.
    MIXED_ROWS = [
        money_row("conversation_assistant_usage", "1.5", **{_USER_ID_FIELD: USER, PROJECT_FIELD: "proj-a"}),
        money_row("conversation_assistant_usage", "0.25", **{_USER_ID_FIELD: USER, PROJECT_FIELD: "proj-b"}),
        money_row(CLI_METRIC, "3", **{_USER_ID_FIELD: USER, PROJECT_FIELD: "proj-a", CLI_FLAG_FIELD: True}),
        money_row("conversation_assistant_usage", "2", **{_USER_ID_FIELD: OTHER_USER, PROJECT_FIELD: "proj-a"}),
        money_row(DATASOURCE_METRIC, "0.5", **{_USER_ID_FIELD: OTHER_USER, PROJECT_FIELD: "proj-a"}),
        {
            "metric_name.keyword": "conversation_assistant_usage",
            "@timestamp": MID_PERIOD,
            _USER_ID_FIELD: OTHER_USER,
            PROJECT_FIELD: "proj-b",
        },
    ]

    SCENARIOS = {
        "user rows sharing a projects term": (
            ReportFilter(projects=("proj-a",)),
            [
                ReportFilter(users=(person(USER),), projects=("proj-a",)),
                ReportFilter(users=(person(OTHER_USER),), projects=("proj-a",)),
            ],
            None,
        ),
        "project rows sharing a users term": (
            ReportFilter(users=(person(OTHER_USER),)),
            [
                ReportFilter(users=(person(OTHER_USER),), projects=("proj-a",)),
                ReportFilter(users=(person(OTHER_USER),), projects=("proj-b",)),
            ],
            None,
        ),
        "an empty common filter": (None, [user_filter(USER), user_filter(OTHER_USER), ReportFilter()], None),
        "restricted access": (
            None,
            [user_filter(USER), user_filter(OTHER_USER), ReportFilter()],
            AccessScope(unrestricted=False, user_id=USER, plain_projects=("proj-a",)),
        ),
        "unrestricted access": (
            None,
            [user_filter(USER), user_filter(OTHER_USER), ReportFilter()],
            AccessScope(unrestricted=True),
        ),
    }

    LEDGERS = {
        "every group readable": lambda repo: legacy_ledger(repo, LLMCallClass.CHAT, LLMCallClass.EMBEDDINGS),
        "a CLI card it cannot read and no embedding card": lambda repo: DefaultSpendLedger(
            [LLMCallClass.CHAT], repository=repo
        ),
    }

    @staticmethod
    def _read_per_filter(led: DefaultSpendLedger, filters: list[ReportFilter], common_filter=None, **kwargs):
        return asyncio.run(
            led.aggregate_by_spend_group_per_filter(
                PERIOD, common_filter=common_filter, report_filters=filters, **kwargs
            )
        )

    @staticmethod
    def _single_reads(led: DefaultSpendLedger, filters: list[ReportFilter], access) -> dict:
        return {
            report_filter: asyncio.run(led.aggregate_by_spend_group(PERIOD, access=access, report_filter=report_filter))
            for report_filter in filters
        }

    @pytest.mark.parametrize("ledger_shape", list(LEDGERS))
    @pytest.mark.parametrize("scenario", list(SCENARIOS))
    def test_each_filter_is_answered_exactly_as_its_single_read_answers_it(self, scenario, ledger_shape):
        common_filter, filters, access = self.SCENARIOS[scenario]
        led = self.LEDGERS[ledger_shape](_RowSetMetrics(self.MIXED_ROWS))
        single_reads = self._single_reads(led, filters, access)

        result = self._read_per_filter(led, filters, common_filter, access=access)

        assert result == single_reads

    def test_a_row_answers_whole_subtotal_and_no_figure_as_its_documents_settled(self):
        common_filter, filters, access = self.SCENARIOS["project rows sharing a users term"]
        led = legacy_ledger(_RowSetMetrics(self.MIXED_ROWS), LLMCallClass.CHAT, LLMCallClass.EMBEDDINGS)

        result = self._read_per_filter(led, [*filters, user_filter(OTHER_USER)], common_filter, access=access)

        assert result[filters[0]][SpendGroup.TOTAL].figure == Decimal("2.5")
        assert result[filters[1]][SpendGroup.TOTAL] == SpendResult.no_figure(PERIOD)
        assert result[user_filter(OTHER_USER)][SpendGroup.TOTAL].figure == Decimal("2.5")

    @pytest.mark.parametrize("scenario", list(SCENARIOS))
    def test_the_outer_query_and_a_row_bucket_select_exactly_what_the_single_read_selects(self, scenario):
        common_filter, filters, access = self.SCENARIOS[scenario]
        repo = _RowSetMetrics(self.MIXED_ROWS)
        led = legacy_ledger(repo, LLMCallClass.CHAT)
        self._single_reads(led, filters, access)
        single_queries = dict(zip(filters, (body["query"]["bool"]["filter"] for body in repo.bodies), strict=True))

        self._read_per_filter(led, filters, common_filter, access=access)

        batch = repo.bodies[-1]
        outer = batch["query"]["bool"]["filter"]
        buckets = dict(zip(filters, (b["bool"]["filter"] for b in row_buckets(batch).values()), strict=True))
        assert {f: clause_set(outer) | clause_set(clauses) for f, clauses in buckets.items()} == {
            f: clause_set(query) for f, query in single_queries.items()
        }
        assert buckets == {
            f: f.elasticsearch_filters(user_id_field=_USER_ID_FIELD, project_field=PROJECT_FIELD) for f in filters
        }

    @pytest.mark.parametrize("scenario", list(SCENARIOS))
    def test_the_outer_query_is_the_single_read_of_the_common_filter(self, scenario):
        common_filter, filters, access = self.SCENARIOS[scenario]
        repo = _RowSetMetrics([])
        led = legacy_ledger(repo, LLMCallClass.CHAT)
        asyncio.run(led.aggregate_by_spend_group(PERIOD, access=access, report_filter=common_filter))

        self._read_per_filter(led, filters, common_filter, access=access)

        common_read, batch = repo.bodies
        assert batch["query"] == common_read["query"]

    def test_each_row_bucket_holds_the_spend_group_buckets_of_the_single_read(self):
        repo = _RowSetMetrics([])
        led = legacy_ledger(repo, LLMCallClass.CHAT, LLMCallClass.EMBEDDINGS)
        asyncio.run(led.aggregate_by_spend_group(PERIOD))

        self._read_per_filter(led, [user_filter(USER), user_filter(OTHER_USER)])

        single_read, batch = repo.bodies
        assert batch["aggs"]["rows"]["aggs"] == single_read["aggs"]

    def test_a_page_of_filters_is_one_search_with_positional_bucket_names(self):
        repo = _RowSetMetrics([])
        led = legacy_ledger(repo, LLMCallClass.CHAT)
        filters = [user_filter(f"user-{index}") for index in range(FILTER_BATCH_SIZE)]

        result = self._read_per_filter(led, filters)

        assert len(repo.bodies) == 1
        assert list(row_buckets(repo.bodies[0])) == [f"row_{index}" for index in range(FILTER_BATCH_SIZE)]
        assert result[filters[-1]][SpendGroup.TOTAL].figure == Decimal("0")

    def test_one_filter_past_a_page_is_a_second_search_holding_only_that_filter(self):
        repo = _RowSetMetrics([])
        led = legacy_ledger(repo, LLMCallClass.CHAT)
        filters = [user_filter(f"user-{index}") for index in range(FILTER_BATCH_SIZE + 1)]

        result = self._read_per_filter(led, filters)

        assert [len(row_buckets(body)) for body in repo.bodies] == [FILTER_BATCH_SIZE, 1]
        assert len(result) == FILTER_BATCH_SIZE + 1

    def test_a_page_stays_within_the_default_bucket_limit(self):
        """Each row costs its own bucket and one per spend group; Elasticsearch refuses a search past 65,536."""
        assert FILTER_BATCH_SIZE * (1 + len(SpendGroup)) < 65_536

    def test_duplicate_filters_are_read_once(self):
        repo = _RowSetMetrics(self.ROWS)
        led = legacy_ledger(repo, LLMCallClass.CHAT)

        result = self._read_per_filter(led, [user_filter(USER), user_filter(USER), user_filter(OTHER_USER)])

        assert list(result) == [user_filter(USER), user_filter(OTHER_USER)]
        assert list(row_buckets(repo.bodies[0])) == ["row_0", "row_1"]

    def test_no_filters_make_no_search(self):
        repo = _RowSetMetrics(self.ROWS)
        led = legacy_ledger(repo, LLMCallClass.CHAT)

        result = self._read_per_filter(led, [])

        assert result == {}
        assert repo.bodies == []

    def test_a_card_it_cannot_read_is_logged_once_per_batch_rather_than_per_row(self):
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=_RowSetMetrics(self.ROWS))

        with patch.object(default_ledger_module, "logger") as ledger_logger:
            self._read_per_filter(led, [user_filter(USER), user_filter(OTHER_USER), user_filter(FAILING_USER)])

        ledger_logger.debug.assert_called_once()

    def test_a_failing_page_costs_its_own_rows_and_nobody_elses(self):
        outage = _OutageForUser(_RowSetMetrics(self.ROWS), FAILING_USER, RuntimeError("elasticsearch is down"))
        led = legacy_ledger(outage, LLMCallClass.CHAT)
        failing_in_project = ReportFilter(users=(person(FAILING_USER),), projects=("proj-a",))
        filters = [user_filter(USER), user_filter(OTHER_USER), user_filter(FAILING_USER), failing_in_project]

        with patch.object(default_ledger_module, "FILTER_BATCH_SIZE", 2):
            with patch("codemie.service.spend.read_failures.BaseMonitoringService") as monitoring:
                with patch("codemie.service.spend.read_failures.logger") as failure_logger:
                    result = self._read_per_filter(led, filters)

        assert list(result) == filters
        assert result[user_filter(USER)][SpendGroup.TOTAL].figure == Decimal("1.5")
        assert result[user_filter(OTHER_USER)][SpendGroup.TOTAL].figure == Decimal("2")
        assert result[user_filter(FAILING_USER)] == {group: SpendResult.no_figure(PERIOD) for group in SpendGroup}
        assert result[failing_in_project] == {group: SpendResult.no_figure(PERIOD) for group in SpendGroup}
        monitoring.send_count_metric.assert_called_once_with(
            name=SPEND_LEDGER_READ_FAILURES_METRIC,
            attributes={"ledger": led.ledger_name, "read": "aggregate_by_spend_group_per_filter"},
            count=2,
        )
        failure_logger.error.assert_called_once()
        assert failure_logger.error.call_args.kwargs["exc_info"] is outage.error

    def test_every_page_failing_is_an_outage_the_caller_counts_and_not_a_figure(self):
        error = RuntimeError("elasticsearch is down")
        outage = _OutageForUser(_RowSetMetrics(self.ROWS), FAILING_USER, error)
        led = legacy_ledger(outage, LLMCallClass.CHAT)
        filters = [user_filter(FAILING_USER), ReportFilter(users=(person(FAILING_USER),), projects=("proj-a",))]

        with patch.object(default_ledger_module, "FILTER_BATCH_SIZE", 1):
            with patch("codemie.service.spend.read_failures.BaseMonitoringService") as monitoring:
                with pytest.raises(RuntimeError) as raised:
                    self._read_per_filter(led, filters)

        assert raised.value is error
        monitoring.send_count_metric.assert_not_called()


class TestThePerModelTable:
    """`SpendDimension.MODEL` as a partition of two attributes: one key per document, never two.

    The dimension means *the model a call was billed under*, which is the ledger's own `model_group`
    and one key per row. In the store that is `llm_model` on every money-bearing document except the
    aggregate datasource one, which carries `embeddings_model` alone - so the partition keys by the
    first wherever a document has it and by the second only where it has none.
    """

    def test_a_named_set_of_models_is_grouped_on_the_model_partition(self):
        repo = _RowSetMetrics([])
        led = legacy_ledger(repo, LLMCallClass.EMBEDDINGS)

        asyncio.run(led.aggregate_by_dimension(PERIOD, SpendDimension.MODEL, ["codemie-text-embedding-ada-002"]))

        assert repo.bodies[0]["aggs"]["values"]["filters"]["filters"] == {
            "value_0": {"bool": {"filter": [model_filter("codemie-text-embedding-ada-002")]}}
        }

    def test_a_chat_models_money_is_read_from_the_llm_model_attribute(self):
        """Keyed on `embeddings_model` alone, every chat model read as a measured `$0.00`.

        Only datasource documents carry that attribute, so a chat model name matched nothing at all
        and the empty bucket - correctly, on this store's footing - answered zero over money sitting
        one field away. That is the fabricated zero arriving through a filter rather than a formula.
        """
        rows = [money_row("conversation_assistant_usage", "4", **{LLM_MODEL_FIELD: "gpt-4o"})]
        led = legacy_ledger(_RowSetMetrics(rows), LLMCallClass.CHAT)

        result = asyncio.run(led.aggregate_by_dimension(PERIOD, SpendDimension.MODEL, ["gpt-4o"]))

        assert result["gpt-4o"].figure == Decimal("4")

    def test_a_document_carrying_both_attributes_counts_under_its_llm_model_alone(self):
        """A plain OR over the two fields would report this one document's money twice.

        The per-model datasource emitter writes the summarising LLM beside the index model, so both
        names are asked for here and only one may carry the figure - otherwise a table summing its
        own rows comes out at double the money the store holds.
        """
        rows = [
            money_row(
                DATASOURCE_METRIC,
                "6",
                **{LLM_MODEL_FIELD: "gpt-4o-mini", EMBEDDINGS_MODEL_FIELD: "ada-002"},
            )
        ]
        led = legacy_ledger(_RowSetMetrics(rows), LLMCallClass.EMBEDDINGS)

        result = asyncio.run(led.aggregate_by_dimension(PERIOD, SpendDimension.MODEL, ["gpt-4o-mini", "ada-002"]))

        assert result["gpt-4o-mini"].figure == Decimal("6")
        assert result["ada-002"].figure == Decimal("0"), "this document's money is already under its llm_model key"

    def test_a_model_the_store_holds_nothing_for_is_a_measured_zero_and_not_an_absence(self):
        """There is no coverage floor here: no document at all means nothing was recorded."""
        rows = [money_row(DATASOURCE_METRIC, "2", **{EMBEDDINGS_MODEL_FIELD: "ada-002"})]
        led = legacy_ledger(_RowSetMetrics(rows), LLMCallClass.EMBEDDINGS)

        result = asyncio.run(led.aggregate_by_dimension(PERIOD, SpendDimension.MODEL, ["ada-002", "ada-003"]))

        assert result["ada-002"].figure == Decimal("2")
        assert result["ada-003"].figure == Decimal("0")
        assert result["ada-003"].unavailable is None
