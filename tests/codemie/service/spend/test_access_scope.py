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

"""One access boundary, rendered twice - and this file is what stops the two halves drifting.

`SecureQueryBuilder` narrows every Elasticsearch analytics read at construction. When a money column
moved off that query onto the spend port, the row set stayed narrowed and the **figure stopped being**
- a plain member's own rows footed with the whole platform's spend. `AccessScope` carries the boundary
across that seam, which means the clause now exists in two implementations that must agree forever.

**Agreement is asserted as byte-identical output over a matrix of contexts, not by reading the two
side by side.** `_build_role_based_filter` is security-critical and is deliberately not touched by the
work that introduced `AccessScope`; the equivalence test is what makes that safe, because the day
either side is changed alone, this fails rather than a figure quietly widening.

The matrix is the five contexts the access filter can produce: admin, auditor, plain-projects-only,
admin-projects-only, both, and neither.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from codemie.service.analytics.access_filter import AccessFilter
from codemie.service.analytics.query_builder import PROJECT_FIELD, USER_ID_FIELD, SecureQueryBuilder
from codemie.service.spend.default_ledger import _PROJECT_FIELD, _USER_ID_FIELD
from codemie.service.spend.ledger import AccessScope, ReportFilter, UserTerm

#: Every shape the access context can take, named by what makes it different.
#:
#: `no_projects` is here because it is the case an implementation is most likely to get wrong in the
#: **widest** direction: a caller with nothing to see must see nothing, and the query builder says so
#: with `minimum_should_match=0`.
_MATRIX = [
    ("plain_only", ["proj-a", "proj-b"], []),
    ("admin_only", [], ["proj-c"]),
    ("both", ["proj-a"], ["proj-c"]),
    ("no_projects", [], []),
]


def _user(user_id: str, plain: list[str], admin: list[str], *, is_admin: bool = False, is_auditor: bool = False):
    """A `User` stand-in carrying only what the access filter reads.

    Each case gets its **own** user id because `AccessFilter` caches a context per id for five
    minutes: two cases sharing an id would have the second silently answered with the first's
    projects, and the equivalence would then be asserted against the wrong context.
    """
    user = Mock(id=user_id, project_names=plain, admin_project_names=admin)
    user.is_admin = is_admin
    user.is_auditor = is_auditor
    return user


def _builder_filter(user) -> dict | None:
    """What `SecureQueryBuilder` actually injects for this user, or `None` when it injects nothing.

    Read off the built query rather than by calling `_build_role_based_filter` directly, so the
    admin/auditor early return - the *absence* of a clause - is part of what is compared.
    """
    must = SecureQueryBuilder(user).build()["bool"]["must"]
    return must[0] if must else None


@pytest.fixture(autouse=True)
def _clear_access_context_cache():
    """The access context cache is class-level and outlives a test. Cleared on both sides so a
    neighbouring test's user id cannot answer this one's question."""
    AccessFilter._context_cache.clear()
    yield
    AccessFilter._context_cache.clear()


class TestTheTwoRenderingsOfOneBoundaryAreIdentical:
    """The drift guard. Byte-identical dicts, not merely equivalent queries."""

    @pytest.mark.parametrize("case,plain,admin", _MATRIX)
    def test_a_restricted_context_renders_the_same_clause_on_both_sides(self, case, plain, admin) -> None:
        user_id = f"user-{case}"
        scope = AccessScope.for_context(user_id=user_id, plain_projects=plain, admin_projects=admin, is_admin=False)

        assert scope.elasticsearch_filter(user_id_field=USER_ID_FIELD, project_field=PROJECT_FIELD) == _builder_filter(
            _user(user_id, plain, admin)
        )

    @pytest.mark.parametrize("role", ["is_admin", "is_auditor"])
    def test_an_unrestricted_caller_is_narrowed_by_nothing_on_both_sides(self, role) -> None:
        """An unrestricted scope renders **no clause at all**, which is the query builder's own early
        return - not an all-matching clause, which would read as a boundary that happens to admit
        everything and could be tightened by mistake."""
        user_id = f"user-{role}"
        user = _user(user_id, ["proj-a"], ["proj-c"], **{role: True})
        # The access filter reports both roles as `is_admin`, which is the vocabulary `for_context`
        # takes; the two roles differ nowhere below this line.
        context = AccessFilter(user).get_project_access_context()
        scope = AccessScope.for_context(
            user_id=context.user_id,
            plain_projects=context.plain_user_projects,
            admin_projects=context.admin_projects,
            is_admin=context.is_admin,
        )

        assert scope.elasticsearch_filter(user_id_field=USER_ID_FIELD, project_field=PROJECT_FIELD) is None
        assert _builder_filter(user) is None

    def test_the_scope_is_built_from_the_very_context_the_query_builder_reads(self) -> None:
        """The two halves must not merely produce the same shape - they must be fed the same context,
        which is why the pipeline derives the scope from `get_project_access_context` and no handler
        assembles one from a `User` for itself."""
        user = _user("user-shared-context", ["proj-a"], ["proj-c"])
        context = AccessFilter(user).get_project_access_context()
        scope = AccessScope.for_context(
            user_id=context.user_id,
            plain_projects=context.plain_user_projects,
            admin_projects=context.admin_projects,
            is_admin=context.is_admin,
        )

        assert scope.elasticsearch_filter(user_id_field=USER_ID_FIELD, project_field=PROJECT_FIELD) == _builder_filter(
            user
        )


class TestTheFieldNamesTheTwoHalvesUseAgree:
    """The equivalence above compares clause *shape*; a field name is the other half of the same
    guarantee, and the ledger spells its two from `MetricsAttributes` rather than importing the
    analytics layer (which would invert the dependency between a consumer and the port it reads
    through). These assertions are what keep the two spellings one boundary."""

    def test_the_ledger_and_the_query_builder_name_the_same_user_field(self) -> None:
        assert _USER_ID_FIELD == USER_ID_FIELD

    def test_the_ledger_and_the_query_builder_name_the_same_project_field(self) -> None:
        assert _PROJECT_FIELD == PROJECT_FIELD


class TestAnEmptyScopeMatchesNothing:
    """The trap this whole design keeps returning to: an empty collection read as *unconstrained*."""

    def test_a_restricted_caller_with_no_projects_matches_nothing_rather_than_everything(self) -> None:
        scope = AccessScope.for_context(user_id="user-x", plain_projects=[], admin_projects=[], is_admin=False)
        rendered = scope.elasticsearch_filter(user_id_field=USER_ID_FIELD, project_field=PROJECT_FIELD)

        assert rendered is not None, "None means narrow nothing, which is the widest possible answer"
        assert rendered["bool"]["should"] == []
        # Zero results by design, and this is the query builder's own answer for the same caller.
        assert rendered["bool"]["minimum_should_match"] == 0

    def test_an_unrestricted_scope_cannot_also_carry_a_narrowing_term(self) -> None:
        """A scope holding both readings has two, and the widest one wins by accident."""
        with pytest.raises(ValueError):
            AccessScope(unrestricted=True, user_id="user-x")

    def test_a_restricted_scope_without_the_callers_id_is_refused(self) -> None:
        """Rendering the admin half alone would show a plain member every other member's rows."""
        with pytest.raises(ValueError):
            AccessScope(unrestricted=False, plain_projects=("proj-a",))

    def test_an_admin_context_becomes_the_unrestricted_scope_and_not_an_empty_one(self) -> None:
        assert (
            AccessScope.for_context(user_id="user-admin", plain_projects=[], admin_projects=[], is_admin=True)
            == AccessScope.everything()
        )


class TestThePipelineIsTheOneDerivationPoint:
    """A scope derived twice is a boundary that can disagree with itself.

    `AnalyticsQueryPipeline` already narrows every query it builds through `SecureQueryBuilder`, so the
    figure's boundary must come from the very same access context - which is why the derivation lives
    on the pipeline and a handler is not allowed one of its own.
    """

    @staticmethod
    def _pipeline(user):
        from unittest.mock import MagicMock

        from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
        from codemie.service.analytics.query_pipeline import AnalyticsQueryPipeline

        return AnalyticsQueryPipeline(user, MagicMock(spec=MetricsElasticRepository))

    def test_a_plain_members_scope_is_the_clause_their_own_query_carries(self) -> None:
        user = _user("user-pipeline-plain", ["proj-a"], ["proj-c"])
        scope = self._pipeline(user).access_scope

        assert scope.elasticsearch_filter(user_id_field=USER_ID_FIELD, project_field=PROJECT_FIELD) == _builder_filter(
            user
        )

    @pytest.mark.parametrize("role", ["is_admin", "is_auditor"])
    def test_an_admin_or_auditor_becomes_the_unrestricted_scope(self, role) -> None:
        user = _user(f"user-pipeline-{role}", ["proj-a"], [], **{role: True})
        assert self._pipeline(user).access_scope == AccessScope.everything()

    def test_a_member_of_no_project_is_restricted_rather_than_unrestricted(self) -> None:
        """The whole disclosure in one assertion: this caller must be narrowed to nothing, and an
        `AccessScope.everything()` here would be the leak with the boundary threaded."""
        scope = self._pipeline(_user("user-pipeline-empty", [], [])).access_scope

        assert scope.unrestricted is False
        assert scope.plain_projects == () and scope.admin_projects == ()

    @pytest.mark.parametrize("module", ["assistant_handler", "workflow_handler"])
    def test_no_consumer_derives_a_scope_of_its_own(self, module) -> None:
        """Pinned as source rather than left to review: a second `AccessFilter` call in a handler is
        exactly how one boundary becomes two that can drift."""
        import importlib
        import inspect as inspect_module

        handler_module = importlib.import_module(f"codemie.service.analytics.handlers.{module}")
        source = inspect_module.getsource(handler_module)

        assert not hasattr(handler_module, "AccessFilter"), "the access filter is not a handler's to import"
        assert "AccessFilter(" not in source
        assert "get_project_access_context" not in source
        assert "access_scope" in source, "the handler must go through the pipeline's single derivation"


class TestAReportFilterIsNotABoundary:
    """The two travel together on every whole-window read, and their empty states mean opposites.

    A boundary says what the caller **may** see; a filter says what they **asked for**. An empty
    restricted boundary matches nothing - `minimum_should_match=0`, the query builder's own answer for
    a caller with no accessible project - and an empty filter matches everything, which is what "no
    filter was applied" has to mean. Merging them would force one reading onto both, and whichever way
    it went would be wrong on one of the two reads: a widened boundary discloses, a narrowed filter
    empties a dashboard.

    They are asserted side by side here for that reason: the pair of assertions in
    `test_the_two_empty_states_are_opposites` is the whole distinction, in one place.
    """

    ALICE = UserTerm(user_id="user-alice", aliases=frozenset({"alice@example.com", "alice"}))
    BOB = UserTerm(user_id="user-bob", aliases=frozenset())

    def _rendered(self, report_filter: ReportFilter) -> list[dict]:
        return report_filter.elasticsearch_filters(user_id_field=USER_ID_FIELD, project_field=PROJECT_FIELD)

    def test_a_filter_with_no_term_is_empty(self) -> None:
        assert ReportFilter().is_empty

    @pytest.mark.parametrize(
        "report_filter",
        [ReportFilter(users=(ALICE,)), ReportFilter(projects=("proj-a",)), ReportFilter((ALICE,), ("proj-a",))],
        ids=["users", "projects", "both"],
    )
    def test_a_filter_carrying_any_term_is_not_empty(self, report_filter) -> None:
        assert not report_filter.is_empty

    def test_an_empty_filter_renders_no_clause_at_all(self) -> None:
        """Nothing was asked, so nothing narrows - the opposite of the empty boundary below."""
        assert self._rendered(ReportFilter()) == []

    def test_the_users_render_as_one_terms_clause_over_their_ids(self) -> None:
        assert self._rendered(ReportFilter(users=(self.ALICE, self.BOB))) == [
            {"terms": {USER_ID_FIELD: ["user-alice", "user-bob"]}}
        ]

    def test_the_aliases_are_not_offered_to_the_metric_store(self) -> None:
        """A person is two identifiers on the **SQL** side, where the fallback parser attributed rows
        by username or by email. The metric store has only ever matched the canonical id, so putting an
        alias in this clause would widen a filtered dashboard to rows its own query never returned."""
        rendered = repr(self._rendered(ReportFilter(users=(self.ALICE,))))
        assert "alice@example.com" not in rendered
        assert "user-alice" in rendered

    def test_the_projects_render_as_their_own_terms_clause(self) -> None:
        """Two clauses in a `bool.filter` list, which is an AND - the dashboard asked for both."""
        assert self._rendered(ReportFilter(users=(self.ALICE,), projects=("proj-a", "proj-b"))) == [
            {"terms": {USER_ID_FIELD: ["user-alice"]}},
            {"terms": {PROJECT_FIELD: ["proj-a", "proj-b"]}},
        ]

    def test_the_two_empty_states_are_opposites(self) -> None:
        """The one assertion the whole separation exists for, stated as the pair it is."""
        boundary = AccessScope(unrestricted=False, user_id="user-alice")
        rendered = boundary.elasticsearch_filter(user_id_field=USER_ID_FIELD, project_field=PROJECT_FIELD)

        assert rendered["bool"]["minimum_should_match"] == 0, "an empty boundary matches nothing"
        assert self._rendered(ReportFilter()) == [], "an empty filter matches everything"

    def test_a_filter_cannot_be_rendered_as_a_boundary(self) -> None:
        """A filter handed where a boundary belongs must not render at all, because the two mean
        opposite things when empty - which is the separation `test_the_two_empty_states_are_opposites`
        states, and this only makes unreachable by a mistyped argument."""
        assert not hasattr(ReportFilter(), "elasticsearch_filter")
