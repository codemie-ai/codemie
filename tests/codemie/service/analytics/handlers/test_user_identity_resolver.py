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

"""Unit tests for UserIdentityResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.analytics.handlers.user_identity_resolver import UserIdentityResolver

_UUID = "550e8400-e29b-41d4-a716-446655440000"
_PATCH_SESSION = "codemie.service.analytics.handlers.user_identity_resolver.get_async_session"


@pytest.fixture
def mock_session():
    return AsyncMock()


@pytest.fixture
def mock_db(mock_session):
    """Async context manager fixture that yields mock_session."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


def _db_row(**kwargs):
    row = MagicMock()
    for k, v in kwargs.items():
        setattr(row, k, v)
    return row


# ── resolve() ────────────────────────────────────────────────────────────────


class TestResolve:
    @pytest.mark.asyncio
    async def test_plain_email_returned_without_db(self):
        with patch(_PATCH_SESSION) as mock_get_session:
            result = await UserIdentityResolver.resolve("alice@example.com")
            mock_get_session.assert_not_called()
        assert result == "alice@example.com"

    @pytest.mark.asyncio
    async def test_cli_suffix_stripped_to_email(self):
        with patch(_PATCH_SESSION) as mock_get_session:
            result = await UserIdentityResolver.resolve("alice@example.com_codemie_cli")
            mock_get_session.assert_not_called()
        assert result == "alice@example.com"

    @pytest.mark.asyncio
    async def test_other_suffix_stripped_to_email(self):
        with patch(_PATCH_SESSION) as mock_get_session:
            result = await UserIdentityResolver.resolve("alice@example.com_codemie_premium_models")
            mock_get_session.assert_not_called()
        assert result == "alice@example.com"

    @pytest.mark.asyncio
    async def test_first_email_candidate_wins(self):
        with patch(_PATCH_SESSION) as mock_get_session:
            result = await UserIdentityResolver.resolve("alice@example.com", "bob@example.com")
            mock_get_session.assert_not_called()
        assert result == "alice@example.com"

    @pytest.mark.asyncio
    async def test_skips_none_finds_email_in_later_candidate(self):
        with patch(_PATCH_SESSION) as mock_get_session:
            result = await UserIdentityResolver.resolve(None, "bob@example.com", _UUID)
            mock_get_session.assert_not_called()
        assert result == "bob@example.com"

    @pytest.mark.asyncio
    async def test_uuid_resolved_via_db_by_id(self, mock_session, mock_db):
        mock_session.execute = AsyncMock(return_value=[_db_row(id=_UUID, email="alice@example.com")])
        with patch(_PATCH_SESSION, return_value=mock_db):
            result = await UserIdentityResolver.resolve(_UUID)
        assert result == "alice@example.com"

    @pytest.mark.asyncio
    async def test_username_resolved_via_db(self, mock_session, mock_db):
        mock_session.execute = AsyncMock(
            return_value=[_db_row(username="alice_smith", name="Alice Smith", email="alice@example.com")]
        )
        with patch(_PATCH_SESSION, return_value=mock_db):
            result = await UserIdentityResolver.resolve("alice_smith")
        assert result == "alice@example.com"

    @pytest.mark.asyncio
    async def test_display_name_resolved_via_db(self, mock_session, mock_db):
        mock_session.execute = AsyncMock(
            return_value=[_db_row(username="asmith", name="Alice Smith", email="alice@example.com")]
        )
        with patch(_PATCH_SESSION, return_value=mock_db):
            result = await UserIdentityResolver.resolve("Alice Smith")
        assert result == "alice@example.com"

    @pytest.mark.asyncio
    async def test_db_miss_returns_first_stripped_candidate(self, mock_session, mock_db):
        mock_session.execute = AsyncMock(return_value=[])
        with patch(_PATCH_SESSION, return_value=mock_db):
            result = await UserIdentityResolver.resolve("unknown_user")
        assert result == "unknown_user"

    @pytest.mark.asyncio
    async def test_no_values_returns_empty_string(self):
        result = await UserIdentityResolver.resolve()
        assert result == ""

    @pytest.mark.asyncio
    async def test_all_none_returns_empty_string(self):
        result = await UserIdentityResolver.resolve(None, None)
        assert result == ""


# ── resolve_rows() ────────────────────────────────────────────────────────────


class TestResolveRows:
    @pytest.mark.asyncio
    async def test_plain_emails_unchanged_no_db(self):
        rows = [{"user": "alice@example.com"}, {"user": "bob@example.com"}]
        with patch(_PATCH_SESSION) as mock_get_session:
            await UserIdentityResolver.resolve_rows(rows, "user")
            mock_get_session.assert_not_called()
        assert rows[0]["user"] == "alice@example.com"
        assert rows[1]["user"] == "bob@example.com"

    @pytest.mark.asyncio
    async def test_suffix_stripped_no_db(self):
        rows = [{"user": "alice@example.com_codemie_cli"}]
        with patch(_PATCH_SESSION) as mock_get_session:
            await UserIdentityResolver.resolve_rows(rows, "user")
            mock_get_session.assert_not_called()
        assert rows[0]["user"] == "alice@example.com"

    @pytest.mark.asyncio
    async def test_uuid_resolved_via_db(self, mock_session, mock_db):
        rows = [{"user": _UUID}]
        mock_session.execute = AsyncMock(return_value=[_db_row(id=_UUID, email="alice@example.com")])
        with patch(_PATCH_SESSION, return_value=mock_db):
            await UserIdentityResolver.resolve_rows(rows, "user")
        assert rows[0]["user"] == "alice@example.com"

    @pytest.mark.asyncio
    async def test_username_resolved_via_db(self, mock_session, mock_db):
        rows = [{"user": "alice_smith"}]
        mock_session.execute = AsyncMock(
            return_value=[_db_row(username="alice_smith", name="Alice Smith", email="alice@example.com")]
        )
        with patch(_PATCH_SESSION, return_value=mock_db):
            await UserIdentityResolver.resolve_rows(rows, "user")
        assert rows[0]["user"] == "alice@example.com"

    @pytest.mark.asyncio
    async def test_multiple_columns_normalized(self):
        rows = [{"u": "alice@example.com_codemie_cli", "v": "bob@example.com_codemie_premium_models"}]
        with patch(_PATCH_SESSION) as mock_get_session:
            await UserIdentityResolver.resolve_rows(rows, "u", "v")
            mock_get_session.assert_not_called()
        assert rows[0]["u"] == "alice@example.com"
        assert rows[0]["v"] == "bob@example.com"

    @pytest.mark.asyncio
    async def test_empty_rows_no_db(self):
        with patch(_PATCH_SESSION) as mock_get_session:
            await UserIdentityResolver.resolve_rows([], "user")
            mock_get_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_mixed_emails_and_uuids_single_db_call(self, mock_session, mock_db):
        rows = [{"user": "alice@example.com"}, {"user": _UUID}]
        mock_session.execute = AsyncMock(return_value=[_db_row(id=_UUID, email="bob@example.com")])
        with patch(_PATCH_SESSION, return_value=mock_db) as mock_get_session:
            await UserIdentityResolver.resolve_rows(rows, "user")
            mock_get_session.assert_called_once()
        assert rows[0]["user"] == "alice@example.com"
        assert rows[1]["user"] == "bob@example.com"

    @pytest.mark.asyncio
    async def test_db_miss_keeps_stripped_value(self, mock_session, mock_db):
        rows = [{"user": "unknown_username"}]
        mock_session.execute = AsyncMock(return_value=[])
        with patch(_PATCH_SESSION, return_value=mock_db):
            await UserIdentityResolver.resolve_rows(rows, "user")
        assert rows[0]["user"] == "unknown_username"

    @pytest.mark.asyncio
    async def test_missing_key_in_row_skipped(self):
        rows = [{"other": "value"}]
        with patch(_PATCH_SESSION) as mock_get_session:
            await UserIdentityResolver.resolve_rows(rows, "user")
            mock_get_session.assert_not_called()
        assert rows[0] == {"other": "value"}


# ── resolve_and_merge() ───────────────────────────────────────────────────────


class TestResolveAndMerge:
    @pytest.mark.asyncio
    async def test_resolved_row_carries_db_email(self, mock_session, mock_db):
        rows = [{"id": _UUID, "name": "Alice Smith"}]
        mock_session.execute = AsyncMock(
            return_value=[_db_row(id=_UUID, username="asmith", name="Alice Smith", email="alice@example.com")]
        )
        with patch(_PATCH_SESSION, return_value=mock_db):
            merged = await UserIdentityResolver.resolve_and_merge(rows)
        assert merged == [{"id": _UUID, "name": "Alice Smith", "email": "alice@example.com"}]

    @pytest.mark.asyncio
    async def test_unresolved_row_falls_back_to_email_shaped_identifier(self, mock_session, mock_db):
        """ES often stores the email as user_name; keep it when the DB lookup misses."""
        rows = [{"id": "raw-id-1", "name": "bob@example.com"}]
        mock_session.execute = AsyncMock(return_value=[])
        with patch(_PATCH_SESSION, return_value=mock_db):
            merged = await UserIdentityResolver.resolve_and_merge(rows)
        assert merged == [{"id": "raw-id-1", "name": "bob@example.com", "email": "bob@example.com"}]

    @pytest.mark.asyncio
    async def test_unresolved_row_without_email_shape_gets_none(self, mock_session, mock_db):
        rows = [{"id": "raw-id-2", "name": "carol_jones"}]
        mock_session.execute = AsyncMock(return_value=[])
        with patch(_PATCH_SESSION, return_value=mock_db):
            merged = await UserIdentityResolver.resolve_and_merge(rows)
        assert merged == [{"id": "raw-id-2", "name": "carol_jones", "email": None}]

    @pytest.mark.asyncio
    async def test_dedup_keeps_first_occurrence_with_email(self, mock_session, mock_db):
        """Two raw ES forms of the same user collapse into one row that keeps the email."""
        rows = [{"id": _UUID, "name": "Alice Smith"}, {"id": "alice@example.com", "name": "Alice Smith"}]
        mock_session.execute = AsyncMock(
            return_value=[_db_row(id=_UUID, username="asmith", name="Alice Smith", email="alice@example.com")]
        )
        with patch(_PATCH_SESSION, return_value=mock_db):
            merged = await UserIdentityResolver.resolve_and_merge(rows)
        assert merged == [{"id": _UUID, "name": "Alice Smith", "email": "alice@example.com"}]

    @pytest.mark.asyncio
    async def test_empty_list_returned_as_is_no_db(self):
        with patch(_PATCH_SESSION) as mock_get_session:
            merged = await UserIdentityResolver.resolve_and_merge([])
            mock_get_session.assert_not_called()
        assert merged == []
