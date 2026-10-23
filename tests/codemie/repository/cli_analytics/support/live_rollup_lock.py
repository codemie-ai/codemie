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


"""The advisory lock the live rollup tests share. They all run against one database, and a rollup
refresh recomputes every queued key in it, other tests' keys included: two recomputes of one key at
once collide on the rollups' unique keys, so every live test that refreshes or recomputes rollups
does its database work under this one lock."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine

_LIVE_ROLLUP_LOCK_KEY = 0x726F6C6C7570


@asynccontextmanager
async def live_rollup_lock(engine: AnalyticsPgEngine) -> AsyncIterator[None]:
    """Hold the lock on a dedicated connection of the engine for the block's duration."""
    async with engine.acquire() as lock_conn:
        await lock_conn.execute("SELECT pg_advisory_lock($1)", _LIVE_ROLLUP_LOCK_KEY)
        try:
            yield
        finally:
            await lock_conn.execute("SELECT pg_advisory_unlock($1)", _LIVE_ROLLUP_LOCK_KEY)
