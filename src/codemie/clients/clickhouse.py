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
from __future__ import annotations

import asyncio
import threading

import clickhouse_connect

from codemie.configs.config import config

_thread_local = threading.local()


def get_client() -> clickhouse_connect.driver.Client:
    client = getattr(_thread_local, "client", None)
    if client is None:
        client = clickhouse_connect.get_client(
            host=config.CLICKHOUSE_HOST,
            port=config.CLICKHOUSE_PORT,
            username=config.CLICKHOUSE_USER,
            password=config.CLICKHOUSE_PASSWORD,
            database="codemie_analytics",
            settings={"max_execution_time": config.CLICKHOUSE_QUERY_TIMEOUT_SECONDS},
        )
        _thread_local.client = client
    return client


async def ch_query(sql: str, params: dict) -> list[dict]:
    def _run() -> list[dict]:
        return list(get_client().query(sql, parameters=params).named_results())

    return await asyncio.to_thread(_run)
