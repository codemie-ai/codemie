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

"""Query construction against `litellm_spend_logs`, and the one place a holder becomes a predicate.

**A hand-written attribution `WHERE` clause outside this module is prohibited.** That is not
tidiness. Two units could each centralise their own logic, each filter on a legitimate `attr_*`
column, and still return different totals for the same person - because the same human appears
under *both* grammars, as a bare principal on platform rows and as a project-scoped value on
project-keyed rows. One resolver is what makes "the same figure" mean something.

**Every read is bounded by a `start_time` range, and the range is half-open**:
`start_time >= period_start AND start_time < period_end`. `BETWEEN` is prohibited because it is
inclusive at both ends, which lands a boundary row in two adjacent period totals - verified on
PostgreSQL 17, where a row at exactly `2026-09-01T00:00:00Z` is counted by `BETWEEN` in both
August and September. `end_time` never assigns a row to a period; a call spanning midnight
belongs to the period it started in.

**The one exemption is a single-request lookup keyed on a correlation id, and inventing a time
bound for it is a defect rather than a safeguard** - see `sum_for_correlation_id`.

**Every windowed `SUM(spend)` and `COUNT(*)` is answered from the hourly projection where it can be,
exactly** (EPMCDME-15537); distinct counts and the single-request lookup stay on raw rows. Each read
below is described once, as a `ReadShape` built by this module's own predicates, and `spend_projection`
renders it either over raw rows - the statement this module has always issued - or over
`litellm_spend_hourly`, its whole hours plus the raw edge hours, with the metric store's ids corrected
from raw rows in the same statement. A predicate the projection cannot express -
a named execution's correlation id - renders the whole read raw. The projection is a
derived index of this table, maintained in the same transaction as every ledger write, never a second
authority. Whether a repository may use it is decided where the ledger is composed, from
`SPEND_LEDGER_PROJECTION_ENABLED`.

This module reads. It never writes, and it never decides whether a period may be answered: the
floors and readiness do that, above it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ColumnElement, Result, and_, false, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from codemie.service.spend.attribution import PROJECT_PREFIX
from codemie.service.spend.coverage import SpendDimension, TimeRange
from codemie.service.spend.spend_group import SpendGroup, spend_group_predicate
from codemie.service.spend.traffic_class import (
    UNION_CLASSES,
    TrafficClass,
    request_resolved,
    request_unresolved,
    traffic_class_predicate,
    unclassified_predicate,
)
from codemie.service.spend.ledger import (
    HOLDER_BATCH_SIZE,
    AccessScope,
    BudgetHolder,
    ComposedScope,
    DimensionScope,
    PersonalHolder,
    PersonTotalHolder,
    ProjectGroupHolder,
    ProjectHolder,
    ProjectMemberHolder,
    ReportFilter,
)
from codemie.repository.spend_logs_model import LiteLLMSpendLog as Row
from codemie.repository.spend_projection import (
    Aggregate,
    Measure,
    ReadShape,
    ShapeOf,
    identities_statement,
    raw_statement,
    read_statement,
    within,
)

#: The provenance value that means "the fallback parser attributed this row". A consumer never
#: branches on `attr_source` for meaning - the `attr_*` columns mean the same thing either way -
#: but the alias set only applies to rows the parser read.
_PARSED = "parsed"

_EXPLICIT = "explicit"
_PRINCIPAL = "principal"
_PROJECT_SCOPED = "project_scoped"

#: What the uploader stores in `end_user` when LiteLLM charged no customer: it writes an empty or
#: NULL `end_user` as the literal `'N/A'`, a declared deviation from the contract's "verbatim".
#: Such a row is outside every customer's figure, exactly as a NULL `end_user` would be.
_NO_CUSTOMER = ("N/A", "")

#: The dimensions this module can resolve as a two-branch union. `PROJECT` is excluded because it
#: is floor-scoped and has its own residue method carrying the origin conjunct;
#: `IDENTITY_BRIDGE` is excluded because its rows cannot be identified at all, which is the whole
#: content of that dimension.
_UNION_DIMENSIONS = frozenset(
    {
        SpendDimension.WORKFLOW,
        SpendDimension.EXECUTION,
        SpendDimension.ASSISTANT,
        SpendDimension.CONVERSATION,
        SpendDimension.DATASOURCE,
        SpendDimension.MODEL,
    }
)

#: Dimensions the ledger can bucket by itself, and the column that does it. **Exactly one column
#: was promoted**, so this map has one key and the execution dimension borrows it: on the workflow
#: path `codemie_request_id` *is* the execution id, and `codemie_workflow_id IS NOT NULL` is what
#: distinguishes an execution id from a chat request id.
#:
#: **Everything absent from this map has no ledger column at all** - assistant, conversation and
#: datasource resolve *wholly* through the metric store. The model dimension is the opposite case:
#: its column resolves it *wholly*, and the metric store contributes nothing, which the resolver
#: already handles by returning an empty id set for a dimension it has no lookup for.
#:
#: Forcing the workflow column's `IS NULL`
#: onto one of them is the silent defect this map exists to prevent: it drops every in-scope row
#: that happens to sit inside a workflow - money, in scope, in no bucket and in no residue - so
#: the report simply shrinks and no label fires to say why.
_DIMENSION_COLUMN = {
    SpendDimension.WORKFLOW: Row.codemie_workflow_id,
    SpendDimension.EXECUTION: Row.codemie_workflow_id,
    #: **The one dimension whose column the provider itself populates**, so unlike the workflow
    #: column it has no pre-rollout NULL era for the metric store to cover. The union machinery is
    #: still correct rather than bypassed: the column is nullable, and a row that does arrive NULL
    #: falls to the residue and is named in the label - which is what a special case here would
    #: have quietly stopped happening.
    SpendDimension.MODEL: Row.model_group,
}

#: Which column a scope *value* is matched against, where it differs from the discriminator above.
_DIMENSION_VALUE_COLUMN = {
    SpendDimension.WORKFLOW: Row.codemie_workflow_id,
    SpendDimension.EXECUTION: Row.codemie_request_id,
    SpendDimension.MODEL: Row.model_group,
}


def _require_union_dimension(scope: DimensionScope) -> None:
    """Reject the two dimensions that are not resolved this way, loudly.

    Silently accepting either would answer a question with the wrong predicate: the project
    dimension needs its floor-scoped sub-range and its origin conjunct, and the identity bridge
    has no row set to query.
    """
    if scope.dimension not in _UNION_DIMENSIONS:
        raise ValueError(
            f"{scope.dimension.value} is not resolved as a dimension union - "
            "use project_dimension_residue for the project dimension, and report the identity "
            "bridge as a portion whose amount is not computable"
        )


def _ledger_branch(scope: DimensionScope) -> ColumnElement[bool] | None:
    """Rows the ledger buckets itself, or `None` when it can bucket none of them.

    `None` is a real answer and not an error: three of the five dimensions have no column, so the
    ledger branch is empty *by construction* and the whole scope is the metric store's to resolve.
    """
    column = _DIMENSION_COLUMN.get(scope.dimension)
    if column is None:
        return None
    populated = column.is_not(None)
    if scope.value is None:
        return populated
    return and_(populated, _DIMENSION_VALUE_COLUMN[scope.dimension] == scope.value)


def _dimension_is_null(scope: DimensionScope) -> ColumnElement[bool] | None:
    """The precedence conjunct restricting the metric-store branch, where a column exists."""
    column = _DIMENSION_COLUMN.get(scope.dimension)
    return None if column is None else column.is_(None)


def _metric_store_branch(scope: DimensionScope, resolved_ids: tuple[str, ...]) -> ColumnElement[bool] | None:
    """Rows the metric store bucketed, restricted so the populated column always wins.

    The restriction is what keeps a row from being counted twice *inside one query*: a row can be
    populated in the dimension column and also sit in the id set the metric store resolved for a
    different bucket. Precedence settles it here; there is no second dedupe step to forget,
    because one scan of one table cannot double-count a row two disjoint branches select.
    """
    if not resolved_ids:
        return None
    in_id_set = request_resolved(resolved_ids)
    is_null = _dimension_is_null(scope)
    return in_id_set if is_null is None else and_(is_null, in_id_set)


def _union_predicate(scope: DimensionScope, resolved_ids: tuple[str, ...]) -> ColumnElement[bool] | None:
    """The whole of one scope's row set - both branches OR'd, or `None` when it has neither.

    **Shared by the three reads that select a dimension scope so they cannot come to mean different
    things by "this assistant".** A total, an execution count and a grouped read all resolve the
    same scope, and a union assembled three times is free to drift in one of them - the same
    disagreement between two surfaces that `_ledger_branch` and `_metric_store_branch` exist to
    prevent, arriving one layer up. `_traffic_class_aggregates` was extracted for exactly this reason.

    **One OR'd predicate rather than two sums added in Python**, because a row shared between the
    branches would be counted twice by the second form. The branches are disjoint under the
    precedence conjunct, so one scan is exactly "summed once, deduped on
    `(request_id, start_time)`" with no dedupe step a later reader could omit.

    `None` is a real answer: a dimension with no ledger column, asked about a scope the metric store
    resolved no ids for, selects no rows at all - a **measured zero**, whose missing money the
    residue names.
    """
    branches = [b for b in (_ledger_branch(scope), _metric_store_branch(scope, resolved_ids)) if b is not None]
    if not branches:
        return None
    return branches[0] if len(branches) == 1 else or_(*branches)


def _fallback_principal_match(aliases: frozenset[str] | None) -> ColumnElement[bool] | None:
    """The fallback half of an identity predicate, or None when the bridge is unavailable.

    A person is not one identifier on rows the parser attributed: some code paths emit
    `users.username` and some `users.email` for the same human, and the two columns are unique
    only *independently*. So a fallback reader matches an alias set rather than one column.

    `aliases is None` means the bridge could not be resolved - a deleted user, an unmatched
    principal - and is **not** the same as an empty set. There is no half to add, and the caller
    must surface that as an `identity_bridge` portion whose amount is not computable, rather than
    quietly returning the explicit half as if it were complete.
    """
    if not aliases:
        return None
    return and_(
        Row.attr_source == _PARSED,
        Row.attr_form == _PRINCIPAL,
        Row.attr_principal.in_(tuple(aliases)),
    )


def predicate_for_holder(holder: BudgetHolder) -> ColumnElement[bool]:
    """Map a budget holder to the exact predicate that defines its spend.

    **Two pots exist today and these predicates keep them apart.** LiteLLM already holds a
    person's spend in two different customers: a personal one, and a project one. A call made
    with a project in context lands in the *project* pot only - it does not also consume the
    caller's personal budget. A project pot also excludes rows that charged no customer - the
    own-credentials bypass - because no project customer's counter holds them.
    """
    match holder:
        case PersonalHolder():
            return _personal_predicate(holder)
        case ProjectHolder():
            return and_(
                _project_scoped_match(holder.project),
                Row.attr_category == holder.category.value,
                _charged_a_customer(),
            )
        case ProjectMemberHolder():
            # Keyed on project AND user id. A pre-rollout principal row carries neither, which is
            # why a figure for this holder reaching below the identity floor is a mixed quantity
            # and must be labelled rather than presented as exact.
            return and_(
                _project_scoped_match(holder.project),
                Row.attr_category == holder.category.value,
                Row.attr_user_id == holder.user_id,
                _charged_a_customer(),
            )
        case ProjectGroupHolder():
            return and_(
                Row.attr_form == _PROJECT_SCOPED,
                Row.attr_project.in_(tuple(holder.projects)),
                Row.attr_category == holder.category.value,
                _charged_a_customer(),
            )
        case PersonTotalHolder():
            return _person_total_predicate(holder)
    raise TypeError(f"no predicate is defined for holder {type(holder).__name__}")


def _charged_a_customer() -> ColumnElement[bool]:
    return Row.end_user.notin_(_NO_CUSTOMER)


def _project_scoped_match(project: str) -> ColumnElement[bool]:
    """One project's rows, in the one spelling every project-keyed read shares.

    **The form conjunct travels with the project comparison, and separating them is what lets two
    readers mean two things by "this project".** It is redundant against today's ingest contract -
    only a project-scoped row carries `attr_project` at all, and a principal-form row carries NULL
    by construction - but the three holders that filter on a project all carry it, so a fourth
    reader spelling the term with one conjunct fewer would have to be *proved* equivalent rather
    than read as equivalent. One expression is what removes the proof obligation; the compiled SQL
    of both callers is unchanged, because `and_` flattens.
    """
    return and_(Row.attr_form == _PROJECT_SCOPED, Row.attr_project == project)


def _project_scoped_match_any(projects: Sequence[str]) -> ColumnElement[bool]:
    """The same project comparison over a **set** of projects, in one expression.

    The set form exists so an access boundary spanning several projects is not spelled as a chain of
    `_project_scoped_match` disjuncts: the compiled SQL would carry the form conjunct once per
    project, and a reader checking whether the two spellings agree would have to prove it. The form
    conjunct travels here for the reason it travels there - see `_project_scoped_match`.

    An empty set matches **nothing** rather than everything. Every caller of this function is a
    narrowing, so the widest possible reading of an empty argument is also the most dangerous one.
    """
    if not projects:
        return false()
    return and_(Row.attr_form == _PROJECT_SCOPED, Row.attr_project.in_(tuple(projects)))


def _personal_pot(owner_id: str) -> ColumnElement[bool]:
    """A personal project's rows: its owner's principal-form rows, keyed on their `users.id` alone.

    Its calls reach the ledger with no project at all, so this - not its name - is what a report term
    naming the project matches. It is the very pot `access_predicate` admits as the caller's own, and
    for the same reason it takes no alias half: a parsed principal-form row means *charged to the
    person's own customer*, which before the identity floor also covered their calls in shared
    projects without a budget - so an alias match would count those as this project's.

    The rows Elasticsearch files elsewhere stay in: where an identity provider's username is not the
    email, a CLI call sent with no project lands under the username there and in this pot here.
    """
    return and_(_person_total_predicate(PersonTotalHolder(owner_id, None)), Row.attr_form == _PRINCIPAL)


def _personal_predicate(holder: PersonalHolder) -> ColumnElement[bool]:
    """A personal or global budget: one user, one category, **what the person's own customer was charged**.

    LiteLLM charges whichever customer a request carries in `end_user`, and attribution does not
    decide that. A project with no live budget for the category routes the call to the person's
    personal budget. Such a call now arrives `principal`, because the proxy names no project it did
    not charge. Rows stored before that (since 2026-11-01) are still `project_scoped` with the
    person's **own** customer - EPMCDME-15204 found every such row missing from the personal figure
    while LiteLLM's counter held it - and they remain in this figure *and* in the project's attributed
    one. That is why the explicit half keys on the customer, not on the form: any `end_user` that is
    neither a project-member customer nor the `_NO_CUSTOMER` placeholder.

    The bare `attr_user_id = :user_id AND attr_category = :category` is still the sharpest
    available mistake here: an explicit row carrying a project lands `attr_form='project_scoped'`
    *with `attr_user_id` populated*, so it would absorb the person's project-**budget** spend into
    their personal budget. A row that charged no customer - the own-credentials bypass, with a
    project in context or without - stays out as well, because no customer's counter holds it; a
    NULL `end_user` fails both comparisons anyway. The parsed half keeps the principal form: a parsed
    row's form was read from `end_user` itself, so there the principal form already *is* the
    person's own customer.
    """
    explicit = and_(
        Row.attr_source == _EXPLICIT,
        Row.attr_user_id == holder.user_id,
        Row.attr_category == holder.category.value,
        ~Row.end_user.startswith(PROJECT_PREFIX, autoescape=True),
        Row.end_user.notin_(_NO_CUSTOMER),
    )
    fallback = _fallback_principal_match(holder.aliases)
    if fallback is None:
        return explicit
    return or_(explicit, and_(fallback, Row.attr_category == holder.category.value))


def _person_total_predicate(holder: PersonTotalHolder) -> ColumnElement[bool]:
    """A person's total across everything - both pots, deliberately.

    This is the **one** predicate that uses the bare `attr_user_id` match, and here it is
    correct: the figure is meant to span the personal and project pots. A row charged to the
    person but attributed to a project is in both a personal and a project figure, yet it is one
    row here, so nothing double-counts.
    """
    explicit = Row.attr_user_id == holder.user_id
    fallback = _fallback_principal_match(holder.aliases)
    if fallback is None:
        return explicit
    return or_(explicit, fallback)


#: The holders `may_hold_rows` rules out by identity; every other holder is always priced.
_PERSON_KEYED = (PersonalHolder, PersonTotalHolder)


def may_hold_rows(holder: BudgetHolder, user_ids: frozenset[str], principals: frozenset[str]) -> bool:
    """Whether `holder`'s predicate could match a row carrying one of these identities.

    Only the two person-keyed holders are ruled out: both predicates require the holder's user id in
    `attr_user_id` or, on the fallback half, one of its aliases in `attr_principal` - see
    `_personal_predicate` and `_person_total_predicate`. This restates which columns they key on, so it
    must follow them if either ever keys on another; the batch-versus-single agreement test over every
    match path is what catches a drift. Every other holder may match.
    """
    if isinstance(holder, _PERSON_KEYED):
        return holder.user_id in user_ids or not principals.isdisjoint(holder.aliases or ())
    return True


def access_predicate(scope: AccessScope) -> ColumnElement[bool] | None:
    """The caller's permission boundary as a SQL conjunct - the twin of `AccessScope`'s ES half.

    **`None` means "narrow nothing", and it is the only unconstrained answer this function gives.**
    It is returned for an unrestricted scope alone, mirroring `SecureQueryBuilder`'s admin/auditor
    path, which appends no filter at all. Every other input returns a real narrowing, and a
    restricted scope with no projects of either kind returns a predicate that matches **nothing** -
    the query builder's `minimum_should_match=0`. Conflating that with `None` is the one mistake here
    that opens the whole table to a caller entitled to none of it.

    **The user term is `_person_total_predicate` with no alias set.** It composes from the predicate
    that already owns the bare `attr_user_id` match rather than writing a second one - this module's
    central rule. Beside it the clause admits the caller's projects **or the principal form**: the
    personal pot, whose rows carry no project, while the same calls reach Elasticsearch under the
    caller's personal project and pass the filter this mirrors (see `AccessScope`). The alias set is
    `None` because an alias names a person only probably - one person's username can be another's
    email - and a boundary admitting by it would show the first the second's legacy parsed rows. Those
    rows carry a principal and no user id, and stay outside. That raises **no** `identity_bridge`
    portion, and must not: an access boundary is *not your money*, never *missing money*.

    **Rows the boundary excludes leave the figure and the residue alike, and that is correct.** The
    Elasticsearch source it mirrors did exactly the same - a document failing the injected filter
    contributed to no bucket and to no reported hole - so a residue narrowed by this predicate
    reports only the caller's *own* unbucketable money. Leaving the residue unnarrowed instead would
    publish the magnitude of spend the caller is forbidden to see.
    """
    if scope.unrestricted:
        return None

    if not scope.plain_projects and not scope.admin_projects:
        # A caller entitled to no project is entitled to nothing, their own pot included - the query
        # builder's `minimum_should_match=0`. `false()` says what it means at the call site.
        return false()

    own = and_(
        _person_total_predicate(PersonTotalHolder(scope.user_id, None)),
        or_(_project_scoped_match_any(scope.plain_projects), Row.attr_form == _PRINCIPAL),
    )
    if not scope.admin_projects:
        return own
    return or_(own, _project_scoped_match_any(scope.admin_projects))


def narrowed_by_access(predicate: ColumnElement[bool], scope: AccessScope | None) -> ColumnElement[bool]:
    """AND a read's own predicate with the caller's boundary - the one place that conjunction happens.

    A `None` scope means the read has no caller (a scheduled job, a budget enforcement path) and is
    therefore unnarrowed. That is a different absence from `AccessScope.everything()`, which is a
    *caller* who is entitled to everything, and both correctly narrow nothing - but a read that
    forgot to thread its scope should be findable, so the two are spelled differently at every call
    site rather than merged into one default.
    """
    if scope is None:
        return predicate
    boundary = access_predicate(scope)
    return predicate if boundary is None else and_(predicate, boundary)


def report_filter_predicate(report_filter: ReportFilter | None) -> ColumnElement[bool] | None:
    """What a dashboard **asked about** as a SQL conjunct, or None when it asked about everything.

    **The twin of `access_predicate` in shape and its opposite in meaning, which is why the two are
    written separately and ANDed rather than merged.** A boundary says what the caller *may* see and
    an empty one matches **nothing**; a filter says what the caller *asked for* and an empty one is
    the whole window. One function returning "no narrowing" for both empty states would have to pick
    a meaning, and either choice is wrong on one of the two.

    **The user term is `_person_total_predicate` with the term's own aliases**, not the bare
    `attr_user_id` match `access_predicate` settles for. The alias set is inert *there* - its
    fallback half is pinned to `attr_form = 'principal'`, whose rows the boundary's project conjunct
    excludes by construction - and load-bearing *here*, because this predicate carries no project
    conjunct beside it: a filter on a person that read the canonical id alone would drop every
    fallback row the parser keyed on `users.username` or `users.email` for that same human. A term
    whose alias set is `None` therefore renders its explicit half alone - nobody found out which
    fallback rows are that person's, so there is no conjunct to write - and the ledger labels the
    hole that leaves rather than this predicate widening to guess at it.

    Several users are ORed, because a dashboard naming three people asks for all three; the two kinds
    of term are ANDed, because one naming a person *and* a project asks for their spend in it.

    **A personal project is matched as its owner's principal pot** (`_personal_pot`): its calls carry
    no project in the ledger, so its name alone matches none of them.
    """
    if report_filter is None or report_filter.is_empty:
        return None

    terms: list[ColumnElement[bool]] = []
    if report_filter.users:
        terms.append(
            or_(
                *(
                    _person_total_predicate(PersonTotalHolder(term.user_id, term.aliases))
                    for term in report_filter.users
                )
            )
        )
    if report_filter.projects:
        personal_pots = map(_personal_pot, report_filter.personal_project_owners or ())
        terms.append(or_(_project_scoped_match_any(report_filter.projects), *personal_pots))
    return and_(*terms)


def narrowed_by_filter(predicate: ColumnElement[bool], report_filter: ReportFilter | None) -> ColumnElement[bool]:
    """AND a read's own predicate with what the dashboard asked about - the one place that happens.

    Shaped exactly like `narrowed_by_access` and deliberately kept beside it, so a read carrying one
    narrowing and not the other is visible at the call site rather than provable only from the SQL.
    A `None` filter - and an empty one - is *the whole window*, which is the opposite of what a
    `None` scope means there.
    """
    if report_filter is None:
        return predicate
    asked = report_filter_predicate(report_filter)
    return predicate if asked is None else and_(predicate, asked)


def composed_predicate(
    scope: ComposedScope,
    *,
    dimension_ids: tuple[str, ...] = (),
    traffic_class_ids: tuple[str, ...] = (),
) -> ColumnElement[bool] | None:
    """A conjunction of report filters, **composed from the predicates that already exist**.

    Nothing here is a new column comparison, and that is the whole design: each term delegates to
    the one place its own meaning is defined, so a composed figure cannot disagree with the
    single-term read for the same term. A term written out again here would be exactly the second
    legitimate-looking predicate this module exists to prevent.

    **The user term is `_person_total_predicate`, not `_personal_predicate`, and the choice is
    load-bearing.** *"What did this person spend"* spans **both pots**: a call made with a project
    in context lands in the project pot, and when a project budget was charged for it a personal
    budget cannot see it. `_personal_predicate` excludes exactly that, correctly, because a
    *personal budget* is what the person's own customer was charged - so borrowing it here would
    silently under-report every project-budget call the person made, which is item 15's
    `PersonalHolder(user, CLI)` trap. The person-total predicate is the one that spans them, one
    row at a time, so spanning them double-counts nothing. Synthesised as a holder rather than
    re-expressed, so the composed term *is* that predicate rather than a copy of it.

    **An absent term is unconstrained; a `None` return means the scope matches nothing.** The two are
    opposite and the distinction is the read's to honour: `None` arises only where the dimension
    term selects no rows at all - a dimension with no ledger column, asked about a scope the metric
    store resolved no ids for - and the answer is then a **measured zero**, the identical answer
    `sum_for_dimension_scope` gives for the same scope without querying. Returning an unconstrained
    predicate there would hand the term's caller every row in the range.

    **The traffic-class term comes from `traffic_class_predicate` and is never re-derived**: the
    closed table is a correctness guard, and a second definition of "CLI" is how two surfaces come to
    disagree. `traffic_class_predicate` already resolves an empty union-class id set to a
    matches-nothing predicate, so a class term needs no `None` case of its own.
    """
    terms: list[ColumnElement[bool]] = []

    if scope.user is not None:
        terms.append(_person_total_predicate(PersonTotalHolder(scope.user.user_id, scope.user.aliases)))
    if scope.project is not None:
        terms.append(or_(_project_scoped_match(scope.project), *map(_personal_pot, scope.project_owners)))
    if scope.dimension is not None:
        _require_union_dimension(scope.dimension)
        in_dimension = _union_predicate(scope.dimension, dimension_ids)
        if in_dimension is None:
            return None
        terms.append(in_dimension)
    if scope.traffic_class is not None:
        terms.append(traffic_class_predicate(scope.traffic_class, traffic_class_ids))

    # `ComposedScope` refuses an all-absent scope, so there is always at least one term and no
    # `and_()` over nothing - which would compile to an unbounded predicate - can be reached.
    return terms[0] if len(terms) == 1 else and_(*terms)


def _require_class_groupable_scope(scope: ComposedScope) -> None:
    """Reject a scope the class split cannot label, loudly, and there are two such scopes.

    A **class term** is the grouping key itself: pinning one and then grouping by it applies the
    same predicate twice and reports three keys whose measured zeros mean *not asked* rather than
    *nothing spent*.

    A **dimension term naming a union class's dimension** collides on the residue. Every union class
    is a key of this read, and each carries a portion on its own dimension; a scope term on that
    same dimension carries another, over a different id set, into the one slot `partial` has for it.
    `ComposedScope` refuses the same collision between a class *term* and a dimension term for the
    same reason - here it is the key set rather than a term that occupies the dimension.
    """
    if scope.traffic_class is not None:
        raise ValueError(
            f"a scope grouped by traffic class must not pin one - drop the {scope.traffic_class.value} "
            "term and read that class from the result"
        )
    if scope.dimension is not None and scope.dimension.dimension in set(UNION_CLASSES.values()):
        raise ValueError(
            f"the {scope.dimension.dimension.value} dimension is how a union class is resolved, so a "
            "scope naming it cannot also be split by class - its residue and the class's would share "
            "one partial slot"
        )


def _traffic_class_aggregates(resolved_ids: dict[TrafficClass, tuple[str, ...]]) -> tuple[Aggregate, ...]:
    """One `SUM(spend) FILTER (WHERE <class predicate>)` column per traffic class, in enum order.

    **Shared by the three class-split reads so they cannot come to mean different things by "CLI".**
    A window total and the chart drawn beside it are read by two different methods, and a filter
    list maintained twice is free to drift in one of them - which is the same disagreement between
    two surfaces that `traffic_class_predicate` exists to prevent, arriving one layer up.

    A class absent from `resolved_ids` is selected by its native predicate if it has one, and
    matches nothing if it does not - never everything.
    """
    return tuple(
        Aggregate(selector=traffic_class_predicate(cls, resolved_ids.get(cls, ())), label=f"class_{index}")
        for index, cls in enumerate(TrafficClass)
    )


def _spend_group_aggregates() -> tuple[Aggregate, ...]:
    """One `SUM(spend)` column per summary group, in enum order - filtered, except for the total.

    The total's column is a **bare** aggregate rather than one filtered on an always-true predicate:
    "every row" is the absence of a selector, and spelling it as a selector is what would let a later
    edit narrow it without looking like a narrowing.
    """
    return tuple(
        Aggregate(selector=spend_group_predicate(group), label=f"group_{index}")
        for index, group in enumerate(SpendGroup)
    )


def by_spend_group(figures: Sequence[object]) -> dict[SpendGroup, Decimal]:
    """The `_spend_group_aggregates` columns of one row, keyed on the group each selects."""
    return {group: Decimal(figures[index]) for index, group in enumerate(SpendGroup)}


#: Money and the ledger rows behind it, in that order - the residue and defect-metric reads' two columns.
_SPEND_AND_ROWS = (Aggregate(), Aggregate(Measure.ROWS))

#: A bare money total.
_SPEND = (Aggregate(),)


def _narrowing(access: AccessScope | None, report_filter: ReportFilter | None) -> ColumnElement[bool]:
    """The caller's boundary and the dashboard's filter as one conjunct - `true()` when neither narrows."""
    return narrowed_by_access(narrowed_by_filter(true(), report_filter), access)


def _or_nothing(predicate: ColumnElement[bool] | None) -> ColumnElement[bool]:
    """A union or composed predicate that selects no rows, spelled `false()` - never "every row".

    Only the read with the metric store's ids emptied can meet it: a scope resolved through ids alone
    selects nothing once they are gone, and its money then arrives through the resolved rows instead.
    """
    return false() if predicate is None else predicate


def _id_free(shape: ReadShape) -> ShapeOf:
    """The shape of a read that applies no metric-store ids, and is therefore the same either way."""
    return lambda _resolved: shape


def _union_of(*id_sets: Sequence[str]) -> tuple[str, ...]:
    """Every id a read applies, once - the set whose rows the projected read takes from raw rows."""
    return tuple(dict.fromkeys(one_id for ids in id_sets for one_id in ids))


def _dimension_values_shape(
    dimension: SpendDimension,
    chunk: Sequence[tuple[str, tuple[str, ...], ColumnElement[bool]]],
    access: AccessScope | None,
    report_filter: ReportFilter | None,
) -> ShapeOf:
    """One chunk of a dimension-grouped read: a `FILTER` column per value, over the union of their rows.

    `chunk` holds each value with its id set and its union predicate over those ids, already built.
    """

    def shape(resolved: bool) -> ReadShape:
        predicates = [
            in_scope if resolved else _or_nothing(_union_predicate(DimensionScope(dimension, value), ()))
            for value, _ids, in_scope in chunk
        ]
        aggregates = tuple(Aggregate(selector=p, label=f"value_{index}") for index, p in enumerate(predicates))
        return ReadShape(narrowed_by_access(narrowed_by_filter(or_(*predicates), report_filter), access), aggregates)

    return shape


class SpendLogsRepository:
    """Reads `litellm_spend_logs`. Sessions are injected; nothing here opens or commits one.

    `projection` decides whether a read may be answered from the hourly projection. It defaults to off,
    so a repository built without it - the module's singleton included - issues exactly the raw statements
    the suites pin; the ledger's composition root builds the one it reads with from
    `SPEND_LEDGER_PROJECTION_ENABLED`.
    """

    def __init__(self, projection: bool = False) -> None:
        self._projection = projection

    async def sum_for_holder(
        self,
        session: AsyncSession,
        holder: BudgetHolder,
        period: TimeRange,
        access: AccessScope | None = None,
    ) -> Decimal:
        """`SUM(spend)` over a holder's attributed rows for a period.

        **Returns `Decimal('0')` when no row matches, and that is correct rather than a
        fabricated zero.** The distinction is which question is being answered: this method is
        only ever called for a period the ledger has already admitted, so "no rows" genuinely
        means the holder spent nothing there. Whether a period may be answered *at all* is
        decided above this layer, by the coverage floor and readiness - and that answer is an
        absent figure, not a zero.

        **`access` is ANDed with the holder's own predicate and is not part of it**, which is why it
        arrives here rather than inside `predicate_for_holder`. A holder predicate *defines* whose
        spend a figure is; the boundary decides who may be *shown* it, and the two are asked by
        different questions about the same rows: a budget read passes `None` and gets the holder's
        whole consumption, because narrowing that would let spend escape its own limit as soon as the
        reader could not see the project it landed in. Folding the boundary into the predicate would
        make that distinction unexpressible.
        """
        shape = ReadShape(narrowed_by_access(predicate_for_holder(holder), access), _SPEND)
        return await self._total(session, period, _id_free(shape))

    async def sum_for_holders(
        self,
        session: AsyncSession,
        holders: Sequence[BudgetHolder],
        period: TimeRange,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> dict[BudgetHolder, Decimal]:
        """`SUM(spend)` per holder for a period, in one scan per chunk of the holders that could match.

        **Why one query of N filtered aggregates, and not a `GROUP BY`.** A `GROUP BY` over the
        attribution columns would also be one query, but it would have to *re-express* each
        holder's predicate as a grouping key - and re-expressing it is exactly what
        `predicate_for_holder` exists to prevent. The grouped form would then be free to disagree
        with the single-holder form for the same holder: `_personal_predicate`'s project-member
        `end_user` exclusion, for instance, has no grouping-key equivalent, so the obvious grouping
        on `(attr_user_id, attr_category)` would quietly absorb every project-budget row into the
        personal figure. `SUM(spend) FILTER (WHERE <the same
        predicate>)` cannot disagree, because it *is* the same expression evaluated in the same
        scan.

        Holders are de-duplicated on value, so a caller passing one holder twice gets one entry
        rather than two that could differ.

        **`access` narrows every holder's figure through one conjunct in the `WHERE`, exactly as
        `sum_for_dimension_values` narrows every value's.** An aggregate's `FILTER` sees only rows the
        `WHERE` admitted, so a boundary there narrows all hundred columns of a chunk at once - whereas
        ANDing it into each holder's own predicate would emit the clause twice per holder (once in its
        `FILTER`, once in the disjunction built from those same predicates) to no effect at all.

        **`None` is the budget answer and it is the default**, for the reason `sum_for_holder` gives:
        this read serves both a caller-facing table and a budget's own consumption, and only the first
        may be narrowed.

        **`report_filter` is a second conjunct in the same `WHERE`, beside the boundary**, as in
        `sum_for_spend_groups` and for the same reason: every holder's `FILTER` then sees only the rows
        the dashboard asked about. Its `None` default is the budget answer too - a budget's consumption
        is not whatever share of it one dashboard's filter selected.

        **Past one chunk, a person holder whose identity no row of the period carries is answered zero
        without being priced.** A budget screen asks about every person with a budget and most spent
        nothing in the window, while each chunk's projected statement costs far more to build than one
        read of the period's identities. That zero is decided by `may_hold_rows`, which restates which
        columns the person predicates key on - the one place a batched figure does not come from the very
        expression the single-holder read evaluates, and why a test holds the two equal over every match
        path. A narrowing cannot undo it: the boundary and the filter only ever remove rows.
        """
        unique = list(dict.fromkeys(holders))
        totals: dict[BudgetHolder, Decimal] = {}
        if len(unique) > HOLDER_BATCH_SIZE and any(isinstance(holder, _PERSON_KEYED) for holder in unique):
            user_ids, principals = await self._identities_in(session, period)
            totals = {holder: Decimal(0) for holder in unique if not may_hold_rows(holder, user_ids, principals)}
            unique = [holder for holder in unique if holder not in totals]

        for start in range(0, len(unique), HOLDER_BATCH_SIZE):
            chunk = unique[start : start + HOLDER_BATCH_SIZE]
            predicates = [predicate_for_holder(holder) for holder in chunk]
            aggregates = tuple(
                Aggregate(selector=predicate, label=f"holder_{index}") for index, predicate in enumerate(predicates)
            )
            # The disjunction narrows the scan to rows some holder in the chunk could claim. It
            # cannot move a single figure: every FILTER predicate implies it, so no row a filter
            # would have counted is excluded by it. The boundary and the dashboard's filter beside it
            # *can* move figures, and that is their purpose.
            where = narrowed_by_access(narrowed_by_filter(or_(*predicates), report_filter), access)
            row = (await self._read(session, period, _id_free(ReadShape(where, aggregates)))).one()
            for holder, total in zip(chunk, row, strict=True):
                totals[holder] = Decimal(total)

        return totals

    async def sum_by_time_bucket(
        self,
        session: AsyncSession,
        period: TimeRange,
        width: str,
        resolved_ids: dict[TrafficClass, tuple[str, ...]] | None = None,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> dict[tuple[datetime, TrafficClass], Decimal]:
        """`SUM(spend)` per (time bucket, traffic class) in **one** scan.

        **Why this exists rather than a loop over `aggregate`.** A twelve-week chart split four ways is
        forty-eight questions; asked one at a time on a warm reporting surface that is forty-eight round
        trips for one picture, and the surface then acquires a cache, and the cache becomes a second
        source of spend truth. One `GROUP BY` over the time bucket answers all of them.

        **This is a `GROUP BY` in the read path, and the one pre-aggregation the architecture allows sits
        beneath it.** A rollup written ahead of the query is a second copy of the money, so the only one
        is the hourly projection (EPMCDME-15537, owner-approved): derived from this table in the same
        transaction as every write, rebuildable from it, exact, and never an authority of its own - see
        `spend_projection`. Grouping inside one read is the same access pattern the dimension-grouped
        report already uses, with a time bucket added.

        **The class predicates come from the closed table, never from the caller.** A caller passing its
        own would be writing an attribution `WHERE` outside this module, which is exactly how two
        surfaces come to disagree about one week.

        `resolved_ids` carries the metric store's answer **per class**, fetched once for the whole range
        rather than per bucket: the widened window makes it a superset for every bucket, and the
        `start_time` predicate below is what assigns each row to one. A class absent from the mapping is
        selected by its native predicate if it has one, and matches nothing if it does not.

        **The caller's boundary and the dashboard's filter are two conjuncts in the one `WHERE`, never
        one per `FILTER`.** An aggregate's `FILTER` sees only the rows the `WHERE` admitted, so both
        narrowings reach all four class columns at once - the argument `sum_for_dimension_values` makes
        at length, and the reason a chart drawn for a plain member no longer foots their own narrowed
        rows with the whole platform's spend on the same week.
        """
        ids = resolved_ids or {}
        narrowing = _narrowing(access, report_filter)

        def shape(resolved: bool) -> ReadShape:
            return ReadShape(narrowing, _traffic_class_aggregates(ids if resolved else {}))

        result = await self._read(session, period, shape, resolved_ids=_union_of(*ids.values()), width=width)
        totals: dict[tuple[datetime, TrafficClass], Decimal] = {}
        for row in result.all():
            bucket_start = row[0]
            for index, cls in enumerate(TrafficClass):
                totals[(bucket_start, cls)] = Decimal(row[index + 1])
        return totals

    async def sum_for_traffic_classes(
        self,
        session: AsyncSession,
        period: TimeRange,
        resolved_ids: dict[TrafficClass, tuple[str, ...]] | None = None,
    ) -> dict[TrafficClass, Decimal]:
        """`SUM(spend)` per traffic class over the whole period, in **one** scan.

        `sum_by_time_bucket` with the time grouping removed, and on the port rather than left to a
        caller adding that method's buckets up: the caller's sum would be a *second* combining rule
        for one class's money, so a window total and the chart drawn beside it would be free to
        disagree. Reading both class splits from the same filter list - `_traffic_class_aggregates` - is
        what makes them arithmetically the same question asked at two grains.

        **No `GROUP BY` at all, because there is nothing to group by.** One filtered aggregate per
        class over the whole range is one row. Grouping on the attribution columns instead would
        re-express each class as a grouping key, which is the substitution
        `traffic_class_predicate` exists to prevent - see `sum_for_holders` for the same argument
        made about holders.

        `resolved_ids` carries the metric store's answer **per class**, exactly as the bucketed read
        takes it; a class absent from the mapping matches nothing rather than everything.
        """
        ids = resolved_ids or {}

        def shape(resolved: bool) -> ReadShape:
            return ReadShape(None, _traffic_class_aggregates(ids if resolved else {}))

        row = (await self._read(session, period, shape, resolved_ids=_union_of(*ids.values()))).one()
        return {cls: Decimal(row[index]) for index, cls in enumerate(TrafficClass)}

    async def sum_for_spend_groups(
        self,
        session: AsyncSession,
        period: TimeRange,
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> dict[SpendGroup, Decimal]:
        """`SUM(spend)` per coarse summary group over the whole period, in **one** scan.

        **One statement because the four figures are printed on one screen and have to foot against
        each other.** The alternative a handler would otherwise write - the platform card as the total
        less the CLI figure - subtracts two independently floored numbers, each carrying its own flag
        and its own portion, and the difference is a figure nobody qualified. Read in one scan they
        are four aggregates over one admitted row set, so `TOTAL = PLATFORM + CLI` holds by
        construction rather than by a caller's arithmetic.

        **The platform column is the NULL-safe complement of the CLI selector (`cli_complement`), of
        which `attr_category IS DISTINCT FROM 'cli'` is the guarded conjunct, and `<> 'cli'` is the
        silent version of this read.** An unattributed row carries a NULL category; `NULL <> 'cli'` is *unknown*, an
        aggregate `FILTER` admits no unknown, and the platform card would quietly omit exactly the
        money the total beside it still counts - two cards on one screen that no longer add up, with
        neither looking wrong.

        The predicates come only from `spend_group_predicate`, for the reason the class split takes
        its own from `traffic_class_predicate`: a card and the chart under it must not come to mean
        different things by "CLI". The total's column carries no `FILTER` at all - selecting every row
        is the absence of a predicate, not a predicate that matches everything.

        `sum_for_spend_groups_per_attribution` answers many filters at once and must give each the
        figures this read gives it; `TestTheBatchedSpendGroupReadEqualsTheSingleRead` keeps the two equal.
        """
        shape = ReadShape(_narrowing(access, report_filter), _spend_group_aggregates())
        row = (await self._read(session, period, _id_free(shape))).one()
        return by_spend_group(row)

    async def sum_for_spend_groups_per_attribution(
        self,
        session: AsyncSession,
        period: TimeRange,
        group_key: tuple[str, ...],
        access: AccessScope | None = None,
        common_filter: ReportFilter | None = None,
    ) -> list[Sequence[object]]:
        """`sum_for_spend_groups` per distinct value of `group_key`, in **one** scan of a fixed size.

        A per-row table asks the same question once per row, each row naming its own person or project.
        Written into the statement, those terms make it grow with the table; here the `WHERE` holds the
        boundary and `common_filter` - the term every row's filter shares - alone, and `SpendGroupBatch`
        assigns the grouped rows to each row's filter in Python. Each row leads with its `group_key`
        values, then the `_spend_group_aggregates` columns, so the group split keeps its one definition.

        `TestTheBatchedSpendGroupReadEqualsTheSingleRead` keeps each filter's figures equal to
        `sum_for_spend_groups` for that filter.
        """
        shape = ReadShape(_narrowing(access, common_filter), _spend_group_aggregates(), group_by=group_key)
        return (await self._read(session, period, _id_free(shape))).all()

    async def unclassified_residue(
        self,
        session: AsyncSession,
        period: TimeRange,
        resolved_ids: tuple[str, ...],
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> tuple[Decimal, int]:
        """The residue of a class split: the rows **no** class claims, over the whole range.

        **Not the assistant class's unresolved rows, and the difference is the whole point.** That
        set contains every CLI, workflow and embedding row - roughly the other three series - so
        naming it as the assistant series' missing money would report three quarters of a healthy
        chart as absent from one line of it. `unclassified_predicate` is the NULL-safe complement of
        the four selectors instead, spelled conjunct by conjunct because the negation of a disjunction
        over nullable columns drops exactly the unattributed rows a residue exists to count.

        **One figure for the range, handed to every class key**, not one per class and never one per
        bucket. The rows it names belong to no series, so no series owns a larger share of it than
        another; a chart renders it once as *an amount is missing from this window*.

        Returns money **and** rows, in that order, for `dimension_residue`'s reason: 92.4% of rows
        carry 1.33% of spend, so a row-based portion misstates a money figure by more than an order
        of magnitude.

        `access` and `report_filter` narrow this read exactly as they narrow the scan beside it. A
        residue narrowed on one side only either publishes the magnitude of spend the caller may not
        see, or labels their figure with a hole none of their rows is in.
        """
        narrowing = _narrowing(access, report_filter)

        def shape(resolved: bool) -> ReadShape:
            unclaimed = unclassified_predicate(resolved_ids if resolved else ())
            return ReadShape(and_(narrowing, unclaimed), _SPEND_AND_ROWS)

        return await self._total_and_rows(session, period, shape, resolved_ids=resolved_ids)

    async def sum_for_period(self, session: AsyncSession, period: TimeRange) -> Decimal:
        """`SUM(spend)` over every holder for a period - a cross-holder reporting read.

        Permitted without an attribution predicate, but still time-bounded: a query with no time
        bound is invalid on this table regardless of what else it filters.
        """
        return await self._total(session, period, _id_free(ReadShape(None, _SPEND)))

    async def sum_for_correlation_id(
        self,
        session: AsyncSession,
        *,
        run_id: str | None = None,
        request_id: str | None = None,
    ) -> Decimal | None:
        """What one CodeMie unit of work cost. **No time bound, deliberately.**

        Two reasons, and the second is the sharper one. The bound exists elsewhere to stop an
        unbounded scan and an id is already a bounded selector - one call resolves to a handful of
        attempt rows - so a bound would restrict nothing. **And it would cost correctness:** a
        caller with no range to supply must invent one, and the obvious invention, the request's
        own start day, silently drops a retry attempt that landed on the other side of a period
        boundary. The id-only lookup returns the true cost; the windowed one under-reports it.

        This exemption does **not** transfer to selecting on `codemie_request_id` against an id
        set produced by a report - see `sum_for_dimension_ids`, which carries the range predicate
        mandatorily because its id set is arbitrarily large.

        Returns `None` when no row matched, which is **not** zero: it means the cost is unsettled,
        and whether that is *pending* or *unavailable* is decided by ledger readiness above this
        layer. A failure row, by contrast, is a matched row carrying a real zero.
        """
        predicate = self._correlation_predicate(run_id=run_id, request_id=request_id)
        # Deliberately not coalesced: SUM over no rows is NULL, and NULL is the signal here.
        result = await session.execute(select(func.sum(Row.spend)).where(predicate))
        return result.scalar_one_or_none()

    async def count_requests(self, session: AsyncSession, period: TimeRange) -> int:
        """How many requests a period saw - **distinct calls, not rows**.

        LiteLLM writes one row per upstream attempt, so a proxy-side retry is several rows,
        several costs and **one** request. Counting and summing treat rows differently on purpose.

        Rows carrying no `codemie_run_id` are counted **one each**, and that is correct rather
        than a compromise: absent the id there is no evidence of duplication. They must be counted
        separately because `COUNT(DISTINCT ...)` discards NULLs entirely - every row written
        before the correlation header shipped carries one by contract, so the bare form would
        count zero requests for all of them.

        Failed requests are requests: they are included here and contribute zero spend.
        """
        bounded = within(period)
        result = await session.execute(
            select(
                func.count(func.distinct(Row.codemie_run_id)),
                func.count().filter(Row.codemie_run_id.is_(None)),
            ).where(bounded)
        )
        distinct_calls, rows_without_an_id = result.one()
        return int(distinct_calls) + int(rows_without_an_id)

    async def sum_for_dimension_scope(
        self,
        session: AsyncSession,
        period: TimeRange,
        scope: DimensionScope,
        resolved_ids: tuple[str, ...] = (),
    ) -> Decimal:
        """A dimension-scoped total, as **one sum over the union row set**.

        The union has two branches and they are summed in a single query rather than added in
        Python. That is the rule, not an optimisation: *"sum over the resolved row set - never
        aggregate per bucket and add"*, because a row shared between buckets is double-counted by
        the second form. Here the branches are disjoint by the precedence conjunct, so one scan is
        exactly "summed once, deduped on `(request_id, start_time)`" with no dedupe step that a
        later reader could omit.

        **Money always comes from here.** The branch a row arrives by decides only how its bucket
        is *identified*; the metric store never supplies an amount.

        Both branches may legitimately be absent - a dimension with no ledger column, asked about a
        scope the metric store resolved no ids for. The answer is then a **measured zero**, and the
        money that ought to have been in scope is named by `dimension_residue`, which is what keeps
        the empty answer honest rather than merely small.

        The `start_time` range is **mandatory** on both branches, and the single-request exemption
        does not transfer to it: that exemption holds because one call resolves to a handful of
        attempt rows, whereas a report's id set is arbitrarily large and the same waiver would
        scan the whole table. The caller owns making the id set a *superset* of what the period
        needs - a metric document's timestamp is emission time and never assigns a row to a period.
        Only `start_time` does, and it does it here.
        """
        _require_union_dimension(scope)
        in_scope = _union_predicate(scope, resolved_ids)
        if in_scope is None:
            return Decimal("0")

        def shape(resolved: bool) -> ReadShape:
            return ReadShape(in_scope if resolved else _or_nothing(_union_predicate(scope, ())), _SPEND)

        return await self._total(session, period, shape, resolved_ids=resolved_ids)

    async def sum_for_dimension_values(
        self,
        session: AsyncSession,
        period: TimeRange,
        dimension: SpendDimension,
        ids_by_value: Mapping[str, tuple[str, ...]],
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> dict[str, Decimal]:
        """`SUM(spend)` per dimension value, in one scan per chunk - `sum_for_holders` for a dimension.

        **Why one filtered aggregate per value and emphatically not a `GROUP BY`.** Exactly one of
        the five dimensions has a ledger column, so `GROUP BY <the dimension column>` is a statement
        that is correct for `workflow` and returns **nothing at all** for assistant, conversation and
        datasource - whose rows are identified only by the correlation-id set the metric store
        resolved, which no column can be grouped on. `SUM(spend) FILTER (WHERE <that value's union
        predicate>)` is uniform over both kinds, because it asks each value the same question
        `sum_for_dimension_scope` asks about one: it *is* the same expression, evaluated in the same
        scan, so the two cannot disagree about one value.

        **The keys of `ids_by_value` are the values to answer for**, and every one of them appears in
        the result. An **empty** id tuple is a real answer rather than a gap - for a dimension with a
        column it means the metric store bucketed none of the pre-column rows, and for a dimension
        without one it means the value resolved to no rows at all, which is a measured zero whose
        missing money `dimension_residue` names. Deciding that an *absent* value matches nothing
        rather than everything belongs to the caller, above this layer, in the same place the
        traffic-class reads decide it.

        The `start_time` range is mandatory here for the reason `sum_for_dimension_scope` gives, and
        the disjunction narrows each chunk's scan to rows some value in that chunk could claim - it
        cannot move a single figure, because every `FILTER` predicate implies it.

        **`access` narrows every value's figure, and one conjunct in the `WHERE` is what does it.** An
        aggregate's `FILTER` sees only rows the `WHERE` admitted, so a boundary there narrows all
        hundred columns of a chunk at once - whereas ANDing it into each value's own predicate would
        emit the clause twice per value (once in its `FILTER`, once in the disjunction built from those
        same predicates) and put two hundred copies of a caller's project list in one statement to no
        effect. This is the read the disclosure was found on: the row set a caller is shown is narrowed
        by `SecureQueryBuilder`, and a figure computed without the boundary foots that narrowed table
        with the whole platform's spend on the same workflow or assistant.

        **`report_filter` is a second conjunct in the same `WHERE`, for the same reason.** A table read
        under a dashboard's users or projects filter lists only the values Elasticsearch found in that
        filter, but a value's ids - and a workflow's or a model's own column - select its rows in every
        project and for every person; without the filter a shared assistant's row foots the filtered
        table with its spend everywhere else.
        """
        _require_union_dimension(DimensionScope(dimension))

        # A value whose union predicate is `None` selects no rows by construction, so it needs no
        # column: its measured zero is the identical answer `sum_for_dimension_scope` returns for the
        # same scope without querying either, and seeding it here is what keeps the two in step.
        totals: dict[str, Decimal] = dict.fromkeys(ids_by_value, Decimal("0"))
        selectable = [
            (value, ids, in_scope)
            for value, ids in ids_by_value.items()
            if (in_scope := _union_predicate(DimensionScope(dimension, value), ids)) is not None
        ]

        for start in range(0, len(selectable), HOLDER_BATCH_SIZE):
            chunk = selectable[start : start + HOLDER_BATCH_SIZE]
            shape = _dimension_values_shape(dimension, chunk, access, report_filter)
            resolved_ids = _union_of(*(ids for _, ids, _ in chunk))
            row = (await self._read(session, period, shape, resolved_ids=resolved_ids)).one()
            # `strict=True` because a row narrower than its own chunk would otherwise silently pair
            # each figure with the *next* value's key - every number plausible and every one wrong.
            for (value, _, _), total in zip(chunk, row, strict=True):
                totals[value] = Decimal(total)

        return totals

    async def sum_for_composed_scope(
        self,
        session: AsyncSession,
        period: TimeRange,
        scope: ComposedScope,
        *,
        dimension_ids: tuple[str, ...] = (),
        traffic_class_ids: tuple[str, ...] = (),
    ) -> Decimal:
        """A conjunction-scoped total, as **one sum over the composed row set**.

        One scan over one predicate, and not two scoped sums added: *"sum over the resolved row set -
        never aggregate per bucket and add."* Here the rule bites harder than it does for the
        dimension union, whose branches are disjoint by construction - two *filter* terms' row sets
        overlap by design, so adding a project figure to a workflow figure counts every row inside
        both twice. The conjunction has no such freedom: a row either satisfies every term or is not
        in the answer.

        **Returns a measured zero when the scope can match nothing**, without querying - the identical
        answer `sum_for_dimension_scope` returns for a dimension scope with neither branch, and
        seeded here rather than queried for so the two stay in step.

        The `start_time` range is mandatory, for the reason `sum_for_dimension_scope` gives: an id set
        a report produced is arbitrarily large, and only `start_time` assigns a row to a period.
        """
        in_scope = composed_predicate(scope, dimension_ids=dimension_ids, traffic_class_ids=traffic_class_ids)
        if in_scope is None:
            return Decimal("0")

        def shape(resolved: bool) -> ReadShape:
            return ReadShape(in_scope if resolved else _or_nothing(composed_predicate(scope)), _SPEND)

        resolved_ids = _union_of(dimension_ids, traffic_class_ids)
        return await self._total(session, period, shape, resolved_ids=resolved_ids)

    async def sum_for_composed_scope_by_traffic_class(
        self,
        session: AsyncSession,
        period: TimeRange,
        scope: ComposedScope,
        *,
        dimension_ids: tuple[str, ...] = (),
        ids_by_class: dict[TrafficClass, tuple[str, ...]] | None = None,
    ) -> dict[TrafficClass, Decimal]:
        """`SUM(spend)` per traffic class **within a composed scope**, in one scan.

        `sum_for_traffic_classes` with the scope's own predicate added to the range bound, and it
        reuses that method's filter list rather than restating it: `_traffic_class_aggregates` is where each
        class's definition lives, so a per-person split and the platform-wide split cannot come to
        mean different things by "CLI". A `GROUP BY` is again impossible for the reason that method
        gives - two of the four classes have no column to group on at all.

        **Every class is keyed, including where the scope matches nothing.** A scope that can match no
        row is a measured zero for each class rather than an absent mapping, on the same footing as
        the single-figure read above: the caller asked about four series and must get four.
        """
        _require_class_groupable_scope(scope)
        in_scope = composed_predicate(scope, dimension_ids=dimension_ids)
        if in_scope is None:
            return dict.fromkeys(TrafficClass, Decimal("0"))
        classes = ids_by_class or {}

        def shape(resolved: bool) -> ReadShape:
            if resolved:
                return ReadShape(in_scope, _traffic_class_aggregates(classes))
            return ReadShape(_or_nothing(composed_predicate(scope)), _traffic_class_aggregates({}))

        resolved_ids = _union_of(dimension_ids, *classes.values())
        row = (await self._read(session, period, shape, resolved_ids=resolved_ids)).one()
        return {cls: Decimal(row[index]) for index, cls in enumerate(TrafficClass)}

    async def count_executions_for_scope(
        self,
        session: AsyncSession,
        period: TimeRange,
        scope: DimensionScope,
        resolved_ids: tuple[str, ...] = (),
    ) -> int:
        """Workflow executions in a scope, counted over **the union** row set.

        On the workflow path `codemie_request_id` *is* the execution id, so no separate execution
        column is needed and none exists.

        The NULL-correlation term is required for the same reason as in `count_requests`: written
        bare, `COUNT(DISTINCT codemie_request_id)` counts **zero** executions for every row whose
        correlation id is NULL - which the uploader contract guarantees for every row written
        before the header shipped. Absent the id there is no evidence of duplication, so each such
        row counts as one.
        """
        _require_union_dimension(scope)
        in_scope = _union_predicate(scope, resolved_ids)
        if in_scope is None:
            return 0
        result = await session.execute(
            select(
                func.count(func.distinct(Row.codemie_request_id)),
                func.count().filter(Row.codemie_request_id.is_(None)),
            ).where(and_(within(period), in_scope))
        )
        distinct_executions, rows_without_an_id = result.one()
        return int(distinct_executions) + int(rows_without_an_id)

    async def dimension_residue(
        self,
        session: AsyncSession,
        period: TimeRange,
        scope: DimensionScope,
        resolved_ids: tuple[str, ...],
        access: AccessScope | None = None,
        report_filter: ReportFilter | None = None,
    ) -> tuple[Decimal, int]:
        """The unresolved residue - what makes a dimension-grouped answer partial.

        **Not every row the ledger could not bucket.** A row the metric store resolved is not a
        hole, and treating every one as a hole would report a wholly pre-rollout period as 100%
        partial while nothing was in fact missing. The residue is a row the ledger cannot bucket
        *and* the metric store did not resolve.

        **The dimension is an argument, not a fixed column, and that is load-bearing.** Three of
        the five dimensions have no ledger column at all, so the `IS NULL` conjunct simply does not
        apply to them - applying the workflow column's anyway would drop every in-scope row that
        happens to sit inside a workflow and put its money in no bucket and in no residue.

        **The `IS NULL` disjunct on the correlation id is load-bearing too, and dropping it is
        silent.** SQL's three-valued logic makes `NULL <> ALL (:ids)` evaluate to *unknown*, not
        true, so a bare comparison excludes exactly the rows guaranteed to carry a NULL
        `codemie_request_id`. Such a row also fails the id-set branch, so without this disjunct its
        money lands in no bucket and in no residue: the report shrinks and the partial label never
        fires to say why. Every test built from rows that *have* correlation ids passes either way.

        Returns money **and** rows, in that order, because a money-bearing surface renders the
        money: 92.4% of rows carry 1.33% of spend, so a row-based portion misstates a money figure
        by more than an order of magnitude. The count is a diagnostic reported beside it, never
        instead of it.

        **`access` narrows the residue too, and narrowing only the figure would be its own
        disclosure.** A residue is money *and a row count* published beside a figure, so an
        unnarrowed one tells a caller the magnitude of spend they are forbidden to see - and it would
        also over-label their own figure, naming a hole none of their rows is in. The Elasticsearch
        source this replaced behaved exactly this way: a document failing the injected filter
        contributed to no bucket and to no reported hole.

        **`report_filter` narrows the residue exactly as it narrows the figure.** A residue left on the
        whole window labels a filtered table with every other project's and person's unbucketable
        money: a table filtered to a $13.48 personal project read "(at least; $23.31 unattributed)".
        """
        _require_union_dimension(scope)
        # No special case for an empty id set: an empty resolved-id set leaves every otherwise
        # unbucketable row unresolved, and `request_unresolved(())` is `true()`, i.e. every row. That
        # is always the case for `datasource` (no metric-store attribute exists) and `model` (resolved
        # wholly by `model_group`), whose id set is always empty.
        is_null = _dimension_is_null(scope)
        narrowing = _narrowing(access, report_filter)

        def shape(resolved: bool) -> ReadShape:
            terms = [narrowing, request_unresolved(resolved_ids if resolved else ())]
            if is_null is not None:
                terms.append(is_null)
            return ReadShape(and_(*terms), _SPEND_AND_ROWS)

        return await self._total_and_rows(session, period, shape, resolved_ids=resolved_ids)

    async def project_dimension_residue(
        self, session: AsyncSession, sub_range: TimeRange, access: AccessScope | None = None
    ) -> tuple[Decimal, int]:
        """The money in `sub_range` that carries **no project**, with its row count beside it.

        This is what makes "naming the portion" executable for a project-keyed figure. Below the
        identity floor a principal-form row carries `attr_project = NULL` and none can be
        reconstructed, because the composer never encoded a project and its composition is
        untouchable. That is not a gap to be narrowed later; it is a fact about what was written.

        **Every row in the ledger is CodeMie's.** The proxy is used by CodeMie exclusively - an
        operational guarantee the owner gave on 2026-09-08 - so a parsed row carrying no project is
        by definition a hole in CodeMie's coverage, and the portion needs no origin conjunct.

        **`access` narrows the portion, and for a restricted caller it empties it outright.** That is
        arithmetic rather than a policy choice: this portion counts rows carrying **no project**, and
        every clause of the boundary pins one, so no row here can satisfy a restricted scope. The
        alternative - publishing it unnarrowed beside a narrowed figure - would tell the caller the
        magnitude of spend they may not see *and* label their own figure with a hole none of their rows
        is in. `access_predicate` states the rule this follows: rows the boundary excludes leave the
        figure and the residue alike. `None` narrows nothing, which is what a budget read passes.
        """
        no_project = narrowed_by_access(and_(Row.attr_project.is_(None), Row.attr_source == _PARSED), access)
        return await self._total_and_rows(session, sub_range, _id_free(ReadShape(no_project, _SPEND_AND_ROWS)))

    async def unattributed_spend(self, session: AsyncSession, period: TimeRange) -> tuple[Decimal, int]:
        """Spend that belongs to someone the parser could not name.

        Surfaced as a first-class metric with a non-zero value treated as a defect. Never absorbed
        into a default owner - an unattributed row is spend that belongs to someone.
        """
        unattributed = ReadShape(Row.attr_form == "unattributed", _SPEND_AND_ROWS)
        return await self._total_and_rows(session, period, _id_free(unattributed))

    async def earliest_start_time(self, session: AsyncSession) -> datetime | None:
        """The oldest `start_time` present, for the activation check only.

        **This is not the coverage floor and must never be used as one.** The floor is a value
        published once at activation, and a minimum over a table still being filled reports
        coverage the table does not have: it drops to the oldest batch already inserted while
        later periods are still empty, so reads routed there return totals missing every unloaded
        row. Exposed here so the activation sequence can report the completed load's oldest row,
        and for no other purpose.
        """
        result = await session.execute(select(func.min(Row.start_time)))
        return result.scalar_one_or_none()

    @staticmethod
    def _correlation_predicate(*, run_id: str | None, request_id: str | None) -> ColumnElement[bool]:
        """Exactly one id, because the two are not interchangeable.

        Which one to use is fixed by the grain of the record being answered for, and it is the
        caller's to choose: this layer cannot tell which grain was meant, and guessing multiplies
        the money.
        """
        if (run_id is None) == (request_id is None):
            raise ValueError("give exactly one of run_id or request_id - the two ids are not interchangeable")
        if run_id is not None:
            return Row.codemie_run_id == run_id
        return Row.codemie_request_id == request_id

    async def _read(
        self,
        session: AsyncSession,
        period: TimeRange,
        shape_of: ShapeOf,
        *,
        resolved_ids: tuple[str, ...] = (),
        width: str | None = None,
    ) -> Result:
        """Issue one windowed read: from the hourly projection when enabled and exact there, else raw.

        `spend` is stored `numeric`, so every sum is exact and order-independent on either source: the
        same rows summed as floating point return different totals depending on scan order, parallel
        workers and partition count - measured on this schema at 200,000 rows. The driver returns
        `numeric` as `Decimal`, and conversion to float happens deliberately at the REST boundary,
        never here.

        `resolved_ids` is the union of every metric-store id set the shape applies - the rows the
        projected read takes from raw rows - and `width`, when given, groups the read by time bucket.
        """
        if not self._projection:
            return await session.execute(raw_statement(period, shape_of(True), width))
        # Building a projected statement is pure CPU, several times the raw statement's for a hundred-holder
        # batch, so it runs on a worker thread while the loop keeps serving other requests.
        # Compiling and executing it stay here, with the session.
        statement = await asyncio.to_thread(read_statement, period, shape_of, resolved_ids, width)
        return await session.execute(statement)

    async def _identities_in(self, session: AsyncSession, period: TimeRange) -> tuple[frozenset[str], frozenset[str]]:
        """The user ids and the principals the rows of `period` carry, read the way the period's figures are."""
        rows = (await session.execute(identities_statement(period, self._projection))).all()
        return (
            frozenset(user_id for user_id, _principal in rows if user_id is not None),
            frozenset(principal for _user_id, principal in rows if principal is not None),
        )

    async def _total(
        self, session: AsyncSession, period: TimeRange, shape_of: ShapeOf, *, resolved_ids: tuple[str, ...] = ()
    ) -> Decimal:
        """A read of one bare money total."""
        return Decimal((await self._read(session, period, shape_of, resolved_ids=resolved_ids)).scalar_one())

    async def _total_and_rows(
        self, session: AsyncSession, period: TimeRange, shape_of: ShapeOf, *, resolved_ids: tuple[str, ...] = ()
    ) -> tuple[Decimal, int]:
        """A read of money and the ledger rows behind it - the residue and defect-metric shape."""
        spend, rows = (await self._read(session, period, shape_of, resolved_ids=resolved_ids)).one()
        return Decimal(spend), int(rows)


#: Callers import the singleton, matching every other repository in this package. It reads raw rows; the
#: ledger's composition root builds the repository it actually reads with (`build_spend_logs_ledger`).
spend_logs_repository = SpendLogsRepository()
