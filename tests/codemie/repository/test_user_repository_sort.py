# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""Unit tests for EPMCDME-16070: ORDER BY built by UserRepository.query_users."""

import re
from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql
from sqlmodel import Session

from codemie.repository.user_repository import user_repository
from codemie.rest_api.models.user_management import UserListFilters

# Whitespace set stripped before the empty check: space, tab, newline, carriage return, NBSP.
NAME_EXPR = "lower(coalesce(nullif(btrim(users.name, ' \t\n\r\u00a0'), ''), users.username))"


def _compile_sql(statement) -> str:
    """Compile a SQLModel statement to a lowercased postgres SQL string."""
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()


def _normalise(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def _order_by_clause(**kwargs) -> str:
    session = MagicMock(spec=Session)
    user_repository.query_users(session, **kwargs)
    # Split without collapsing whitespace: the btrim character set is whitespace itself.
    sql = _compile_sql(session.exec.call_args[0][0])
    order_by = re.split(r"\s+order by\s+", sql, maxsplit=1)[1]
    return re.split(r"\s+limit\s+", order_by, maxsplit=1)[0]


class TestQueryUsersOrdering:
    def test_name_asc_order(self):
        assert _order_by_clause(sort_by="name") == f"{NAME_EXPR} asc, users.email asc, users.id asc"

    def test_name_desc_order(self):
        assert _order_by_clause(sort_by="name", sort_order="desc") == f"{NAME_EXPR} desc, users.email asc, users.id asc"

    def test_default_order(self):
        assert _order_by_clause() == "users.date desc, users.id asc"

    def test_default_order_ignores_sort_order(self):
        assert _order_by_clause(sort_order="desc") == "users.date desc, users.id asc"

    def test_positional_call_shape_still_supported(self):
        session = MagicMock(spec=Session)
        user_repository.query_users(session, None, UserListFilters(), 2, 10)
        sql = _normalise(_compile_sql(session.exec.call_args[0][0]))
        assert "order by users.date desc, users.id asc" in sql


class TestNameSortKeyWhitespace:
    def test_name_key_strips_all_whitespace_kinds(self):
        sql = _compile_sql(user_repository._SORT_COLUMN_MAP["name"])
        assert sql == NAME_EXPR
        for char in (" ", "\t", "\n", "\r", "\u00a0"):
            assert char in sql.split("btrim(users.name, ", 1)[1].split(")", 1)[0]


class TestCountUsersUnordered:
    def test_count_users_unordered(self):
        session = MagicMock(spec=Session)
        user_repository.count_users(session)
        sql = _compile_sql(session.exec.call_args[0][0])
        assert "order by" not in sql
