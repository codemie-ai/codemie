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

"""Tests for the keyset-paged query behind the assistant icon backfill."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from codemie.rest_api.models import assistant as assistant_module
from codemie.rest_api.models.assistant import Assistant


@pytest.fixture
def captured(mocker):
    """Patch Session so the statement handed to session.exec is captured instead of executed."""
    statements = []
    session = MagicMock()
    session.exec.side_effect = lambda stmt: statements.append(stmt) or MagicMock(all=lambda: ["row"])
    mocker.patch.object(assistant_module, "Session").return_value.__enter__.return_value = session
    mocker.patch.object(Assistant, "get_engine", return_value=MagicMock())
    return statements


def _sql(statement) -> tuple[str, dict]:
    compiled = statement.compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


def test_first_page_filters_uploaded_icons_orders_by_id_and_limits(captured) -> None:
    rows = Assistant.get_uploaded_icon_batch(None, 200)

    sql, params = _sql(captured[0])
    assert rows == ["row"]
    assert "icon_url LIKE" in sql
    assert any("/v1/files/" in str(v) for v in params.values())
    assert "ORDER BY assistants.id" in sql and "DESC" not in sql
    assert " > " not in sql
    assert 200 in params.values()


def test_next_page_uses_strict_keyset_cursor(captured) -> None:
    Assistant.get_uploaded_icon_batch("a5", 50)

    sql, params = _sql(captured[0])
    assert "assistants.id > " in sql
    assert "a5" in params.values()
    assert sql.index("assistants.id >") < sql.index("ORDER BY")
    assert 50 in params.values()


def test_global_parents_query_filters_global_assistants_listing_the_sub_assistant(captured) -> None:
    rows = Assistant.get_global_parents("sub-1")

    sql, params = _sql(captured[0])
    assert rows == ["row"]
    assert "assistants.is_global" in sql
    assert "@>" in sql
    assert any("sub-1" in str(v) for v in params.values())
