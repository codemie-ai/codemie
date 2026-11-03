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

"""The four traffic classes a spend chart splits money into, and how each is selected.

**A closed table, in one place, because it is a correctness guard and not a convenience.** The weekly
spend chart shows money split four ways, and a caller that passed its own predicates in would be writing
an attribution `WHERE` clause outside `spend_logs_repository` - which is prohibited for the reason this
whole port exists: two callers would each write a legitimate-looking predicate and return different
numbers for the same week. This is the same shape as `default_ledger._MONEY_METRICS`, which is the
existing precedent for *a closed class-to-selector table kept in one place because getting it wrong is
silent*.

**Three of the four are ledger-native, and the asymmetry is the thing to get right.** AD-29 named two
native classes - CLI by `attr_category = 'cli'`, workflow by `codemie_workflow_id IS NOT NULL` - and sent
the other two through the metric store's correlation ids. The datasource class never resolved that way:
no datasource document carries a correlation id, so its id set was empty in every window and the class a
permanent measured zero over real money. It is native now, on the column the ledger's own provider
populates - `call_type`, which names an embedding request as one - and the union mechanism is left for
the one class that still needs it:

| Class | Selected natively by | Needs the metric store? |
| --- | --- | --- |
| `CLI` | `attr_category = 'cli'` | no |
| `WORKFLOW` | `codemie_workflow_id IS NOT NULL` | no |
| `DATASOURCE` | `call_type IN (the embedding call types)` | no |
| `ASSISTANT` | correlation ids the metric store resolved | **yes** |

**The CLI predicate is `attr_category` and never `attr_channel`**, and the correction is load-bearing:
`attr_channel` is populated only on *fallback* rows, from a suffix the parser found in `end_user`, so the
uploader's explicit branch leaves it NULL by rule. Every post-rollout CodeMie row therefore carries
`attr_channel = NULL` - proxy and CLI rows included - so a CLI bucket keyed on it **empties as the rollout
proceeds**, and an empty bucket is indistinguishable from no CLI spend.

**The residue of a class split is the rows no class claims, not the rows one class could not resolve.**
Naming the assistant class's unresolved rows as the residue would name every CLI, workflow and embedding
row too - roughly the other three series - as money missing from the assistant series.
`unclassified_predicate` is the complement of the four selectors, and its native half is written NULL-safe
conjunct by conjunct, because `NOT (attr_category = 'cli' OR ...)` is NULL for an unattributed row and a
NULL never satisfies a `WHERE`: the very rows the residue exists to name would fall out of it.

**Each selector reads the origin first and the native columns only where there is no origin.**
`TrafficOrigin` names what a request was for - the vocabulary the metric store has in its metric names and
the ledger's own columns cannot express - and `ORIGINS_BY_CLASS` files each origin under the class it
belongs to. Every selector is `origin IN (the class's origins) OR (origin IS NULL AND <the native
predicate>)`: a row that says what it was for is classified by what it said, and a row that says nothing -
every row written before the sending release, and every thread nothing published an origin on - is
classified exactly as it was before. The two branches of one selector are exclusive on `codemie_origin IS
NULL`, so no selector claims a row twice; a row that names its origin may well land in a different class from
the one its native columns would have given it, and that is the point of asking.

**An origin word no class claims is a defect to see, not a row to re-guess.** `proxy` is claimed by no
class by design, and a value a later sender invents is claimed by none either; both land in the residue
*even when a native column would have claimed them*. The sender said what the request was for, and
overriding that with a column-derived guess would hide the one thing the residue exists to make visible.

**A union class over an empty id set selects nothing, and that is a measured zero rather than an error.**
The rows it could not bucket are named by the residue, so the chart says *an amount is missing*
instead of drawing a shorter line.
"""

from __future__ import annotations

from enum import Enum

from sqlalchemy import ColumnElement, and_, false, or_, true

from codemie.repository.spend_logs_model import LiteLLMSpendLog as Row
from codemie.service.spend.coverage import SpendDimension

__all__ = [
    "EMBEDDING_CALL_TYPES",
    "ORIGINS_BY_CLASS",
    "UNION_CLASSES",
    "TrafficClass",
    "TrafficOrigin",
    "cli_complement",
    "request_resolved",
    "request_unresolved",
    "traffic_class_predicate",
    "unclassified_predicate",
]

_CLI_CATEGORY = "cli"

#: LiteLLM's own `call_type` values for an embedding request. The sync spelling is included
#: defensively; every sampled row carries the async one. A failure row carries an empty string, and a
#: NULL is possible on the ledger's column, so neither is an embedding and neither is matched.
EMBEDDING_CALL_TYPES: tuple[str, ...] = ("embedding", "aembedding")


class TrafficClass(str, Enum):
    """The four splits, enumerated. A fifth would need a predicate, which is the point of the table."""

    CLI = "cli"
    WORKFLOW = "workflow"
    ASSISTANT = "assistant"
    DATASOURCE = "datasource"


class TrafficOrigin(str, Enum):
    """What a request was for - one value per row, the closed vocabulary `codemie_origin` will carry.

    Mirrors the metric names the metric store already splits money by, so the pre-floor and the
    ledger side speak one vocabulary. `PROXY` names a proxied call that is not CLI, so that no row of
    a known kind lands NULL by design; `WORKFLOW_GENERATOR` and `CONVERSATION_ANALYSIS` name the two
    money-bearing emitters that sit in no metric group today, stamped with their real origin from the
    day the column ships.
    """

    CONVERSATION = "conversation"
    WORKFLOW = "workflow"
    ASSISTANT_GENERATOR = "assistant_generator"
    PROMPT_GENERATOR = "prompt_generator"
    SKILL_GENERATOR = "skill_generator"
    WORKFLOW_GENERATOR = "workflow_generator"
    WORKFLOW_OUTPUT_CHANGE = "workflow_output_change"
    TOOLS = "tools"
    MARKETPLACE_VALIDATION = "marketplace_validation"
    CONVERSATION_ANALYSIS = "conversation_analysis"
    DATASOURCE_INDEXING = "datasource_indexing"
    SEARCH_EMBEDDING = "search_embedding"
    CLI = "cli"
    PROXY = "proxy"


#: Which class each origin belongs to. Every origin but `PROXY` is filed once; a proxied call that is
#: not CLI is claimed by no class and lands in the residue, which is what it is today too. The
#: generators, tools, marketplace validation and the background analysis are assistant traffic, as the
#: chart's own metric-name grouping has always counted them.
ORIGINS_BY_CLASS: dict[TrafficClass, frozenset[TrafficOrigin]] = {
    TrafficClass.CLI: frozenset({TrafficOrigin.CLI}),
    TrafficClass.WORKFLOW: frozenset({TrafficOrigin.WORKFLOW, TrafficOrigin.WORKFLOW_OUTPUT_CHANGE}),
    TrafficClass.DATASOURCE: frozenset({TrafficOrigin.DATASOURCE_INDEXING, TrafficOrigin.SEARCH_EMBEDDING}),
    TrafficClass.ASSISTANT: frozenset(
        {
            TrafficOrigin.CONVERSATION,
            TrafficOrigin.ASSISTANT_GENERATOR,
            TrafficOrigin.PROMPT_GENERATOR,
            TrafficOrigin.SKILL_GENERATOR,
            TrafficOrigin.WORKFLOW_GENERATOR,
            TrafficOrigin.TOOLS,
            TrafficOrigin.MARKETPLACE_VALIDATION,
            TrafficOrigin.CONVERSATION_ANALYSIS,
        }
    ),
}

#: Each class's origins as the values an `IN` takes, derived from the table above once rather than at
#: every call, so a selector's origin branch cannot come to disagree with the table it is filed under.
_ORIGIN_VALUES: dict[TrafficClass, tuple[str, ...]] = {
    traffic_class: tuple(sorted(origin.value for origin in origins))
    for traffic_class, origins in ORIGINS_BY_CLASS.items()
}

#: Every origin some class claims. A row whose origin is set and absent from this set belongs to no
#: class - `proxy` by design, an unrecognised word by defect - and the residue names it on that ground
#: alone, without consulting a native column the sender has already contradicted.
_CLASSED_ORIGINS: tuple[str, ...] = tuple(sorted({value for values in _ORIGIN_VALUES.values() for value in values}))

#: Which classes the ledger cannot bucket by itself, and the dimension whose id set resolves each. The
#: caller fetches the ids - only it knows the scope in the metric store's vocabulary - and the ledger
#: turns them into money, because money never comes from the metric store.
UNION_CLASSES: dict[TrafficClass, SpendDimension] = {
    TrafficClass.ASSISTANT: SpendDimension.ASSISTANT,
}


def request_resolved(resolved_ids: tuple[str, ...]) -> ColumnElement[bool]:
    """Rows whose correlation id the metric store resolved - the one spelling of that membership.

    An empty set matches **nothing**, spelled `false()` rather than as an empty `IN`: the predicate then
    names no correlation id at all, which is what lets the hourly projection - which carries no
    correlation ids - answer a read whose id set is empty (EPMCDME-15537).
    """
    if not resolved_ids:
        return false()
    return Row.codemie_request_id.in_(resolved_ids)


def request_unresolved(resolved_ids: tuple[str, ...]) -> ColumnElement[bool]:
    """Rows the metric store did **not** resolve - the NULL-safe complement of `request_resolved`.

    **The `IS NULL` disjunct is load-bearing, and dropping it is silent.** `NULL NOT IN (:ids)` is
    *unknown*, not true, so a bare comparison excludes exactly the rows guaranteed to carry a NULL
    correlation id - every row written before the header shipped - and their money then lands in no
    bucket and in no residue. An empty set leaves every row unresolved, spelled `true()` for the reason
    `request_resolved` spells its empty case `false()`.
    """
    if not resolved_ids:
        return true()
    return or_(Row.codemie_request_id.is_(None), Row.codemie_request_id.not_in(resolved_ids))


def _claimed_by(traffic_class: TrafficClass, natively: ColumnElement[bool]) -> ColumnElement[bool]:
    """One class's rows: the ones that named it as their origin, plus the ones that named none.

    The `IS NULL` guard is what keeps the two branches exclusive. Without it a row carrying one
    class's origin would still be selected by another class whose native column happens to be
    populated - the same money in two series, and both series individually plausible.
    """
    return or_(Row.codemie_origin.in_(_ORIGIN_VALUES[traffic_class]), and_(Row.codemie_origin.is_(None), natively))


def traffic_class_predicate(
    traffic_class: TrafficClass,
    resolved_ids: tuple[str, ...] = (),
) -> ColumnElement[bool]:
    """What selects one class's rows.

    `resolved_ids` is ignored by the three native classes and is the **whole** native selector for the
    assistant class. An empty set there selects nothing, so before the origin
    column was populated the class was a measured zero in every bucket, with the missing rows named in
    the residue rather than dropped unremarked; a row that names an assistant origin is claimed without
    the metric store resolving anything.
    """
    match traffic_class:
        case TrafficClass.CLI:
            return _claimed_by(traffic_class, Row.attr_category == _CLI_CATEGORY)
        case TrafficClass.WORKFLOW:
            return _claimed_by(traffic_class, Row.codemie_workflow_id.is_not(None))
        case TrafficClass.DATASOURCE:
            return _claimed_by(traffic_class, Row.call_type.in_(EMBEDDING_CALL_TYPES))
        case TrafficClass.ASSISTANT:
            return _claimed_by(traffic_class, request_resolved(resolved_ids))
    raise TypeError(f"no predicate is defined for traffic class {traffic_class!r}")


def cli_complement() -> ColumnElement[bool]:
    """Exactly the rows the CLI class does **not** claim, on both columns and NULL-safe on each.

    The summary screen's platform card is this, and it has to be the CLI selector's complement rather
    than a second opinion about what CLI means: `TOTAL = PLATFORM + CLI` holds only while every row is
    on exactly one side. A CLI session billed to a premium budget is the row that proves the two
    columns disagree legitimately - `codemie_origin = 'cli'` with `attr_category = 'premium_models'` -
    and a platform card keyed on the category alone would count it twice with the CLI card beside it.

    Each conjunct admits the NULL the other would drop: `origin <> 'cli'` is *unknown* on a row with no
    origin, and `attr_category IS DISTINCT FROM 'cli'` is the wrong question on a row that has one.
    """
    cli_origins = _ORIGIN_VALUES[TrafficClass.CLI]
    return and_(
        or_(Row.codemie_origin.is_(None), Row.codemie_origin.not_in(cli_origins)),
        or_(Row.codemie_origin.is_not(None), Row.attr_category.is_distinct_from(_CLI_CATEGORY)),
    )


def unclassified_predicate(resolved_ids: tuple[str, ...] = ()) -> ColumnElement[bool]:
    """The rows no class claims - the residue every class split names as its missing money.

    The complement of the four selectors, and so it splits the same way they do. A row that named an
    origin is judged on that word alone: `proxy` and any word this vocabulary does not hold are claimed
    by no class, and re-reading their native columns would file them under a class their sender said
    they were not. A row that named none falls to the native complement, spelled conjunct by conjunct
    rather than as `NOT (a OR b OR c OR d)`: on an unattributed row `attr_category` is NULL, so the
    disjunction is NULL, its negation is NULL, and the row - exactly the kind this residue exists to
    count - satisfies nothing. Each conjunct below admits the NULL it must.
    """
    return or_(
        and_(Row.codemie_origin.is_not(None), Row.codemie_origin.not_in(_CLASSED_ORIGINS)),
        and_(
            Row.codemie_origin.is_(None),
            Row.attr_category.is_distinct_from(_CLI_CATEGORY),
            Row.codemie_workflow_id.is_(None),
            or_(Row.call_type.is_(None), Row.call_type.not_in(EMBEDDING_CALL_TYPES)),
            request_unresolved(resolved_ids),
        ),
    )
