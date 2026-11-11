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

import pytest
from unittest.mock import MagicMock, patch
from sqlmodel import select

from codemie.rest_api.models.assistant import Assistant, AssistantSortBy
from codemie.rest_api.models.index import SortOrder
from codemie.service.assistant.assistant_repository import AssistantRepository, AssistantScope


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.is_admin = False
    user.is_external_user = False
    user.project_names = ["DEMO"]
    user.admin_project_names = []
    user.id = "test_user"
    return user


# ---------------------------------------------------------------------------
# _build_sort_column — column + direction + null handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sort_by,sort_order,expected_column,expected_direction,expected_nulls",
    [
        (AssistantSortBy.USAGE, SortOrder.DESC, "unique_users_count", "DESC", "NULLS LAST"),
        (AssistantSortBy.USAGE, SortOrder.ASC, "unique_users_count", "ASC", "NULLS FIRST"),
        (AssistantSortBy.LIKES, SortOrder.DESC, "unique_likes_count", "DESC", "NULLS LAST"),
        (AssistantSortBy.LIKES, SortOrder.ASC, "unique_likes_count", "ASC", "NULLS FIRST"),
        (AssistantSortBy.DISLIKES, SortOrder.DESC, "unique_dislikes_count", "DESC", "NULLS LAST"),
        (AssistantSortBy.DISLIKES, SortOrder.ASC, "unique_dislikes_count", "ASC", "NULLS FIRST"),
        (AssistantSortBy.NAME, SortOrder.DESC, "name", "DESC", "NULLS LAST"),
        (AssistantSortBy.NAME, SortOrder.ASC, "name", "ASC", "NULLS FIRST"),
    ],
)
def test_build_sort_column(sort_by, sort_order, expected_column, expected_direction, expected_nulls):
    """_build_sort_column produces correct column, direction, and null handling."""
    expr = AssistantRepository._build_sort_column(sort_by, sort_order)
    sql = str(expr.compile(compile_kwargs={"literal_binds": True}))
    assert expected_column in sql
    assert expected_direction in sql.upper()
    assert expected_nulls in sql.upper()


def test_build_sort_column_none_returns_none():
    """_build_sort_column returns None when sort_by is None."""
    result = AssistantRepository._build_sort_column(None, SortOrder.DESC)
    assert result is None


# ---------------------------------------------------------------------------
# _apply_sort_to_query — full ORDER BY assembly per scope
# ---------------------------------------------------------------------------


def _sql(query) -> str:
    return str(query.compile(compile_kwargs={"literal_binds": True})).upper()


# --- MARKETPLACE ---


def test_marketplace_default_sort_uses_unique_users_count():
    """MARKETPLACE with sort_by=None → unique_users_count DESC NULLS LAST first."""
    q = AssistantRepository._apply_sort_to_query(
        select(Assistant), AssistantScope.MARKETPLACE, None, SortOrder.DESC, True
    )
    sql = _sql(q)
    assert "UNIQUE_USERS_COUNT" in sql
    assert "DESC NULLS LAST" in sql


def test_marketplace_default_sort_has_stable_tiebreakers():
    """MARKETPLACE default ORDER BY includes update_date and id as tiebreakers."""
    q = AssistantRepository._apply_sort_to_query(
        select(Assistant), AssistantScope.MARKETPLACE, None, SortOrder.DESC, True
    )
    sql = _sql(q)
    assert "UPDATE_DATE" in sql
    assert "ID" in sql


@pytest.mark.parametrize(
    "sort_by,col",
    [
        (AssistantSortBy.USAGE, "UNIQUE_USERS_COUNT"),
        (AssistantSortBy.LIKES, "UNIQUE_LIKES_COUNT"),
        (AssistantSortBy.DISLIKES, "UNIQUE_DISLIKES_COUNT"),
        (AssistantSortBy.NAME, "NAME"),
    ],
)
def test_marketplace_explicit_sort_uses_requested_column(sort_by, col):
    """MARKETPLACE with sort_by set → requested column appears first in ORDER BY."""
    q = AssistantRepository._apply_sort_to_query(
        select(Assistant), AssistantScope.MARKETPLACE, sort_by, SortOrder.DESC, True
    )
    sql = _sql(q)
    assert col in sql
    # update_date and id remain as tiebreakers
    assert "UPDATE_DATE" in sql
    assert "ID" in sql


# --- PROJECT_WITH_MARKETPLACE ---


def test_project_with_marketplace_default_has_is_global_grouping():
    """PROJECT_WITH_MARKETPLACE with sort_by=None → is_global in ORDER BY for grouping."""
    q = AssistantRepository._apply_sort_to_query(
        select(Assistant), AssistantScope.PROJECT_WITH_MARKETPLACE, None, SortOrder.DESC, True
    )
    sql = _sql(q)
    assert "IS_GLOBAL" in sql


def test_project_with_marketplace_group_true_prepends_is_global():
    """PROJECT_WITH_MARKETPLACE sort_by=USAGE, group_by_is_global=True → is_global first."""
    q = AssistantRepository._apply_sort_to_query(
        select(Assistant), AssistantScope.PROJECT_WITH_MARKETPLACE, AssistantSortBy.USAGE, SortOrder.DESC, True
    )
    sql = _sql(q)
    assert "IS_GLOBAL" in sql
    assert "UNIQUE_USERS_COUNT" in sql
    # is_global must appear before unique_users_count in the ORDER BY list
    assert sql.index("IS_GLOBAL") < sql.index("UNIQUE_USERS_COUNT")


def test_project_with_marketplace_group_false_omits_is_global():
    """PROJECT_WITH_MARKETPLACE sort_by=USAGE, group_by_is_global=False → no is_global prefix."""
    q = AssistantRepository._apply_sort_to_query(
        select(Assistant), AssistantScope.PROJECT_WITH_MARKETPLACE, AssistantSortBy.USAGE, SortOrder.DESC, False
    )
    sql = _sql(q)
    assert "UNIQUE_USERS_COUNT" in sql
    # is_global must NOT appear as an ORDER BY term when group_by_is_global=False
    # (it may appear in WHERE/FROM clauses, so check ORDER BY substring only)
    order_by_part = sql.split("ORDER BY", 1)[-1] if "ORDER BY" in sql else sql
    assert "IS_GLOBAL" not in order_by_part


def test_project_with_marketplace_default_group_false_omits_is_global():
    """PROJECT_WITH_MARKETPLACE sort_by=None, group_by_is_global=False → no is_global grouping."""
    q = AssistantRepository._apply_sort_to_query(
        select(Assistant), AssistantScope.PROJECT_WITH_MARKETPLACE, None, SortOrder.DESC, False
    )
    sql = _sql(q)
    order_by_part = sql.split("ORDER BY", 1)[-1] if "ORDER BY" in sql else sql
    assert "IS_GLOBAL" not in order_by_part
    assert "UNIQUE_USERS_COUNT" in order_by_part


# --- Other scopes fall back to update_date ---


def test_other_scope_sorts_by_update_date():
    """Non-MARKETPLACE, non-PROJECT_WITH_MARKETPLACE scopes sort by update_date, then id as tiebreaker."""
    q = AssistantRepository._apply_sort_to_query(
        select(Assistant), AssistantScope.VISIBLE_TO_USER, AssistantSortBy.USAGE, SortOrder.DESC, True
    )
    sql = _sql(q)
    # Sort params are ignored — ORDER BY must only contain update_date, not the requested sort col
    order_by_part = sql.split("ORDER BY", 1)[-1] if "ORDER BY" in sql else sql
    assert "ASSISTANTS.UPDATE_DATE DESC NULLS LAST, ASSISTANTS.ID ASC" in order_by_part
    assert "UNIQUE_USERS_COUNT" not in order_by_part


# ---------------------------------------------------------------------------
# EPMCDME-13980 — search filter must not override explicit sort_by
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sort_by,expected_col",
    [
        (AssistantSortBy.LIKES, "unique_likes_count"),
        (AssistantSortBy.DISLIKES, "unique_dislikes_count"),
        (AssistantSortBy.USAGE, "unique_users_count"),
    ],
)
@patch("codemie.service.assistant.assistant_repository.Session")
def test_marketplace_search_with_explicit_sort_sort_takes_precedence_over_priority_case(
    mock_session_class, mock_user, sort_by, expected_col
):
    """When user searches AND sorts by likes/dislikes/usage, the sort must apply to ALL
    matched assistants (title + description), not just re-order within each priority group.

    Bug: compose_multi_field_wildcard_filter injects an ORDER BY priority_case, which
    is then followed by the user's sort. SQLAlchemy appends ORDER BYs, so the priority
    case wins and description-matched assistants stay after all title-matched ones
    regardless of likes/dislikes/usage.
    """
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = []
    mock_session.exec.return_value.one.return_value = 0

    AssistantRepository().query(
        user=mock_user,
        scope=AssistantScope.MARKETPLACE,
        filters={"search": "AI"},
        sort_by=sort_by,
        sort_order=SortOrder.DESC,
        page=0,
        per_page=10,
    )

    # Last exec call runs the paginated/sorted query
    executed_query = mock_session.exec.call_args_list[-1][0][0]
    sql = str(executed_query).lower()

    order_by_part = sql.split("order by", 1)[-1] if "order by" in sql else sql

    # The requested sort column must be the FIRST ORDER BY term — before any priority CASE.
    assert expected_col in order_by_part, f"Expected {expected_col} in ORDER BY, got: {order_by_part}"

    if "case" in order_by_part:
        # If a CASE expression still appears (fallback tiebreaker is acceptable),
        # it must come AFTER the user-requested sort column.
        assert order_by_part.index(expected_col) < order_by_part.index(
            "case"
        ), f"User sort '{expected_col}' must precede any priority CASE. ORDER BY: {order_by_part}"


@patch("codemie.service.assistant.assistant_repository.Session")
def test_marketplace_search_with_name_sort_keeps_priority_case(mock_session_class, mock_user):
    """When user searches AND sorts by NAME, priority (title-first) must apply BEFORE
    the name ordering — so title matches are listed A→Z first, then description-only
    matches A→Z. Any priority CASE injected by the filter must precede the NAME term.
    """
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = []
    mock_session.exec.return_value.one.return_value = 0

    AssistantRepository().query(
        user=mock_user,
        scope=AssistantScope.MARKETPLACE,
        filters={"search": "AI"},
        sort_by=AssistantSortBy.NAME,
        sort_order=SortOrder.ASC,
        page=0,
        per_page=10,
    )

    executed_query = mock_session.exec.call_args_list[-1][0][0]
    sql = str(executed_query).lower()
    order_by_part = sql.split("order by", 1)[-1] if "order by" in sql else sql

    assert "name" in order_by_part
    if "case" in order_by_part:
        assert order_by_part.index("case") < order_by_part.index(
            "name"
        ), f"Priority CASE must precede NAME sort. ORDER BY: {order_by_part}"


@patch("codemie.service.assistant.assistant_repository.Session")
def test_marketplace_search_without_explicit_sort_still_uses_priority_ordering(mock_session_class, mock_user):
    """When user searches WITHOUT explicit sort, title-first priority ordering is preserved."""
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = []
    mock_session.exec.return_value.one.return_value = 0

    AssistantRepository().query(
        user=mock_user,
        scope=AssistantScope.MARKETPLACE,
        filters={"search": "AI"},
        sort_by=None,
        sort_order=SortOrder.DESC,
        page=0,
        per_page=10,
    )

    executed_query = mock_session.exec.call_args_list[-1][0][0]
    sql = str(executed_query).lower()
    order_by_part = sql.split("order by", 1)[-1] if "order by" in sql else sql

    # The default MARKETPLACE sort (unique_users_count) is still present
    assert "unique_users_count" in order_by_part
