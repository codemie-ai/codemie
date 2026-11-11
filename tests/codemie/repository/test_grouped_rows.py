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

"""Assigning grouped rows to a filter by evaluating the filter's own predicate tree, set-wise.

A node the evaluator does not understand must fail the read rather than be guessed at, and its cost must
follow the groups a filter matches, not rows x groups. The agreement with SQL over real rows is pinned
in `test_spend_logs_access_boundary.py`; these tests pin the evaluator's own contract.
"""

from __future__ import annotations

import pytest
from sqlalchemy import and_, bindparam, false, not_, or_, true

from codemie.repository.spend_logs_model import LiteLLMSpendLog as Row
from codemie.repository.grouped_rows import GroupedRows, UnmatchablePredicate, grouping_columns

KEY = ("attr_user_id", "attr_project")

ROWS = [
    ("alice", "A", 1),
    ("alice", "B", 2),
    ("bob", "A", 3),
    ("bob", None, 4),
    (None, "B", 5),
]


@pytest.fixture
def grouped() -> GroupedRows:
    return GroupedRows(ROWS, KEY)


class TestGroupingColumns:
    def test_returns_the_named_columns_in_projected_order(self) -> None:
        predicates = [Row.attr_project == "A", None, and_(Row.attr_user_id == "alice", Row.attr_source == "parsed")]

        assert grouping_columns(predicates) == ("attr_source", "attr_user_id", "attr_project")

    def test_names_each_column_once_across_trees(self) -> None:
        predicates = [Row.attr_user_id == "alice", Row.attr_user_id.in_(("bob", "carol"))]

        assert grouping_columns(predicates) == ("attr_user_id",)

    def test_names_nothing_for_no_trees(self) -> None:
        assert grouping_columns([None, true()]) == ()

    def test_refuses_a_column_outside_the_projected_dimensions(self) -> None:
        with pytest.raises(UnmatchablePredicate):
            grouping_columns([Row.codemie_request_id == "r1"])


class TestMatching:
    def test_equality_selects_the_rows_holding_the_value(self, grouped: GroupedRows) -> None:
        assert grouped.matching(Row.attr_user_id == "alice") == {0, 1}

    def test_in_selects_the_union_of_its_values(self, grouped: GroupedRows) -> None:
        assert grouped.matching(Row.attr_project.in_(("A", "B"))) == {0, 1, 2, 4}

    def test_and_selects_the_rows_every_operand_selects(self, grouped: GroupedRows) -> None:
        assert grouped.matching(and_(Row.attr_user_id == "alice", Row.attr_project == "A")) == {0}

    def test_or_selects_the_rows_any_operand_selects(self, grouped: GroupedRows) -> None:
        assert grouped.matching(or_(Row.attr_user_id == "alice", Row.attr_project == "A")) == {0, 1, 2}

    def test_an_or_inside_an_and_is_evaluated_through_its_grouping(self, grouped: GroupedRows) -> None:
        predicate = and_(Row.attr_user_id == "bob", or_(Row.attr_project == "A", Row.attr_project == "B"))

        assert grouped.matching(predicate) == {2}

    def test_no_predicate_selects_every_row(self, grouped: GroupedRows) -> None:
        assert grouped.matching(None) == {0, 1, 2, 3, 4}

    def test_true_selects_every_row(self, grouped: GroupedRows) -> None:
        assert grouped.matching(true()) == {0, 1, 2, 3, 4}

    def test_false_selects_no_row(self, grouped: GroupedRows) -> None:
        assert grouped.matching(false()) == frozenset()

    def test_a_value_absent_from_the_rows_selects_nothing(self, grouped: GroupedRows) -> None:
        assert grouped.matching(Row.attr_user_id == "nobody") == frozenset()

    @pytest.mark.parametrize(
        ("predicate", "expected"),
        [
            pytest.param(Row.attr_user_id == bindparam("user", None), frozenset(), id="equality"),
            pytest.param(Row.attr_user_id.in_(bindparam("users", ["bob", None], expanding=True)), {2, 3}, id="in"),
        ],
    )
    def test_a_null_bound_value_matches_no_null_key_value(self, grouped: GroupedRows, predicate, expected) -> None:
        assert grouped.matching(predicate) == expected

    def test_an_empty_grouping_selects_nothing(self) -> None:
        assert GroupedRows([], KEY).matching(Row.attr_user_id == "alice") == frozenset()

    def test_key_values_are_read_from_the_leading_positions_only(self) -> None:
        rows = GroupedRows([("alice", "A", "alice"), ("bob", "A", "alice")], KEY)

        assert rows.matching(Row.attr_user_id == "alice") == {0}


class TestUnsupportedNodes:
    @pytest.mark.parametrize(
        "predicate",
        [
            pytest.param(Row.attr_user_id.not_in(("alice",)), id="not-in"),
            pytest.param(not_(Row.attr_user_id == "alice"), id="not"),
            pytest.param(Row.attr_user_id.is_(None), id="is-null"),
            pytest.param(Row.attr_user_id != "alice", id="not-equal"),
            pytest.param(Row.attr_user_id.startswith("al"), id="startswith"),
            pytest.param(Row.attr_source == "parsed", id="column-outside-the-key"),
            pytest.param(and_(Row.attr_user_id == "alice", Row.attr_source == "parsed"), id="outside-column-in-and"),
        ],
    )
    def test_raises_rather_than_guessing(self, grouped: GroupedRows, predicate) -> None:
        with pytest.raises(UnmatchablePredicate):
            grouped.matching(predicate)


class TestMemoisation:
    def test_a_repeated_term_costs_nothing_the_second_time(self, grouped: GroupedRows) -> None:
        grouped.matching(Row.attr_project.in_(("A", "B")))
        visited = grouped.visits

        again = grouped.matching(Row.attr_project.in_(("B", "A")))

        assert again == {0, 1, 2, 4}
        assert grouped.visits == visited

    def test_a_term_shared_by_two_trees_is_looked_up_once(self, grouped: GroupedRows) -> None:
        grouped.matching(and_(Row.attr_user_id == "alice", Row.attr_project.in_(("A", "B"))))
        visited = grouped.visits

        grouped.matching(and_(Row.attr_user_id == "bob", Row.attr_project.in_(("A", "B"))))

        assert grouped.visits - visited == 4

    def test_an_equality_and_a_one_value_in_are_the_same_term(self, grouped: GroupedRows) -> None:
        grouped.matching(Row.attr_user_id == "alice")
        visited = grouped.visits

        grouped.matching(Row.attr_user_id.in_(("alice",)))

        assert grouped.visits == visited


class TestCostFollowsTheMatches:
    GROUPS = 2_000

    def test_filters_sharing_one_term_cost_their_matches_not_rows_times_groups(self) -> None:
        projects = tuple(f"p{index}" for index in range(self.GROUPS))
        rows = GroupedRows([(f"u{index}", f"p{index}", 1) for index in range(self.GROUPS)], KEY)

        matches = [
            rows.matching(and_(Row.attr_user_id == f"u{index}", Row.attr_project.in_(projects)))
            for index in range(self.GROUPS)
        ]

        assert [set(match) for match in matches] == [{index} for index in range(self.GROUPS)]
        assert rows.visits <= 14_000

    def test_an_and_tests_its_broad_operand_only_on_what_its_selective_operand_matched(self) -> None:
        rows = GroupedRows([(f"u{index}", "shared", 1) for index in range(self.GROUPS)], KEY)

        matches = [
            rows.matching(and_(Row.attr_user_id == f"u{index}", Row.attr_project.in_(("shared", f"own-{index}"))))
            for index in range(self.GROUPS)
        ]

        assert [set(match) for match in matches] == [{index} for index in range(self.GROUPS)]
        assert rows.visits == 2 * self.GROUPS
