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

"""The two closed selector tables, compiled - because every way of getting one wrong is silent.

Nothing here touches a repository or a session. A predicate is a value, and the defects it can carry
are visible in its rendered SQL: `<> 'cli'` in place of `IS DISTINCT FROM`, an embedding selector that
lost the async call type, a card cut by a predicate the chart beside it does not share. All three
return a plausible figure and none of them raises, so each is asserted against the rendered text.

**The card and the chart must be selected by the same clause, so the assertions compare texts rather
than restate them.** Re-spelling `attr_category = 'cli'` here is how the summary card and the split
chart come to mean two different things by "CLI"; comparing `spend_group_predicate(CLI)` against
`traffic_class_predicate(TrafficClass.CLI)` is what makes a divergence fail instead of merely differ.

The vocabulary tables are pinned the same way: `ORIGINS_BY_CLASS` files each origin exactly once
(`PROXY` deliberately nowhere, so a non-CLI proxied call lands in the residue), and `UNION_CLASSES`
holds the one class the ledger still cannot bucket by itself - the datasource class was moved onto
`call_type` and an entry left behind for it would send every embedding read through an id set that
resolves nothing.
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects import postgresql

from codemie.service.spend.coverage import SpendDimension
from codemie.service.spend.spend_group import SpendGroup, spend_group_predicate
from codemie.service.spend.traffic_class import (
    EMBEDDING_CALL_TYPES,
    ORIGINS_BY_CLASS,
    UNION_CLASSES,
    TrafficClass,
    TrafficOrigin,
    cli_complement,
    traffic_class_predicate,
)


def sql(clause) -> str:
    return str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).replace("\n", " ")


class TestTheTotalSelectsEveryRow:
    def test_the_total_has_no_predicate_at_all(self) -> None:
        """`None` rather than a true-for-everything clause, which a later edit could tighten by mistake."""
        assert spend_group_predicate(SpendGroup.TOTAL) is None


class TestThePlatformGroupAdmitsTheUnattributedRow:
    """`NULL <> 'cli'` is NULL and a `WHERE` never admits it, so the wrong spelling drops real money.

    An unattributed row carries no category. Selected with `<> 'cli'` the platform card would omit
    exactly the rows the total still counts, and the two cards on one screen would stop footing - by an
    amount nobody could see, because both figures are individually plausible.
    """

    def test_it_is_rendered_as_is_distinct_from(self) -> None:
        assert "attr_category IS DISTINCT FROM 'cli'" in sql(spend_group_predicate(SpendGroup.PLATFORM))

    def test_it_is_not_a_plain_inequality(self) -> None:
        assert "attr_category !=" not in sql(spend_group_predicate(SpendGroup.PLATFORM))

    def test_it_is_the_cli_selectors_own_complement_and_never_a_second_opinion(self) -> None:
        """`TOTAL = PLATFORM + CLI` holds only while every row is on exactly one side, so the platform
        card is the CLI class's complement rather than a category test written again beside it."""
        assert sql(spend_group_predicate(SpendGroup.PLATFORM)) == sql(cli_complement())

    def test_a_cli_origin_row_is_excluded_whatever_budget_paid_for_it(self) -> None:
        """The row the origin column was added for: a CLI session billed to a premium budget carries
        `codemie_origin = 'cli'` beside `attr_category = 'premium_models'`. Keyed on the category alone
        the platform card and the CLI card would both count it."""
        cli_origins = {origin.value for origin in ORIGINS_BY_CLASS[TrafficClass.CLI]}
        rendered = sql(spend_group_predicate(SpendGroup.PLATFORM))

        assert f"codemie_origin NOT IN ({', '.join(sorted(repr(value) for value in cli_origins))})" in rendered

    def test_it_admits_the_null_of_either_column_that_the_other_conjunct_would_drop(self) -> None:
        """Two columns to be NULL-safe on now: a pre-rollout row carries no origin and an unattributed
        row carries no category, and `NULL <> 'cli'` is *unknown* on either."""
        rendered = sql(spend_group_predicate(SpendGroup.PLATFORM))

        assert "codemie_origin IS NULL OR" in rendered
        assert "codemie_origin IS NOT NULL OR" in rendered
        assert "codemie_origin !=" not in rendered, "an inequality drops every row with no origin"


class TestACardIsCutByTheSameClauseAsTheChartBesideIt:
    """The card and the series must be one selector, or "CLI" means two things on one screen."""

    def test_the_cli_card_is_the_cli_traffic_class(self) -> None:
        assert sql(spend_group_predicate(SpendGroup.CLI)) == sql(traffic_class_predicate(TrafficClass.CLI))

    def test_the_embedding_card_is_the_datasource_traffic_class(self) -> None:
        assert sql(spend_group_predicate(SpendGroup.EMBEDDING)) == sql(traffic_class_predicate(TrafficClass.DATASOURCE))

    def test_the_embedding_card_matches_both_call_types(self) -> None:
        """The sync spelling is defensive and the async one is what every sampled row carries; losing
        either narrows the card to a slice of the embeddings and nothing says so."""
        rendered = sql(spend_group_predicate(SpendGroup.EMBEDDING))
        assert "call_type IN" in rendered
        for call_type in EMBEDDING_CALL_TYPES:
            assert f"'{call_type}'" in rendered


class TestTheGroupTableIsClosed:
    def test_the_four_groups_are_exactly_these_in_this_order(self) -> None:
        """A fifth group would need a predicate, which is the point of the table; the order is the
        summary screen's own - the total first, then the three cards cut from it."""
        assert [group.value for group in SpendGroup] == ["total", "platform", "cli", "embedding"]


class TestTheOriginVocabularyFilesEachOriginOnce:
    """The table every selector's origin branch is built from.

    An origin filed under two classes would double a row's money across two series; one filed under
    none would drop it into the residue with nothing naming why. `PROXY` is the single deliberate
    absence - a proxied call that is not CLI is claimed by no class today either.
    """

    @pytest.mark.parametrize("origin", [origin for origin in TrafficOrigin if origin is not TrafficOrigin.PROXY])
    def test_every_origin_but_proxy_belongs_to_exactly_one_class(self, origin) -> None:
        claiming = [traffic_class for traffic_class, origins in ORIGINS_BY_CLASS.items() if origin in origins]
        assert len(claiming) == 1, f"{origin.value} is claimed by {[cls.value for cls in claiming]}"

    def test_a_non_cli_proxied_call_is_claimed_by_no_class(self) -> None:
        assert not [origins for origins in ORIGINS_BY_CLASS.values() if TrafficOrigin.PROXY in origins]

    def test_the_table_files_origins_under_the_four_classes_and_no_other_key(self) -> None:
        assert set(ORIGINS_BY_CLASS) == set(TrafficClass)


class TestOnlyTheAssistantClassStillNeedsTheMetricStore:
    """The datasource class became native on `call_type`; an entry left behind for it would send an
    embedding read through a correlation-id set no datasource document has ever carried - empty in
    every window, and a permanent measured zero over real money."""

    def test_the_union_table_holds_the_assistant_class_alone(self) -> None:
        assert UNION_CLASSES == {TrafficClass.ASSISTANT: SpendDimension.ASSISTANT}

    def test_the_embedding_call_types_are_the_two_litellm_spells_them(self) -> None:
        assert EMBEDDING_CALL_TYPES == ("embedding", "aembedding")
