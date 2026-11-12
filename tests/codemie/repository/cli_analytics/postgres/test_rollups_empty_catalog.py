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

"""The refresher never stamps stored sort keys under the empty rate set (EPMCDME-16427, part 2)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.repository.cli_analytics.postgres import rollups
from codemie.repository.cli_analytics.postgres.rates_version import EMPTY_RATES_VERSION

_KEYS = [{"session_id": "s1", "kinds": ~0}]


class _Connection:
    """The savepoint the derivation runs in, and the unstamping a catalog without rates would do."""

    def __init__(self) -> None:
        self.execute = AsyncMock()

    def transaction(self):
        return AsyncMock()


async def _write(version: str, conn: _Connection | None = None) -> tuple[AsyncMock, _Connection]:
    derive = AsyncMock()
    conn = conn or _Connection()
    me = SimpleNamespace(_rates=MagicMock(), _rates_version=lambda: version)
    me._derive_sort_keys = lambda *args: rollups.RollupRefresher._derive_sort_keys(me, *args)
    with (
        patch.object(rollups, "derive_session_sort_keys", derive),
        patch.object(rollups, "read_classifier_names", AsyncMock(return_value={})),
        patch.object(rollups, "_SORT_KEY_BITS", 1),
    ):
        await rollups.RollupRefresher._write_sort_keys(me, conn, _KEYS, {}, 1.0)
    return derive, conn


@pytest.mark.asyncio
async def test_an_empty_catalog_stamps_nothing_and_drops_the_stamps_of_the_rewritten_sessions():
    derive, conn = await _write(EMPTY_RATES_VERSION)

    assert derive.await_count == 0
    # the day rows of s1 were just rewritten: a stamp from an earlier catalog must not stay trusted
    conn.execute.assert_awaited_once()
    assert conn.execute.await_args.args[0].endswith("AND rates_version IS NOT NULL")
    assert conn.execute.await_args.args[1] == ["s1"]


@pytest.mark.asyncio
async def test_a_loaded_catalog_stamps_the_sessions_and_unstamps_none():
    derive, conn = await _write("v-real")

    assert derive.await_count == 1
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failing_unstamp_is_not_swallowed_so_the_recompute_rolls_back_and_the_key_is_retried():
    conn = _Connection()
    conn.execute.side_effect = RuntimeError("statement timeout")

    with pytest.raises(RuntimeError, match="statement timeout"):
        await _write(EMPTY_RATES_VERSION, conn)
