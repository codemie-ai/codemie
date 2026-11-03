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

"""Release-blocking: the fallback parser must agree with the two live composers.

Agreement is *proven* here, not assumed, and the composers are imported rather than
reimplemented - a test that restates the composer proves only that the test agrees with itself.

The round-trip is claimed over domain **I** and the two collision vectors are carried as named
**expected failures**, so the unqualified claim cannot be quietly re-asserted later.
"""

from __future__ import annotations

import itertools
import uuid

import pytest

from codemie.enterprise.litellm.budget_categories import BudgetCategory as EnterpriseBudgetCategory
from codemie.enterprise.litellm.budget_categories import build_user_id
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.spend.attribution import (
    PROJECT_PREFIX,
    RESERVED_SUFFIXES,
    UNATTRIBUTED_SENTINEL,
    AttributionForm,
    is_in_domain_i,
    parse_end_user,
    safe_principal,
)

# Sampled production shapes: an email, a second email domain, and a service account -
# the sample holds `service-account-api-scor-gits`, so an email must not be assumed.
DOMAIN_I_PRINCIPALS = [
    "andrei_malashkou@epam.com",
    "cristian_machado@epamneoris.com",
    "service-account-api-scor-gits",
    "alice",
]


def project_customer_id(project_name: str, budget_category: str, user_id: str) -> str:
    """`_project_customer_id`, mirrored because it lives in the enterprise package.

    Kept byte-identical to the composer at `codemie_enterprise/litellm/service.py`; the test
    below asserts the mirror still matches that source.
    """
    return f"{PROJECT_PREFIX}{project_name}:category:{budget_category}:user:{user_id}"


class TestTheEnumsAndComposersHaveNotDrifted:
    def test_core_and_enterprise_budget_category_agree(self):
        """The parser validates against the core enum; the composer uses the enterprise one."""
        assert {m.name: m.value for m in BudgetCategory} == {m.name: m.value for m in EnterpriseBudgetCategory}

    def test_reserved_suffixes_are_exactly_what_the_composer_appends(self):
        appended = {
            build_user_id("x", category).removeprefix("x")
            for category in EnterpriseBudgetCategory
            if category is not EnterpriseBudgetCategory.PLATFORM
        }
        assert appended == set(RESERVED_SUFFIXES)

    def test_platform_appends_nothing(self):
        assert build_user_id("alice", EnterpriseBudgetCategory.PLATFORM) == "alice"

    def test_the_mirrored_project_composer_matches_its_source(self):
        """Guards the mirror above against the composer drifting underneath it.

        `_project_customer_id` is a private staticmethod on `LiteLLMService`
        (`codemie_enterprise/litellm/service.py:440`), so it is reached through the class.
        """
        from codemie_enterprise.litellm.service import LiteLLMService

        user_id = str(uuid.uuid4())
        for project, category in (("epm-awb2", "platform"), ("weird:category:name", "cli")):
            assert LiteLLMService._project_customer_id(project, category, user_id) == project_customer_id(
                project, category, user_id
            )


class TestGrammarBRoundTripsOverDomainI:
    @pytest.mark.parametrize(
        ("principal", "category"),
        list(itertools.product(DOMAIN_I_PRINCIPALS, list(EnterpriseBudgetCategory))),
    )
    def test_parse_of_build_user_id_returns_the_inputs(self, principal, category):
        assert is_in_domain_i(principal), "test vector must lie inside domain I"

        parsed = parse_end_user(build_user_id(principal, category))

        assert parsed.form is AttributionForm.PRINCIPAL
        assert parsed.principal == principal
        assert parsed.category == BudgetCategory(category.value)
        assert parsed.project is None, "Form B carries no project"

    def test_a_missing_suffix_is_platform_and_not_a_failure(self):
        """`platform` is precisely what `build_user_id` encoded for a suffix-less value.

        This is the one place the design's own "do not inherit the PLATFORM fallback" rule is
        most likely to be over-applied: the forbidden fallback assigns a category to a value
        that *failed* to parse, not to one that parsed cleanly and carried no suffix.
        """
        parsed = parse_end_user("alice@example.com")
        assert parsed.form is AttributionForm.PRINCIPAL
        assert parsed.category is BudgetCategory.PLATFORM
        assert parsed.channel is None

    def test_exactly_one_suffix_is_stripped_never_repeatedly(self):
        """`build_user_id` appends exactly one, so the parser strips exactly one."""
        parsed = parse_end_user("x_codemie_cli_codemie_cli")
        assert parsed.principal == "x_codemie_cli"
        assert parsed.category is BudgetCategory.CLI

    def test_the_channel_is_recorded_for_a_suffixed_value(self):
        assert parse_end_user("alice_codemie_cli").channel == "codemie_cli"
        assert parse_end_user("alice_codemie_premium_models").channel == "codemie_premium_models"

    def test_a_service_account_carrying_a_channel_suffix_parses(self):
        """The one sampled value that matched neither grammar cleanly."""
        parsed = parse_end_user("service-account-api-scor-gits_codemie_cli")
        assert parsed.principal == "service-account-api-scor-gits"
        assert parsed.category is BudgetCategory.CLI


class TestGrammarBCollisionVectorsAreExpectedFailures:
    """`build_user_id` is **not** injective, and these are the inputs that prove it.

    Carried as named expected failures so the unqualified round-trip claim cannot be
    re-asserted. Both lie **outside** domain I, which is exactly why the domain exists.
    """

    def test_the_suffix_collision_is_real(self):
        collision = build_user_id("robot_codemie_cli", EnterpriseBudgetCategory.PLATFORM)
        assert collision == build_user_id("robot", EnterpriseBudgetCategory.CLI)
        assert not is_in_domain_i("robot_codemie_cli")

        parsed = parse_end_user(collision)
        # The parser resolves it the one legal way; the other input therefore cannot round-trip.
        assert (parsed.principal, parsed.category) == ("robot", BudgetCategory.CLI)

    def test_the_suffix_collision_costs_its_owner_a_mis_set_category(self):
        parsed = parse_end_user(build_user_id("robot_codemie_cli", EnterpriseBudgetCategory.PLATFORM))
        assert parsed.category is not BudgetCategory.PLATFORM

    def test_the_prefix_collision_books_spend_against_another_project(self):
        """The sharper half of domain I, and it is not symmetric with the suffix half.

        A username beginning `codemie:project:` is composed verbatim on the platform path, so
        the emitted value is byte-identical to a genuine project-scoped one and parses as one.
        """
        victim = "acme-victim"
        injected = f"{PROJECT_PREFIX}{victim}:category:platform:user:{uuid.uuid4()}"
        assert build_user_id(injected, EnterpriseBudgetCategory.PLATFORM) == injected
        assert not is_in_domain_i(injected)

        parsed = parse_end_user(injected)
        assert parsed.form is AttributionForm.PROJECT_SCOPED
        assert parsed.project == victim, "this is attribution corruption, not a mislabel"

    def test_an_email_cannot_reach_the_suffix_collision(self):
        """The suffix would have to trail the domain, so the risk surface is bot principals."""
        assert is_in_domain_i("alice_codemie_cli@example.com")


class TestGrammarARoundTripsAndIsRightAnchored:
    @pytest.mark.parametrize("project", ["epm-awb2", "amit-wloy", "epm-usps", "epm-bops"])
    @pytest.mark.parametrize("category", list(EnterpriseBudgetCategory))
    def test_parse_of_project_customer_id_returns_the_inputs(self, project, category):
        user_id = str(uuid.uuid4())

        parsed = parse_end_user(project_customer_id(project, category.value, user_id))

        assert parsed.form is AttributionForm.PROJECT_SCOPED
        assert parsed.project == project
        assert parsed.category == BudgetCategory(category.value)
        assert parsed.user_id == user_id
        assert parsed.principal is None

    @pytest.mark.parametrize(
        "project",
        [
            "weird:category:name",
            "weird:user:name",
            "a:category:b:user:c",
            "trailing:",
            ":leading",
        ],
    )
    def test_a_project_name_containing_the_separators_survives_verbatim(self, project):
        """Right-anchored parsing is an exact inverse for *every* project identifier.

        Left-anchored non-greedy matching silently mis-splits exactly these.
        """
        user_id = str(uuid.uuid4())
        parsed = parse_end_user(project_customer_id(project, "platform", user_id))
        assert parsed.project == project
        assert parsed.user_id == user_id

    def test_a_category_outside_the_closed_set_rejects_the_whole_value(self):
        """Never a guess, and the project field is never read."""
        parsed = parse_end_user(project_customer_id("epm-awb2", "not_a_category", str(uuid.uuid4())))
        assert parsed.form is AttributionForm.UNATTRIBUTED
        assert parsed.project is None
        assert parsed.category is None

    def test_a_user_field_that_is_not_a_uuid_rejects_the_whole_value(self):
        parsed = parse_end_user(f"{PROJECT_PREFIX}epm-awb2:category:platform:user:not-a-uuid")
        assert parsed.form is AttributionForm.UNATTRIBUTED
        assert parsed.category is None

    def test_an_empty_project_rejects_the_value(self):
        parsed = parse_end_user(f"{PROJECT_PREFIX}:category:platform:user:{uuid.uuid4()}")
        assert parsed.form is AttributionForm.UNATTRIBUTED

    def test_a_project_prefixed_value_is_never_re_read_as_a_principal(self):
        """A failed Grammar A parse does not fall through to Grammar B.

        Falling through would assign the whole raw string as a principal and hand a parse
        failure a category - the silent misattribution this parser refuses.
        """
        parsed = parse_end_user(f"{PROJECT_PREFIX}epm-awb2:category:bogus:user:bogus")
        assert parsed.form is AttributionForm.UNATTRIBUTED
        assert parsed.principal is None


class TestUnparseableValuesAreSurfacedNeverAbsorbed:
    @pytest.mark.parametrize("value", [None, ""])
    def test_an_absent_value_is_unattributed_with_no_category(self, value):
        parsed = parse_end_user(value)
        assert parsed.form is AttributionForm.UNATTRIBUTED
        assert parsed.category is None
        assert not parsed.is_attributed

    def test_a_parse_failure_never_yields_a_category(self):
        """`derive_category_from_user_id` returns PLATFORM here; this parser must not."""
        from codemie.enterprise.litellm.budget_categories import derive_category_from_user_id

        garbage = f"{PROJECT_PREFIX}p:category:nope:user:nope"
        assert derive_category_from_user_id(garbage) is EnterpriseBudgetCategory.PLATFORM
        assert parse_end_user(garbage).category is None


class TestThePreCompositionGuard:
    @pytest.mark.parametrize("principal", DOMAIN_I_PRINCIPALS)
    def test_a_principal_inside_the_domain_passes_through_unchanged(self, principal):
        assert safe_principal(principal) == principal

    @pytest.mark.parametrize(
        "principal",
        [
            "robot_codemie_cli",
            "robot_codemie_premium_models",
            # A fixed id: a parametrized value is the test id, and xdist workers must collect the same ids.
            f"{PROJECT_PREFIX}acme-victim:category:platform:user:2aaeabce-a561-4da3-952f-3a57c790ac87",
            f"{PROJECT_PREFIX}anything",
        ],
    )
    def test_a_principal_outside_the_domain_is_replaced_by_the_sentinel(self, principal):
        assert safe_principal(principal) == UNATTRIBUTED_SENTINEL

    def test_the_guard_does_not_strip_before_testing(self):
        """The strip belongs to the parser. Borrowing it here readmits the collision.

        `robot_codemie_cli` strips to `robot`, which ends in no reserved suffix and would pass -
        yet the two compositions still collide.
        """
        assert is_in_domain_i("robot_codemie_cli") is False

    def test_the_guard_covers_the_prefix_half_and_not_only_the_suffix_half(self):
        assert is_in_domain_i(f"{PROJECT_PREFIX}acme-victim") is False

    def test_the_sentinel_is_guaranteed_to_parse_as_unattributed(self):
        """Not a convention - a mechanism.

        A bare stand-in would parse as an ordinary principal under Grammar B, landing
        `attr_form='principal'` with an unresolvable owner. The sentinel routes to Grammar A
        and is rejected there as a whole.
        """
        parsed = parse_end_user(UNATTRIBUTED_SENTINEL)
        assert parsed.form is AttributionForm.UNATTRIBUTED
        assert parsed.project is None
        assert parsed.category is None
        assert parsed.principal is None

    def test_the_sentinel_itself_lies_outside_domain_i_so_it_is_never_re_composed(self):
        assert is_in_domain_i(UNATTRIBUTED_SENTINEL) is False
        assert safe_principal(UNATTRIBUTED_SENTINEL) == UNATTRIBUTED_SENTINEL
