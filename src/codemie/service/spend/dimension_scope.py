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

"""The Elasticsearch branch of AD-29's dimension union - P10's id resolver.

**This module answers exactly one question:** for a dimension, a scope value and a report range,
which `codemie_request_id` values does the metric store hold? Nothing else. It never sums, never
counts, never touches `attributes.money_spent` - AD-29/CL-48 forbids that field as a numeric
operand anywhere, and this module has no numeric operand at all. The caller turns the returned
ids into money by handing them to `SpendLedger`, which is the only thing allowed to.

**The window is not the report range.** A metric document's `@timestamp` is *emission* time, and
conversation and workflow metrics are emitted at *completion* - a call starting at 23:59 on a
period's last day emits its metric into the next period. `@timestamp` never assigns a row to a
period; only `litellm_spend_logs.start_time` does, on the ledger side, elsewhere. This module's
only job is to be a **superset**: the report range widened on both ends by the emission-lag
allowance `L` (`TimeRange.widened`, already built - reused rather than reimplemented). Under-
widening moves a row into the P11 residue silently; over-widening only costs a bigger id set,
because the ledger's own `start_time` predicate discards the extras.

**Never truncates.** If a scope's id set is large, this module pages through it with an
Elasticsearch composite aggregation until exhausted, however many round trips that takes - "a
truncated total is indistinguishable from a small one." `page_size` (the caller's `N`) sizes each
page; it is never a cap on the total returned.

**Which dimension resolves how:**

- `assistant`, `conversation` - the metric store carries a stable id on the money-bearing
  document (`MetricsAttributes.ASSISTANT_ID` / `CONVERSATION_ID`, emitted alongside
  `MONEY_SPENT` in `conversation_monitoring_service.py`), so the scope value *is* that id.
- `execution` - likewise `MetricsAttributes.EXECUTION_ID` (`workflow_monitoring_service.py`).
- `workflow` - **two lookups, unioned, because one ledger-column rollout and one Elasticsearch-
  attribute rollout happened at different times and neither retrofits the other's history.**
  `MetricsAttributes.WORKFLOW_ID` is now emitted (`workflow_monitoring_service.py`, using the
  constant defined at `metrics_constants.py`) precisely so the union's NULL branch has a stable
  id to match `scope.value` against, the same way `assistant`/`conversation`/`execution` do - so
  that lookup always runs. But a document emitted *before* that attribute existed carries only
  the mutable, non-unique `workflow_name`, which the id can never retrofit onto already-written
  history. For that population only, a `workflow_name` resolving to anything other than *exactly
  one* live workflow id must yield NO bucket assignment - a rename, or one name shared by two
  live ids - never every matching id's bucket and never an arbitrarily chosen one. Deciding
  whether a name is currently safe to search by needs a live workflow directory this module does
  not have and is not wired to (composition is a later wave); `resolve_metric_store_ids` therefore
  takes the **already-disambiguated** name as `resolved_workflow_name` and only queries by it when
  one is supplied - given none, the id-based lookup alone still runs and the name-only historical
  rows correctly land in the P11 residue instead of a wrong or duplicated bucket.
- `datasource` - **verified absent, not merely unmodelled.** `datasource_monitoring_service.py`
  emits `datasource_type`, `project`, `repo_name`, `embeddings_model`, `user_name`, `user_id` -
  no datasource id and no `MetricsAttributes.REQUEST_ID` correlation attribute either. AD-29
  rejects a `codemie_datasource_id` ledger column on the same ground ("no consumer groups by
  it... Elasticsearch cannot supply one"). There is nothing to query, so this dimension always
  resolves to no ids - the branch is self-quiescing by construction, not a defect here.

**Permanent, with no exit condition.** AD-29 forbids retiring this branch once NULLs reach zero -
a call-site regression can stop sending a dimension key at any time and repopulate them. When no
in-scope row has a NULL dimension column this module's queries simply match nothing, at no extra
cost. Nothing here counts rows toward a retirement instant, and nothing should.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.service.monitoring.metrics_constants import MetricsAttributes
from codemie.service.spend.coverage import (
    DEFAULT_EMISSION_LAG,
    DEFAULT_MAX_ELASTICSEARCH_IDS,
    SpendDimension,
    TimeRange,
)
from codemie.service.spend.ledger import DimensionScope

_TIMESTAMP_FIELD = "@timestamp"
_REQUEST_ID_FIELD = f"attributes.{MetricsAttributes.REQUEST_ID}.keyword"
_WORKFLOW_ID_FIELD = f"attributes.{MetricsAttributes.WORKFLOW_ID}.keyword"
_WORKFLOW_NAME_FIELD = f"attributes.{MetricsAttributes.WORKFLOW_NAME}.keyword"

#: Dimensions Elasticsearch can identify directly by a stable id it already carries on the
#: money-bearing metric document. `workflow` is handled separately (`_lookups`) because it needs
#: a second, name-based lookup for the population `workflow_id` cannot retrofit onto; `datasource`
#: is absent because no id exists there at all (module docstring).
_DIMENSION_ID_FIELDS: dict[SpendDimension, str] = {
    SpendDimension.ASSISTANT: f"attributes.{MetricsAttributes.ASSISTANT_ID}.keyword",
    SpendDimension.CONVERSATION: f"attributes.{MetricsAttributes.CONVERSATION_ID}.keyword",
    SpendDimension.EXECUTION: f"attributes.{MetricsAttributes.EXECUTION_ID}.keyword",
}


def _lookups(scope: DimensionScope, resolved_workflow_name: str | None) -> list[tuple[str, str | None]]:
    """The (field, value) pairs to query Elasticsearch by and union.

    Every dimension but `workflow` resolves to at most one pair; an empty list is what makes an
    unresolvable scope a measured zero rather than an error - `datasource` has no field ever.
    `workflow` can resolve to two: the id-based lookup always runs (it is the id-emitting
    population's exact match, the same shape as `assistant`/`conversation`/`execution`), and the
    name-based lookup joins it only when the caller has already disambiguated a name, covering the
    pre-id-emission population the id lookup cannot reach.
    """
    if scope.dimension is SpendDimension.WORKFLOW:
        lookups = [(_WORKFLOW_ID_FIELD, scope.value)]
        if resolved_workflow_name is not None:
            lookups.append((_WORKFLOW_NAME_FIELD, resolved_workflow_name))
        return lookups
    field = _DIMENSION_ID_FIELDS.get(scope.dimension)
    return [] if field is None else [(field, scope.value)]


def _scope_filter(field: str, value: str | None) -> dict:
    """A specific value narrows by `term`; `None` (a grouped, all-values scope) narrows by
    `exists` - the Elasticsearch mirror of the ledger branch's bare `column.is_not(None)`."""
    return {"exists": {"field": field}} if value is None else {"term": {field: value}}


async def _collect_ids(
    repository: MetricsElasticRepository,
    field: str,
    value: str | None,
    window: TimeRange,
    page_size: int,
) -> list[str]:
    """Every `codemie_request_id` matching one (field, value) lookup over `window`, exhaustively.

    A composite aggregation's `after_key` is the only exhaustion signal it gives - it never
    reports a total - so a short page is what ends the loop, never a fixed number of round trips.
    """
    query_filter = [
        {"range": {_TIMESTAMP_FIELD: {"gte": window.start.isoformat(), "lt": window.end.isoformat()}}},
        _scope_filter(field, value),
    ]

    resolved_ids: list[str] = []
    after: dict | None = None
    while True:
        composite: dict = {"size": page_size, "sources": [{"id": {"terms": {"field": _REQUEST_ID_FIELD}}}]}
        if after is not None:
            composite["after"] = after
        response = await repository.execute_aggregation_query(
            {"size": 0, "query": {"bool": {"filter": query_filter}}, "aggs": {"ids": {"composite": composite}}}
        )
        ids_agg = response.get("aggregations", {}).get("ids", {})
        buckets = ids_agg.get("buckets", [])
        resolved_ids.extend(bucket["key"]["id"] for bucket in buckets)
        # A full page may have more behind it; anything shorter than what was asked for means
        # nothing is left. Page again rather than stop and drop the rest.
        if len(buckets) < page_size:
            break
        after = ids_agg.get("after_key")
        if after is None:
            break
    return resolved_ids


async def resolve_metric_store_ids(
    repository: MetricsElasticRepository,
    scope: DimensionScope,
    period: TimeRange,
    *,
    emission_lag: timedelta = DEFAULT_EMISSION_LAG,
    page_size: int = DEFAULT_MAX_ELASTICSEARCH_IDS,
    resolved_workflow_name: str | None = None,
) -> tuple[str, ...]:
    """The `codemie_request_id` values Elasticsearch holds for this dimension scope and range.

    `emission_lag` (`L`) and `page_size` (`N`) are the two config-derived values a composition
    root reads once and passes down (`LedgerCoverage.emission_lag` / `.max_elasticsearch_ids`) -
    never re-read from config here, and never a report caller's argument. `resolved_workflow_name`
    only applies to the `workflow` dimension; see the module docstring for why this module does
    not resolve it itself.

    The returned ids are a **superset candidate set**, nothing more: the caller's own
    `start_time` predicate is what actually assigns a row to the period, so an id resolved here
    for a row that in fact belongs to an adjacent period is simply discarded there, at no cost
    to correctness - which is exactly why the window below only ever needs to widen, never match.
    """
    lookups = _lookups(scope, resolved_workflow_name)
    if not lookups:
        return ()

    # @timestamp is emission time, never period-assigning - widen so this window is a superset
    # of the report range rather than reusing it verbatim (AD-29's F4-1 failure shape).
    window = period.widened(emission_lag)

    # Deduped by insertion order, not a set: two lookups (the `workflow` case) can legitimately
    # both match the same id and the union must still count it once, but the order otherwise
    # carries no meaning either way.
    resolved_ids: dict[str, None] = {}
    for field, value in lookups:
        for one_id in await _collect_ids(repository, field, value, window, page_size):
            resolved_ids[one_id] = None
    return tuple(resolved_ids)


async def resolve_metric_store_ids_by_value(
    repository: MetricsElasticRepository,
    dimension: SpendDimension,
    values: Sequence[str],
    period: TimeRange,
    *,
    emission_lag: timedelta = DEFAULT_EMISSION_LAG,
    page_size: int = DEFAULT_MAX_ELASTICSEARCH_IDS,
) -> dict[str, tuple[str, ...]]:
    """`resolve_metric_store_ids` for a **named set** of values, in **one** query.

    **One paged read for the whole value set, not one per value, and that is the whole reason this
    exists.** A grouped read over a thousand assistants resolved value by value would move the
    thousand round trips off PostgreSQL and onto Elasticsearch, which is not a saving. The shape is a
    **composite** aggregation over two sources - the dimension's id field and `codemie_request_id` -
    so one paged stream carries every (value, id) pair, with the requested values narrowed in the
    query filter.

    **A `terms` aggregation with a nested `terms` was the obvious shape and it is the wrong one.**
    A sub-aggregation has no `after_key`, so a value holding more correlation ids than its `size`
    loses the remainder silently - and `N` is *"a paging threshold, never a truncation point"* under
    AD-29, a rule this module's own docstring states absolutely. The nested form also multiplies
    into `search.max_buckets` (65 536): an inner `size` of `N` times a handful of values exceeds it.
    The composite has neither problem and needs no bucket count guessed at all.

    Every rule the single-value resolver obeys is obeyed here, for the same reasons:

    - the window is the report range **widened** by the emission-lag allowance, never the range
      verbatim - `@timestamp` is emission time and never assigns a row to a period;
    - a dimension with **no id field** resolves to **no ids for every value** - an empty mapping,
      not an error, exactly as `_lookups`' empty list makes an unresolvable scope a measured zero;
    - **a value Elasticsearch knows nothing about is absent from the mapping**, and a caller must
      read an absent key as *no ids* rather than as *all ids* - the inversion that would turn one
      value's bucket into the whole table.

    **`workflow`'s second, name-based lookup deliberately does not run here.** `_lookups` adds it
    only when a caller has already disambiguated a *single* name, and a grouped read over many
    values has done that for none of them - so `None` is passed and only the id-based lookup
    remains. A grouped workflow read therefore covers the id-emitting population plus the residue,
    and a pre-id-emission row lands in the residue rather than in a name-matched bucket, which is
    the same choice `resolve_metric_store_ids` makes when given no name.
    """
    # De-duplicated on insertion order, like every other id set here: a caller passing one value
    # twice must get one entry rather than two that could differ, and `size` below is derived from
    # this count.
    requested = tuple(dict.fromkeys(values))

    # `_lookups` is asked about the *grouped* scope - value `None`, no disambiguated name - so it
    # yields exactly the dimension's own id field and nothing else. Asking it rather than reading
    # `_DIMENSION_ID_FIELDS` directly is what keeps `workflow`'s field in one place.
    lookups = _lookups(DimensionScope(dimension, None), None)
    if not lookups or not requested:
        # No field to group on, or nothing asked about. An `include` over no values and a `size` of
        # zero are not a query Elasticsearch accepts, and there is nothing for one to answer.
        return {}
    field, _ = lookups[0]

    window = period.widened(emission_lag)
    # Narrowed in the **query**, not by an aggregation `include`. A `terms` filter on the requested
    # values is index-backed and exact, and it keeps the aggregation below free of any bucket count
    # to guess at - which is what lets the composite page instead of truncate.
    query_filter = [
        {"range": {_TIMESTAMP_FIELD: {"gte": window.start.isoformat(), "lt": window.end.isoformat()}}},
        {"terms": {field: list(requested)}},
    ]

    # `dict.fromkeys` per value for the same reason the single-value path uses it: two pages can
    # legitimately carry the same id for one value and the set must still count it once.
    per_value: dict[str, dict[str, None]] = {}
    after: dict | None = None
    while True:
        composite: dict = {
            "size": page_size,
            # Two sources, so one paged stream carries every (value, id) pair. This is what makes
            # the grouped read obey this module's no-truncation rule: a `terms` sub-aggregation has
            # no `after_key`, so it would silently drop a value's ids past `N` - and `N` is a paging
            # threshold, never a truncation point.
            "sources": [
                {"value": {"terms": {"field": field}}},
                {"id": {"terms": {"field": _REQUEST_ID_FIELD}}},
            ],
        }
        if after is not None:
            composite["after"] = after
        response = await repository.execute_aggregation_query(
            {"size": 0, "query": {"bool": {"filter": query_filter}}, "aggs": {"pairs": {"composite": composite}}}
        )
        pairs_agg = response.get("aggregations", {}).get("pairs", {})
        buckets = pairs_agg.get("buckets", [])
        for bucket in buckets:
            key = bucket["key"]
            per_value.setdefault(key["value"], {})[key["id"]] = None
        # A short page means nothing is left; a full one may have more behind it. Same exhaustion
        # signal as `_collect_ids`, because a composite never reports a total.
        if len(buckets) < page_size:
            break
        after = pairs_agg.get("after_key")
        if after is None:
            break

    return {value: tuple(ids) for value, ids in per_value.items()}
