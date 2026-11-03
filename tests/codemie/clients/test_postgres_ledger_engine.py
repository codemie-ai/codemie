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

"""The spend ledger's async engine: the application's by default, its own only when configured (EPMCDME-15537)."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from codemie.clients import postgres
from codemie.clients.postgres import PostgresClient

APPLICATION_POOL_SIZE = 10
SEARCH_PATH = f"{postgres.config.DEFAULT_DB_SCHEMA},public"


@pytest.fixture
def create_async_engine():
    """`create_async_engine` stubbed with both per-process caches emptied and no atexit hook left behind."""
    factory = MagicMock(side_effect=lambda **kwargs: MagicMock(name="engine"))
    with (
        patch.object(postgres, "create_async_engine", factory),
        patch.object(postgres.atexit, "register"),
        patch.object(postgres.config, "PG_IAM_AUTH_PROVIDER", ""),
        patch.object(postgres.config, "PG_POOL_SIZE", APPLICATION_POOL_SIZE),
        patch.dict(PostgresClient._async_engines, clear=True),
        patch.dict(PostgresClient._ledger_async_engines, clear=True),
    ):
        yield factory


def _ledger_pool(size=3, timeout_seconds=7.5, statement_timeout_ms=20_000):
    return (
        patch.object(postgres.config, "SPEND_LEDGER_POOL_SIZE", size),
        patch.object(postgres.config, "SPEND_LEDGER_POOL_TIMEOUT_SECONDS", timeout_seconds),
        patch.object(postgres.config, "SPEND_LEDGER_STATEMENT_TIMEOUT_MS", statement_timeout_ms),
    )


def test_ledger_reads_share_the_application_engine_by_default(create_async_engine):
    with patch.object(postgres.config, "SPEND_LEDGER_POOL_SIZE", 0):
        assert PostgresClient.get_ledger_async_engine() is PostgresClient.get_async_engine()

    create_async_engine.assert_called_once()
    assert not PostgresClient._ledger_async_engines


def test_a_configured_pool_size_builds_a_dedicated_engine_with_its_timeouts(create_async_engine):
    size, timeout, statement = _ledger_pool()
    with size, timeout, statement:
        ledger_engine = PostgresClient.get_ledger_async_engine()

    assert ledger_engine is not PostgresClient.get_async_engine()
    options = create_async_engine.call_args_list[0].kwargs
    assert (options["pool_size"], options["pool_timeout"], options["max_overflow"]) == (3, 7.5, 0)
    assert options["pool_pre_ping"] is True
    assert options["connect_args"] == {"server_settings": {"search_path": SEARCH_PATH, "statement_timeout": "20000"}}
    postgres.atexit.register.assert_any_call(PostgresClient.cleanup_ledger_async_engine)


def test_a_zero_statement_timeout_sets_none_on_the_dedicated_engine(create_async_engine):
    size, timeout, statement = _ledger_pool(statement_timeout_ms=0)
    with size, timeout, statement:
        PostgresClient.get_ledger_async_engine()

    assert create_async_engine.call_args.kwargs["connect_args"] == {"server_settings": {"search_path": SEARCH_PATH}}


def test_the_dedicated_engine_is_built_once_per_process(create_async_engine):
    size, timeout, statement = _ledger_pool()
    with size, timeout, statement:
        assert PostgresClient.get_ledger_async_engine() is PostgresClient.get_ledger_async_engine()

    create_async_engine.assert_called_once()


def test_the_dedicated_engine_authenticates_with_iam_like_the_application_engine(create_async_engine):
    size, timeout, statement = _ledger_pool()
    with size, timeout, statement:
        with (
            patch.object(postgres.config, "PG_IAM_AUTH_PROVIDER", "aws"),
            patch.object(postgres, "_register_iam_token_event") as register,
        ):
            ledger_engine = PostgresClient.get_ledger_async_engine()

    register.assert_called_once_with(ledger_engine)


def test_the_application_engine_is_built_exactly_as_before(create_async_engine):
    PostgresClient.get_async_engine()

    options = create_async_engine.call_args.kwargs
    assert set(options) == {
        "url",
        "echo",
        "pool_pre_ping",
        "pool_size",
        "max_overflow",
        "json_serializer",
        "connect_args",
    }, "no pool_timeout: the application engine keeps SQLAlchemy's default"
    assert options["url"] == PostgresClient._get_connection_string(async_mode=True)
    assert (options["echo"], options["pool_pre_ping"], options["max_overflow"]) == (False, True, 0)
    assert options["pool_size"] == APPLICATION_POOL_SIZE
    assert options["connect_args"] == {"server_settings": {"search_path": SEARCH_PATH}}


def test_a_ledger_session_is_opened_on_the_ledger_engine():
    ledger_engine = MagicMock(name="ledger engine")

    async def _open():
        async with postgres.get_ledger_async_session() as session:
            return session

    with (
        patch.object(PostgresClient, "get_ledger_async_engine", return_value=ledger_engine),
        patch.object(postgres, "AsyncSession") as async_session,
    ):
        opened = asyncio.run(_open())

    async_session.assert_called_once_with(ledger_engine)
    assert opened is async_session.return_value.__aenter__.return_value
