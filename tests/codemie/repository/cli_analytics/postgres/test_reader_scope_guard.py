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

"""Detail authorization guard: every reader method that reads a session by bare id goes through the
detail predicate, and a denied or absent scope fails closed. SQL-text checks (no database)."""

import ast
import inspect
from contextlib import asynccontextmanager

import pytest

from codemie.repository.cli_analytics.postgres import reader as reader_module
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.scope import ViewerScope, sql_norm
from tests.codemie.repository.cli_analytics.support.scopes import DETAIL_SCOPE

DETAIL_METHODS = [
    "get_session_detail_meta",
    "get_session_detail_cost",
    "get_session_detail_model_costs",
    "get_session_detail_scalars",
    "get_session_detail_tools",
    "get_session_detail_events",
    "get_session_detail_dispatches",
]
# Methods allowed to read by a bare session id: each applies detail_gate (checked below).
ALLOWED_BARE = {
    *DETAIL_METHODS,
    "get_skill_names_by_session",
    "authorize_session",
    "detail_gate",
    "_model_sums",
    "_filter_conditions",
}
# The gate itself, the gate-only read, a SQL-fragment helper used inside get_session_detail_dispatches, and the
# session-id narrowing of `_sessions_cte`, which is applied after the viewer scope condition (a filter, not a read).
EXEMPT = {"authorize_session", "detail_gate", "_model_sums", "_filter_conditions"}
NON_ADMIN = ViewerScope("me@x.com", False, frozenset({"p1"}), "detail")


class _Rec:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.args: list[tuple] = []

    @asynccontextmanager
    async def acquire(self, timeout=None):
        yield self

    async def fetch(self, sql, *args):
        self.statements.append(" ".join(sql.split()))
        self.args.append(args)
        return []


async def _call(reader, name, scope):
    if name == "get_skill_names_by_session":
        return await reader.get_skill_names_by_session(["s1"], scope=scope)
    return await getattr(reader, name)("s1", scope=scope)


@pytest.mark.parametrize("name", [*DETAIL_METHODS, "get_skill_names_by_session"])
@pytest.mark.asyncio
async def test_every_detail_method_applies_the_detail_predicate(name):
    rec = _Rec()
    await _call(PostgresCliAnalyticsReader(rec), name, NON_ADMIN)  # type: ignore[arg-type]
    sql = rec.statements[0]
    assert "LEFT JOIN session_dims d ON d.session_id = g.session_id" in sql
    assert "$1" in sql or sql_norm("d.owner_email") in sql
    assert f"{sql_norm('d.owner_email')} =" in sql
    assert "coalesce(d.project_name, '') = ANY(" in sql


@pytest.mark.parametrize("scope", [None, "admin", object()], ids=["none", "str", "object"])
@pytest.mark.parametrize("name", [*DETAIL_METHODS, "get_skill_names_by_session"])
@pytest.mark.asyncio
async def test_absent_or_malformed_scope_denies_in_sql(name, scope):
    rec = _Rec()
    await _call(PostgresCliAnalyticsReader(rec), name, scope)  # type: ignore[arg-type]
    assert "WHERE false)" in rec.statements[0]


@pytest.mark.asyncio
async def test_authorize_session_is_a_cheap_read_and_denies_on_empty_result():
    rec = _Rec()
    assert await PostgresCliAnalyticsReader(rec).authorize_session("s1", DETAIL_SCOPE) is False  # type: ignore[arg-type]
    assert rec.statements[0].startswith("SELECT EXISTS (SELECT 1 FROM (SELECT $1::text AS session_id) g")
    assert "FROM cost_daily" not in rec.statements[0]


def test_missing_dims_row_is_class_m_not_personal_unowned():
    # The gate LEFT JOINs: no dims row -> NULL columns -> not Personal; project '' (unattributed).
    sql = inspect.getsource(reader_module.detail_gate)
    assert "LEFT JOIN session_dims d" in sql


def _functions_reading_by_bare_session_id():
    tree = ast.parse(inspect.getsource(reader_module))
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            text = ast.get_source_segment(inspect.getsource(reader_module), node) or ""
            if "$sid" in text or "session_id = ANY($session_ids" in text:
                found[node.name] = text
    return found


def test_guard_no_reader_function_reads_by_bare_session_id_outside_the_allow_list():
    found = _functions_reading_by_bare_session_id()
    assert set(found) <= ALLOWED_BARE, set(found) - ALLOWED_BARE
    for name, text in found.items():
        if name not in EXEMPT:
            assert "detail_gate(" in text, name
