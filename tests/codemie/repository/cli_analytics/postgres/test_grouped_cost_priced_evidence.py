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

"""The priced-usage evidence of the grouped cost SQL (`cost_priced`), as far as it is checkable without Postgres
(EPMCDME-17152). Its values are pinned by the live parity of `test_live_grouped_cost`; here the statement text and the
reader's row mapping are.
"""

import re

import pytest

from codemie.repository.cli_analytics.postgres.grouped_cost_sql import Grouping, grouped_cost_sql, priced_groups_cte
from codemie.repository.cli_analytics.postgres.reader import _PART_COLUMNS, _gc_part, _part, grouped_money

GROUPINGS = [
    Grouping("total", []),
    Grouping("by_repo", ["repository"]),
    Grouping("by_user_model", ["user", "model"]),
]


def test_every_arm_and_both_cell_sets_carry_the_evidence_column():
    sql = grouped_cost_sql(GROUPINGS)

    assert sql.count("AS cost_priced") == len(GROUPINGS)
    # a cell is priced when some (session, model) row has a used kind with a rate: not "not unpriced" (an empty row)
    assert sql.count("bool_or(p.has_rate) AS priced") == 2  # cells_session and cells_model
    assert "NOT p.unpriced" not in sql
    # the zero-money rows that only carry an unpriced name add no evidence, in the same position as `unpriced`
    assert sql.count("0::bigint, true, false, p.model_name") == 2


def test_the_arms_of_the_cells_union_have_the_same_number_of_columns():
    sql = grouped_cost_sql([Grouping("by_repo", ["repository"])])
    cells = sql.split("cells_session AS MATERIALIZED (")[1].split(")\n        (SELECT 'by_repo'")[0]
    first, second = (arm.strip() for arm in cells.split("UNION ALL"))

    def width(arm: str) -> int:
        select = arm.split("FROM", 1)[0] if arm.startswith("SELECT") else arm
        return len([c for c in re.split(r",(?![^(]*\))", select.removeprefix("SELECT")) if c.strip()])

    assert width(first) == width(second)


def test_the_parts_shape_has_the_column_for_every_part():
    money = _gc_part("money", GROUPINGS, "gc")
    other = _part("dead", n="count(*)")

    assert ("cost_priced", "boolean") in _PART_COLUMNS
    assert "gc.cost_priced::boolean AS cost_priced" in money
    assert money.count(" AS ") == other.count(" AS ") == len(_PART_COLUMNS)  # the UNION ALL of the parts lines up


@pytest.mark.parametrize(
    ("cost_priced", "expected"),
    [(True, True), (False, False), (None, False)],  # NULL cannot reach: coalesced in SQL
)
def test_the_reader_maps_cost_priced_onto_the_group(cost_priced, expected):
    row = {
        "grouping": "total",
        "cost_usd": 0,
        "cache_read_cost_usd": 0,
        "session_count": 1,
        "unpriced_models": ["m"],
        "cost_incomplete": True,
        "cost_priced": cost_priced,
    }

    group = grouped_money([row], [Grouping("total", [])]).groups["total"][0]

    assert group.has_priced is expected and group.cost_incomplete


def test_the_evidence_is_a_used_kind_with_a_rate_beside_the_unpriced_test_of_the_same_kinds():
    from codemie.repository.cli_analytics.postgres.cost_key_sql import priced_test, unpriced_test

    priced, unpriced = priced_test(), unpriced_test()

    assert priced.count("IS NOT NULL") == unpriced.count("IS NULL") == 5  # one term per token kind, same kinds
    assert priced.count("> 0 AND") == unpriced.count("> 0 AND") == 5  # a kind is used when its tokens are positive
    assert "has_rate" in priced_groups_cte() and priced in priced_groups_cte()
