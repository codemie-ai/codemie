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

"""Tests for UserEnrichmentRepository."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.repository.user_enrichment_repository import UserEnrichmentRepository
from codemie.rest_api.models.user_management import UserEnrichment


@pytest.fixture
def repo():
    return UserEnrichmentRepository()


@pytest.fixture
def mock_session():
    return AsyncMock()


def _make_enrichment(email: str, **kwargs) -> UserEnrichment:
    return UserEnrichment(email=email, user_id="user-1", **kwargs)


def _mock_execute(session: AsyncMock, rows: list) -> None:
    """Wire session.execute to return *rows* via .scalars().all()."""
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = rows
    session.execute.return_value = execute_result


class TestGetByEmails:
    """Tests for get_by_emails."""

    @pytest.mark.asyncio
    async def test_returns_empty_dict_for_empty_list(self, repo, mock_session):
        # Arrange / Act
        result = await repo.get_by_emails(mock_session, [])

        # Assert
        assert result == {}
        mock_session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_matching_records_keyed_by_email(self, repo, mock_session):
        # Arrange
        row = _make_enrichment("alice@example.com", first_name="Alice")
        _mock_execute(mock_session, [row])

        # Act
        result = await repo.get_by_emails(mock_session, ["alice@example.com"])

        # Assert
        assert "alice@example.com" in result
        assert result["alice@example.com"] is row

    @pytest.mark.asyncio
    async def test_normalises_input_emails_to_lowercase(self, repo, mock_session):
        # Arrange
        row = _make_enrichment("alice@example.com")
        _mock_execute(mock_session, [row])

        # Act
        result = await repo.get_by_emails(mock_session, ["ALICE@EXAMPLE.COM"])

        # Assert
        assert "alice@example.com" in result

    @pytest.mark.asyncio
    async def test_result_keys_are_lowercase_regardless_of_stored_case(self, repo, mock_session):
        # Arrange — simulate DB returning mixed-case email
        row = _make_enrichment("Alice@Example.COM")
        _mock_execute(mock_session, [row])

        # Act
        result = await repo.get_by_emails(mock_session, ["alice@example.com"])

        # Assert
        assert "alice@example.com" in result
        assert "Alice@Example.COM" not in result

    @pytest.mark.asyncio
    async def test_returns_multiple_records(self, repo, mock_session):
        # Arrange
        rows = [
            _make_enrichment("alice@example.com", first_name="Alice"),
            _make_enrichment("bob@example.com", first_name="Bob"),
        ]
        _mock_execute(mock_session, rows)

        # Act
        result = await repo.get_by_emails(mock_session, ["alice@example.com", "bob@example.com"])

        # Assert
        assert len(result) == 2
        assert result["alice@example.com"].first_name == "Alice"
        assert result["bob@example.com"].first_name == "Bob"

    @pytest.mark.asyncio
    async def test_missing_emails_absent_from_result(self, repo, mock_session):
        # Arrange — only alice has an enrichment record
        row = _make_enrichment("alice@example.com")
        _mock_execute(mock_session, [row])

        # Act
        result = await repo.get_by_emails(mock_session, ["alice@example.com", "nobody@example.com"])

        # Assert
        assert "alice@example.com" in result
        assert "nobody@example.com" not in result

    @pytest.mark.asyncio
    async def test_executes_select_with_in_clause(self, repo, mock_session):
        # Arrange
        _mock_execute(mock_session, [])

        # Act
        await repo.get_by_emails(mock_session, ["alice@example.com"])

        # Assert — verify a SELECT was executed (not a raw query or update)
        mock_session.execute.assert_called_once()
        stmt = mock_session.execute.call_args[0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "user_enrichment" in sql
        assert "alice@example.com" in sql

    @pytest.mark.asyncio
    async def test_returns_empty_dict_when_no_records_match(self, repo, mock_session):
        # Arrange
        _mock_execute(mock_session, [])

        # Act
        result = await repo.get_by_emails(mock_session, ["unknown@example.com"])

        # Assert
        assert result == {}
