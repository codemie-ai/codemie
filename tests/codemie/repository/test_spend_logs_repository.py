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

"""Every attribution predicate this module builds - holder, dimension union and composed scope -
pinned as compiled SQL.

These assert the *shape* of each predicate rather than a figure, because the failure mode being
guarded is silent: a predicate that drops one conjunct still returns a plausible number, and the
number it returns is a different holder's. No database is needed - the predicates are compiled
against the PostgreSQL dialect with literals inlined.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import and_, or_
from sqlalchemy.dialects import postgresql

from codemie.repository.spend_logs_model import LiteLLMSpendLog as Row

from codemie.repository.spend_logs_repository import (
    _dimension_is_null,
    _ledger_branch,
    _metric_store_branch,
    _person_total_predicate,
    _require_class_groupable_scope,
    _require_union_dimension,
    composed_predicate,
    narrowed_by_filter,
    predicate_for_holder,
    report_filter_predicate,
    within,
)
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.spend.attribution import PROJECT_PREFIX
from codemie.service.spend.coverage import SpendDimension, TimeRange
from codemie.service.spend.ledger import (
    ComposedScope,
    DimensionScope,
    PersonalHolder,
    PersonTotalHolder,
    ProjectGroupHolder,
    ProjectHolder,
    ProjectMemberHolder,
    ReportFilter,
    UserTerm,
)
from codemie.service.spend.traffic_class import TrafficClass, traffic_class_predicate

USER = "aaaaaaaa-1111-1111-1111-111111111111"
ALIASES = frozenset({"alice", "alice@example.com"})
PERIOD = TimeRange(datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc))


def sql(clause) -> str:
    """The clause as PostgreSQL would see it, with literals inlined so it can be asserted on."""
    return str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).replace("\n", " ")


class TestTheRangePredicateIsHalfOpen:
    def test_it_is_lower_inclusive_and_upper_exclusive(self):
        rendered = sql(within(PERIOD))
        assert "start_time >=" in rendered
        assert "start_time <" in rendered
        assert "<=" not in rendered, "an inclusive upper bound double-counts a boundary row"

    def test_between_is_never_emitted(self):
        """`BETWEEN` is inclusive at both ends, so a boundary row lands in two period totals."""
        assert "BETWEEN" not in sql(within(PERIOD)).upper()

    def test_end_time_never_assigns_a_row_to_a_period(self):
        """A call spanning midnight belongs to the period it started in."""
        assert "end_time" not in sql(within(PERIOD))


class TestThePersonalHolderIsWhatThePersonsOwnCustomerWasCharged:
    def test_the_explicit_half_keys_on_the_charged_customer_not_on_the_form(self):
        """EPMCDME-15204: a project with no live budget routes the call to the personal budget.

        The row is attributed `project_scoped` but LiteLLM charged the person's own customer, so the
        explicit half admits any `end_user` outside the project-member grammar and the placeholder,
        whatever the row's form. The principal form survives only on the parsed half.
        """
        rendered = sql(predicate_for_holder(PersonalHolder(USER, BudgetCategory.PLATFORM, ALIASES)))
        assert f"litellm_spend_logs.end_user NOT LIKE '{PROJECT_PREFIX}'" in rendered
        assert "end_user NOT IN ('N/A', '')" in rendered
        assert rendered.count("attr_form = 'principal'") == 1

    def test_a_bare_user_id_match_is_not_the_predicate(self):
        """The sharpest available mistake here is dropping the project-member exclusion.

        An explicit row carrying a project lands `project_scoped` with `attr_user_id` populated, so a
        bare `attr_user_id = :user_id` would absorb the person's project-budget spend into their
        personal budget - a redefinition of what a personal budget covers, not a widened query.
        """
        rendered = sql(predicate_for_holder(PersonalHolder(USER, BudgetCategory.PLATFORM, ALIASES)))
        assert f"end_user NOT LIKE '{PROJECT_PREFIX}' || '%%' ESCAPE '/'" in rendered
        assert "project_scoped" not in rendered, "no project-scoped row is admitted by its form alone"

    def test_a_row_that_charged_no_customer_is_not_personal(self):
        """The uploader stores an absent `end_user` as `'N/A'`, which passes the project-member `NOT LIKE`.

        The own-credentials bypass sends a complete identity but no customer, so such a row lands
        explicit - `project_scoped` or principal - while LiteLLM charged nobody, not the person's own
        customer.
        """
        rendered = sql(predicate_for_holder(PersonalHolder(USER, BudgetCategory.PLATFORM, ALIASES)))
        assert "end_user NOT IN ('N/A', '')" in rendered

    def test_the_fallback_half_keeps_the_principal_form(self):
        """A parsed row's form was read from `end_user`, so its principal form is the own customer."""
        rendered = sql(predicate_for_holder(PersonalHolder(USER, BudgetCategory.PLATFORM, ALIASES)))
        fallback = rendered[rendered.index("attr_source = 'parsed'") :]
        assert "attr_form = 'principal'" in fallback

    def test_the_explicit_half_keys_on_the_canonical_user_id(self):
        rendered = sql(predicate_for_holder(PersonalHolder(USER, BudgetCategory.PLATFORM, ALIASES)))
        assert "attr_source = 'explicit'" in rendered
        assert f"attr_user_id = '{USER}'" in rendered

    def test_the_fallback_half_matches_the_whole_alias_set(self):
        """A person is not one identifier on rows the parser attributed."""
        rendered = sql(predicate_for_holder(PersonalHolder(USER, BudgetCategory.PLATFORM, ALIASES)))
        assert "attr_source = 'parsed'" in rendered
        for alias in ALIASES:
            assert alias in rendered

    def test_the_category_constrains_both_halves(self):
        rendered = sql(predicate_for_holder(PersonalHolder(USER, BudgetCategory.CLI, ALIASES)))
        assert rendered.count("attr_category = 'cli'") == 2

    def test_an_unresolvable_bridge_yields_the_explicit_half_only(self):
        """`None` aliases is not an empty set: the person's fallback rows cannot be identified.

        The caller must surface that as an uncomputable `identity_bridge` portion rather than
        return this half as though it were complete.
        """
        rendered = sql(predicate_for_holder(PersonalHolder(USER, BudgetCategory.PLATFORM, None)))
        assert "parsed" not in rendered
        assert "attr_principal" not in rendered
        assert "attr_source = 'explicit'" in rendered

    def test_an_empty_alias_set_behaves_as_no_fallback_half(self):
        rendered = sql(predicate_for_holder(PersonalHolder(USER, BudgetCategory.PLATFORM, frozenset())))
        assert "attr_principal" not in rendered


class TestTheProjectHolders:
    def test_the_project_holder_is_project_scoped_and_category_bound(self):
        rendered = sql(predicate_for_holder(ProjectHolder("acme", BudgetCategory.PLATFORM)))
        assert "attr_form = 'project_scoped'" in rendered
        assert "attr_project = 'acme'" in rendered
        assert "attr_category = 'platform'" in rendered

    def test_the_project_holder_does_not_key_on_a_user(self):
        rendered = sql(predicate_for_holder(ProjectHolder("acme", BudgetCategory.PLATFORM)))
        assert "attr_user_id" not in rendered

    def test_the_member_holder_adds_the_user_and_keeps_everything_else(self):
        """Keyed on project AND user id - a pre-rollout principal row carries neither."""
        rendered = sql(predicate_for_holder(ProjectMemberHolder("acme", BudgetCategory.PLATFORM, USER)))
        assert "attr_form = 'project_scoped'" in rendered
        assert "attr_project = 'acme'" in rendered
        assert "attr_category = 'platform'" in rendered
        assert f"attr_user_id = '{USER}'" in rendered

    def test_the_group_holder_widens_the_project_to_a_set(self):
        rendered = sql(predicate_for_holder(ProjectGroupHolder(frozenset({"acme", "beta"}), BudgetCategory.PLATFORM)))
        assert "attr_project IN " in rendered
        assert "acme" in rendered and "beta" in rendered
        assert "attr_form = 'project_scoped'" in rendered


class TestThePersonTotalIsTheOneHolderThatSpansBothPots:
    def test_it_uses_the_bare_user_id_match(self):
        """The one place the bare form is correct - and the two pots are disjoint on `attr_form`,
        so spanning them does not double-count."""
        rendered = sql(predicate_for_holder(PersonTotalHolder(USER, ALIASES)))
        assert f"attr_user_id = '{USER}'" in rendered

    def test_it_does_not_restrict_the_form(self):
        assert "attr_form = 'project_scoped'" not in sql(predicate_for_holder(PersonTotalHolder(USER, ALIASES)))

    def test_it_does_not_restrict_the_category(self):
        """ "Across everything" includes every category."""
        assert "attr_category" not in sql(predicate_for_holder(PersonTotalHolder(USER, ALIASES)))

    def test_its_fallback_half_is_still_principal_scoped(self):
        """Only principal-form rows carry a principal to match against."""
        rendered = sql(predicate_for_holder(PersonTotalHolder(USER, ALIASES)))
        assert "attr_form = 'principal'" in rendered

    def test_an_unresolvable_bridge_yields_the_explicit_half_only(self):
        rendered = sql(predicate_for_holder(PersonTotalHolder(USER, None)))
        assert "attr_principal" not in rendered


class TestEveryHolderKindHasAPredicate:
    @pytest.mark.parametrize(
        "holder",
        [
            PersonalHolder(USER, BudgetCategory.PLATFORM, ALIASES),
            ProjectHolder("acme", BudgetCategory.PLATFORM),
            ProjectMemberHolder("acme", BudgetCategory.PLATFORM, USER),
            ProjectGroupHolder(frozenset({"acme"}), BudgetCategory.PLATFORM),
            PersonTotalHolder(USER, ALIASES),
        ],
    )
    def test_it_resolves(self, holder):
        assert sql(predicate_for_holder(holder))

    def test_an_unknown_holder_is_refused_rather_than_silently_matching_everything(self):
        class Rogue:
            pass

        with pytest.raises(TypeError, match="no predicate is defined"):
            predicate_for_holder(Rogue())  # type: ignore[arg-type]


class TestTheCorrelationIdLookup:
    def test_exactly_one_id_is_required(self):
        """The two ids are not interchangeable, and this layer cannot tell which grain was meant.

        Hydrating one LLM call by the outer request id writes the whole request's cost into every
        pending call in it, so a ten-call agent loop reports roughly ten times the true cost.
        """
        from codemie.repository.spend_logs_repository import SpendLogsRepository

        for kwargs in ({}, {"run_id": "r", "request_id": "q"}):
            with pytest.raises(ValueError, match="exactly one"):
                SpendLogsRepository._correlation_predicate(
                    run_id=kwargs.get("run_id"), request_id=kwargs.get("request_id")
                )

    def test_it_carries_no_time_bound(self):
        """Inventing one drops a retry attempt that landed in the next period."""
        from codemie.repository.spend_logs_repository import SpendLogsRepository

        rendered = sql(SpendLogsRepository._correlation_predicate(run_id="run-1", request_id=None))
        assert "start_time" not in rendered
        assert rendered.strip() == "litellm_spend_logs.codemie_run_id = 'run-1'"


# ---------------------------------------------------------------------------
# The dimension union. These pin the defect with no other detector: exactly one dimension has a
# ledger column, and forcing that column's predicate onto a dimension that has none silently drops
# every in-scope row which happens to sit inside a workflow.
# ---------------------------------------------------------------------------

IDS = ("req-1", "req-2")

WITH_A_COLUMN = (SpendDimension.WORKFLOW, SpendDimension.EXECUTION)
WITHOUT_A_COLUMN = (SpendDimension.ASSISTANT, SpendDimension.CONVERSATION, SpendDimension.DATASOURCE)


class TestOnlyOneDimensionHasALedgerColumn:
    @pytest.mark.parametrize("dimension", WITH_A_COLUMN)
    def test_a_dimension_with_a_column_selects_on_it(self, dimension):
        assert "codemie_workflow_id IS NOT NULL" in sql(_ledger_branch(DimensionScope(dimension)))

    @pytest.mark.parametrize("dimension", WITHOUT_A_COLUMN)
    def test_a_dimension_without_one_has_no_ledger_branch_at_all(self, dimension):
        """`None` is the answer, not an empty predicate: the ledger can bucket none of these rows."""
        assert _ledger_branch(DimensionScope(dimension)) is None

    def test_the_workflow_scope_value_matches_the_workflow_column(self):
        assert "codemie_workflow_id = 'wf-9'" in sql(_ledger_branch(DimensionScope(SpendDimension.WORKFLOW, "wf-9")))

    def test_the_execution_scope_value_matches_the_correlation_id(self):
        """On the workflow path `codemie_request_id` *is* the execution id."""
        rendered = sql(_ledger_branch(DimensionScope(SpendDimension.EXECUTION, "exec-9")))
        assert "codemie_request_id = 'exec-9'" in rendered
        assert "codemie_workflow_id IS NOT NULL" in rendered, "the column tells an execution id from a chat one"


class TestTheMetricStoreBranchNeverBorrowsAnotherDimensionsColumn:
    @pytest.mark.parametrize("dimension", WITHOUT_A_COLUMN)
    def test_no_workflow_conjunct_leaks_onto_a_columnless_dimension(self, dimension):
        """The defect this class exists for.

        With `codemie_workflow_id IS NULL` forced on, an assistant-grouped report loses every
        assistant invocation made inside a workflow: real money, in scope, in no bucket and in no
        residue. The report shrinks and nothing marks it.
        """
        rendered = sql(_metric_store_branch(DimensionScope(dimension, "a-1"), IDS))
        assert "codemie_workflow_id" not in rendered
        assert "codemie_request_id IN ('req-1', 'req-2')" in rendered

    @pytest.mark.parametrize("dimension", WITH_A_COLUMN)
    def test_a_dimension_with_a_column_defers_to_it(self, dimension):
        """Precedence: the populated column wins, so the id-set branch takes only the NULL rows."""
        assert "codemie_workflow_id IS NULL" in sql(_metric_store_branch(DimensionScope(dimension), IDS))

    def test_an_empty_id_set_yields_no_branch_rather_than_an_empty_in_clause(self):
        assert _metric_store_branch(DimensionScope(SpendDimension.ASSISTANT, "a-1"), ()) is None

    @pytest.mark.parametrize("dimension", WITHOUT_A_COLUMN)
    def test_a_columnless_dimension_has_no_is_null_conjunct(self, dimension):
        assert _dimension_is_null(DimensionScope(dimension)) is None


class TestTheTwoDimensionsThatAreNotResolvedThisWay:
    @pytest.mark.parametrize("dimension", [SpendDimension.PROJECT, SpendDimension.IDENTITY_BRIDGE])
    def test_they_are_rejected_loudly(self, dimension):
        """Accepting either silently would answer the question with the wrong predicate.

        The project dimension needs its floor-scoped sub-range and its origin conjunct; the
        identity bridge has no row set to query at all.
        """
        with pytest.raises(ValueError):
            _require_union_dimension(DimensionScope(dimension))

    @pytest.mark.parametrize("dimension", WITH_A_COLUMN + WITHOUT_A_COLUMN)
    def test_the_other_five_are_accepted(self, dimension):
        _require_union_dimension(DimensionScope(dimension))


# ---------------------------------------------------------------------------
# The composed scope. A conjunction has one failure the single-term predicates cannot have: a term
# that is *absent* and one that *matches nothing* look identical in a figure, and getting them the
# wrong way round either hands a caller the whole table or empties its report.
# ---------------------------------------------------------------------------

USER_TERM = UserTerm(USER, ALIASES)
WORKFLOW_TERM = DimensionScope(SpendDimension.WORKFLOW, "wf-1")
ASSISTANT_TERM = DimensionScope(SpendDimension.ASSISTANT, "asst-1")

FULL_SCOPE = ComposedScope(
    user=USER_TERM,
    project="acme",
    dimension=WORKFLOW_TERM,
    traffic_class=TrafficClass.CLI,
)


class TestTheComposedScopeRefusesWhatItCannotAnswerHonestly:
    def test_an_all_absent_scope_is_refused_rather_than_answered_as_the_platform_total(self):
        """The defect this type exists to prevent.

        A consumer building a scope from optional, agent-supplied arguments and omitting all of them
        would otherwise be handed the **platform total** in place of one person's spend, silently,
        on the surface an assistant quotes to that person.
        """
        with pytest.raises(ValueError, match="at least one term"):
            ComposedScope()

    def test_a_union_class_term_beside_the_same_dimension_is_refused(self):
        """Both resolve through one correlation-id set, so their two residues would share the one
        slot `partial` has for that dimension and the second would overwrite the first."""
        with pytest.raises(ValueError, match="same correlation-id set"):
            ComposedScope(dimension=ASSISTANT_TERM, traffic_class=TrafficClass.ASSISTANT)

    def test_a_project_owner_without_its_project_is_refused(self):
        """The owner qualifies the project term; alone it would be silently dropped from the read."""
        with pytest.raises(ValueError, match="project owners"):
            ComposedScope(user=USER_TERM, project_owners=(USER,))

    def test_a_native_class_term_composes_freely_with_any_dimension(self):
        """The useful case, and it must not be caught by the rule above: the workflow class is
        selected by a column of its own, so it has no residue to collide with."""
        assert ComposedScope(dimension=ASSISTANT_TERM, traffic_class=TrafficClass.WORKFLOW)

    def test_a_scope_grouped_by_class_must_not_pin_one(self):
        with pytest.raises(ValueError, match="must not pin one"):
            _require_class_groupable_scope(ComposedScope(user=USER_TERM, traffic_class=TrafficClass.CLI))

    def test_a_scope_grouped_by_class_must_not_name_a_union_class_dimension(self):
        """Every union class is a key of that read, so the collision arrives through the key set
        rather than through a second term. One class resolves that way now, so one dimension does."""
        with pytest.raises(ValueError, match="cannot also be split by class"):
            _require_class_groupable_scope(ComposedScope(dimension=DimensionScope(SpendDimension.ASSISTANT, "x")))

    @pytest.mark.parametrize(
        "dimension", [SpendDimension.WORKFLOW, SpendDimension.CONVERSATION, SpendDimension.DATASOURCE]
    )
    def test_a_dimension_no_class_resolves_through_is_groupable(self, dimension):
        """The datasource dimension is here rather than above because its class became **native** - it
        selects on `call_type` and carries no id set, so no residue of its can collide with anything."""
        _require_class_groupable_scope(ComposedScope(dimension=DimensionScope(dimension, "x")))


class TestEachTermAloneIsTheTermItsOwnReadUses:
    def test_the_user_term_spans_both_pots_and_is_not_the_personal_predicate(self):
        """The sharpest available mistake here, and it is item 15's trap.

        `_personal_predicate` excludes project-member customers - correctly, because a *personal
        budget* is what the person's own customer was charged. Borrowed for a scope it silently
        drops every call the person made against a project budget, which is most of a CLI figure.
        """
        rendered = sql(composed_predicate(ComposedScope(user=USER_TERM)))
        assert f"attr_user_id = '{USER}'" in rendered
        assert "attr_form = 'project_scoped'" not in rendered, "the explicit half must not pin a form"
        assert rendered.count("attr_form = 'principal'") == 1, "only the fallback half pins the form"
        assert "attr_category" not in rendered, "a scope has no category to constrain"

    def test_the_user_term_matches_both_identifier_forms(self):
        """A person is not one identifier on rows the parser attributed."""
        rendered = sql(composed_predicate(ComposedScope(user=USER_TERM)))
        assert "attr_source = 'parsed'" in rendered
        for alias in ALIASES:
            assert alias in rendered

    def test_the_user_term_is_the_person_total_holders_own_predicate(self):
        """Byte-identical, so a composed figure for one person cannot differ from their total."""
        assert sql(composed_predicate(ComposedScope(user=USER_TERM))) == sql(
            predicate_for_holder(PersonTotalHolder(USER, ALIASES))
        )

    def test_an_unresolvable_alias_set_yields_the_explicit_half_only(self):
        """`None` is not an empty set: the caller must surface it as an uncomputable portion."""
        rendered = sql(composed_predicate(ComposedScope(user=UserTerm(USER, None))))
        assert "attr_principal" not in rendered
        assert f"attr_user_id = '{USER}'" in rendered

    def test_the_project_term_is_the_project_holders_project_term(self):
        """One spelling of "this project", so no reader has to prove two equivalent."""
        rendered = sql(composed_predicate(ComposedScope(project="acme")))
        assert rendered == sql(and_(Row.attr_form == "project_scoped", Row.attr_project == "acme"))
        assert "attr_category" not in rendered

    def test_a_personal_project_term_is_its_owners_principal_pot(self):
        """UA-H-D3 (pass H): the composed twin of the report filter's rule (UD-H-D2) - a personal
        project's calls carry no project in the ledger, so its name alone answered a measured zero."""
        rendered = sql(composed_predicate(ComposedScope(project="alice@example.com", project_owners=(USER,))))

        assert rendered == sql(
            or_(
                and_(Row.attr_form == "project_scoped", Row.attr_project == "alice@example.com"),
                and_(Row.attr_user_id == USER, Row.attr_form == "principal"),
            )
        )

    def test_the_traffic_class_term_is_byte_identical_to_the_closed_tables_predicate(self):
        """A second definition of "CLI" is how two surfaces come to disagree about one window."""
        for traffic_class in TrafficClass:
            ids = ("req-1", "req-2")
            composed = composed_predicate(ComposedScope(traffic_class=traffic_class), traffic_class_ids=ids)
            assert sql(composed) == sql(traffic_class_predicate(traffic_class, ids)), traffic_class

    def test_a_workflow_term_compiles_a_column_predicate(self):
        rendered = sql(composed_predicate(ComposedScope(dimension=WORKFLOW_TERM), dimension_ids=IDS))
        assert "codemie_workflow_id = 'wf-1'" in rendered
        assert "codemie_request_id IN ('req-1', 'req-2')" in rendered

    def test_an_assistant_term_compiles_no_column_predicate_at_all(self):
        """F-1's trap. With `codemie_workflow_id IS NULL` forced on, an assistant-scoped figure
        loses every assistant invocation made inside a workflow: money, in scope, in no bucket and
        in no residue."""
        rendered = sql(composed_predicate(ComposedScope(dimension=ASSISTANT_TERM), dimension_ids=IDS))
        assert "codemie_workflow_id" not in rendered
        assert rendered == "litellm_spend_logs.codemie_request_id IN ('req-1', 'req-2')"

    def test_a_columnless_term_with_no_ids_matches_nothing_rather_than_everything(self):
        """`None` here means *this scope selects no rows*, which the read turns into a measured
        zero. Read as *unconstrained* it would hand the caller every row in the range."""
        assert composed_predicate(ComposedScope(dimension=ASSISTANT_TERM)) is None

    @pytest.mark.parametrize("dimension", [SpendDimension.PROJECT, SpendDimension.IDENTITY_BRIDGE])
    def test_a_dimension_term_that_is_not_resolved_as_a_union_is_refused(self, dimension):
        with pytest.raises(ValueError):
            composed_predicate(ComposedScope(dimension=DimensionScope(dimension, "x")))


class TestAnAbsentTermIsUnconstrained:
    """Asserted on compiled SQL rather than on a figure, because the two readings differ only in
    which rows are scanned and a stubbed figure cannot tell them apart."""

    def test_no_user_term_leaves_no_identity_conjunct(self):
        rendered = sql(composed_predicate(ComposedScope(project="acme")))
        assert "attr_user_id" not in rendered
        assert "attr_principal" not in rendered

    def test_no_project_term_leaves_no_project_conjunct(self):
        rendered = sql(composed_predicate(ComposedScope(user=USER_TERM)))
        assert "attr_project" not in rendered
        assert "project_scoped" not in rendered

    def test_no_dimension_term_leaves_no_dimension_conjunct(self):
        rendered = sql(composed_predicate(ComposedScope(user=USER_TERM), dimension_ids=IDS))
        assert "codemie_workflow_id" not in rendered
        assert "codemie_request_id" not in rendered, "ids supplied for a term that is absent select nothing"

    def test_no_traffic_class_term_leaves_no_category_conjunct(self):
        assert "attr_category" not in sql(composed_predicate(ComposedScope(user=USER_TERM)))


class TestEveryTermTogether:
    def test_the_full_scope_ands_all_four_terms(self):
        rendered = sql(composed_predicate(FULL_SCOPE, dimension_ids=("req-1",)))
        assert f"attr_user_id = '{USER}'" in rendered
        assert "attr_project = 'acme'" in rendered
        assert "codemie_workflow_id = 'wf-1'" in rendered
        assert "attr_category = 'cli'" in rendered
        assert " OR " in rendered and rendered.count(" AND ") >= 3

    def test_each_terms_disjunction_is_parenthesised_so_the_conjunction_binds_correctly(self):
        """`a OR b AND c` is not `(a OR b) AND c`, and the difference is every row of one term.

        The user term and the workflow union are both disjunctions, so an unparenthesised
        conjunction would silently widen the scope to everything either disjunct matches.
        """
        rendered = sql(composed_predicate(FULL_SCOPE, dimension_ids=("req-1",)))
        assert rendered.startswith(f"(litellm_spend_logs.attr_user_id = '{USER}' OR ")
        assert "AND (litellm_spend_logs.codemie_workflow_id IS NOT NULL" in rendered


class TestTheReportFilterIsAFilterAndNeverABoundary:
    """What a dashboard **asked for**, pinned beside the holders whose predicates it composes from.

    Its empty state is the opposite of an access scope's: a restricted boundary naming no project
    matches **nothing**, a filter naming no term matches **everything**. A single function serving
    both would have to pick one, and either choice silently answers the wrong question on one read.
    """

    def test_an_absent_filter_and_an_empty_one_are_both_the_whole_window(self):
        assert report_filter_predicate(None) is None
        assert report_filter_predicate(ReportFilter()) is None

    def test_the_user_term_is_the_person_total_holders_own_predicate(self):
        """Composed rather than re-spelled - this module's central rule. A second identity predicate
        is how one person's spend comes to mean two different row sets."""
        term = report_filter_predicate(ReportFilter(users=(UserTerm(USER, ALIASES),)))

        assert sql(term) == sql(_person_total_predicate(PersonTotalHolder(USER, ALIASES)))

    def test_it_carries_the_alias_set_the_access_boundary_can_do_without(self):
        """Inert in the boundary - its fallback half is `principal`-form, which the boundary's project
        conjunct excludes - and load-bearing here, where no project conjunct stands beside it: a filter
        on a person keyed on the canonical id alone drops every row the parser attributed by username
        or e-mail for that same human."""
        rendered = sql(report_filter_predicate(ReportFilter(users=(UserTerm(USER, ALIASES),))))

        assert f"attr_user_id = '{USER}'" in rendered
        assert "attr_principal IN (" in rendered
        assert all(f"'{alias}'" in rendered for alias in ALIASES)

    def test_the_project_term_is_the_project_scoped_match_and_carries_the_form_conjunct(self):
        rendered = sql(report_filter_predicate(ReportFilter(projects=("P", "Q"))))

        assert "attr_form = 'project_scoped'" in rendered
        assert "attr_project IN ('P', 'Q')" in rendered

    def test_a_personal_project_is_matched_as_its_owners_principal_pot(self):
        """UD-H-D2 (pass H): a personal project's calls reach the ledger in principal form with no
        project at all, while Elasticsearch carries them under the project's name - so the project
        term alone matched none of that money, and the dashboard's cards read a measured $0.00. The
        pot is the one `access_predicate` admits as the caller's own, keyed through the owner's id."""
        asked = ReportFilter(projects=("P", "alice@example.com"), personal_project_owners=(USER,))

        assert sql(report_filter_predicate(asked)) == sql(
            or_(
                and_(Row.attr_form == "project_scoped", Row.attr_project.in_(("P", "alice@example.com"))),
                and_(Row.attr_user_id == USER, Row.attr_form == "principal"),
            )
        )

    def test_a_personal_project_takes_no_alias_half(self):
        """A parsed principal-form row means *charged to the person's own customer* - before the identity
        floor that included their calls in shared projects without a budget, which are not this project's."""
        rendered = sql(report_filter_predicate(ReportFilter(projects=("P",), personal_project_owners=(USER,))))

        assert "attr_principal" not in rendered
        assert "attr_source" not in rendered

    def test_owners_nobody_could_look_up_leave_the_project_scoped_match_alone(self):
        """`None` is a failed lookup, not "no personal project": there is no owner to key a pot on, so
        the term stays what it was and the ledger labels the hole instead (`_report_filter_label`)."""
        unknown = ReportFilter(projects=("P",), personal_project_owners=None)

        assert sql(report_filter_predicate(unknown)) == sql(report_filter_predicate(ReportFilter(projects=("P",))))

    def test_users_are_ored_and_the_two_kinds_of_term_are_anded(self):
        """Three people named is a question about all three; a person *and* a project is a question
        about their spend in it."""
        rendered = sql(
            report_filter_predicate(
                ReportFilter(users=(UserTerm("u1", frozenset()), UserTerm("u2", frozenset())), projects=("P",))
            )
        )

        assert "attr_user_id = 'u1' OR" in rendered
        assert "attr_user_id = 'u2'" in rendered
        assert "attr_project IN ('P')" in rendered

    def test_an_absent_filter_leaves_a_reads_own_predicate_untouched(self):
        """`narrowed_by_filter` is shaped like `narrowed_by_access` so that a read carrying one
        narrowing and not the other is visible at the call site rather than provable from the SQL."""
        assert sql(narrowed_by_filter(within(PERIOD), None)) == sql(within(PERIOD))
        assert sql(narrowed_by_filter(within(PERIOD), ReportFilter())) == sql(within(PERIOD))

    def test_a_populated_filter_is_anded_onto_it(self):
        rendered = sql(narrowed_by_filter(within(PERIOD), ReportFilter(projects=("P",))))

        assert "start_time >=" in rendered
        assert "attr_project IN ('P')" in rendered


class TestTheResidueWhenTheMetricStoreRESOLVEDNOTHING:
    """The case the empty-id special case got wrong, and it rendered a measured $0.00 over real money.

    `dimension_residue` used to read `is_(None) if not resolved_ids else or_(...)`. When the metric
    store resolves **nothing** — an Elasticsearch read that failed and degraded to `{}`, a regressed
    emission, or one of the two dimensions whose id set is *always* empty (`datasource` has no
    metric-store attribute at all; `model` resolves wholly by `model_group`) — that narrowed the
    residue to NULL-request-id rows alone. But every row carries a correlation id since the header
    shipped, so the residue counted **zero** rows while the figure was a seeded `Decimal("0")`
    (a column-less dimension with no ids issues no query at all).

    `_dimension_label` then returned `{}`, the result was `is_exact`, and a table rendered
    `total_cost_usd: 0.0` with `spend_unavailable: false` and `spend_partial: false` — the fabricated
    exact zero AD-23/AD-33 forbid, contradicting `SpendService._metric_store_ids`' own promise that
    *"the loss is reported rather than silently subtracted from the total."*
    """

    @staticmethod
    def _residue_sql(resolved_ids: tuple[str, ...]) -> str:
        from sqlalchemy import and_, or_, select
        from sqlalchemy.dialects import postgresql

        unresolved = or_(Row.codemie_request_id.is_(None), Row.codemie_request_id.notin_(resolved_ids))
        statement = select(Row.spend).where(and_(unresolved))
        return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    def test_an_empty_id_set_makes_the_residue_every_row_not_only_the_null_ones(self):
        """Nothing was bucketed, so everything unbucketable is a hole - which is every row."""
        sql = self._residue_sql(())

        # `notin_(())` compiles to an always-true term, so the residue is unrestricted.
        assert "1 = 1" in sql
        # The bug's signature: the NULL test standing ALONE, with no always-true disjunct beside it.
        assert "NOT IN" in sql

    def test_a_populated_id_set_still_excludes_exactly_what_it_resolved(self):
        sql = self._residue_sql(("req-1",))

        assert "codemie_request_id IS NULL" in sql
        assert "NOT IN ('req-1')" in sql
        # No always-true term here - the resolved rows are genuinely not holes.
        assert "1 = 1" not in sql

    def test_the_null_disjunct_survives_in_both_forms(self):
        """Three-valued logic: `NULL NOT IN (:ids)` is *unknown*, not true, so the explicit
        `IS NULL` test is what keeps a NULL-id row in the residue rather than in no bucket at all."""
        for ids in ((), ("req-1",)):
            assert "codemie_request_id IS NULL" in self._residue_sql(ids)
