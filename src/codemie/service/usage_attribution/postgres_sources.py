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

"""Reads of the CLI analytics Postgres by usage attribution: the reader with its feature-flag check, the A5 framework
sessions of the analysis, and the skill names of the person view. Every failure degrades to the Elasticsearch data."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING

from codemie.service.usage_attribution.constants import PG_FRAMEWORK_TIMEOUT_S, PG_SKILL_NAMES_TIMEOUT_S

if TYPE_CHECKING:
    from codemie.repository.cli_analytics.ports import CliAnalyticsReader

logger = logging.getLogger(__name__)


def feature_enabled() -> bool:
    """Whether the ``cliAnalytics`` feature is on. A failing customer configuration raises."""
    from codemie.configs.customer_config import customer_config

    return customer_config.is_feature_enabled("cliAnalytics")


def _storage_reader() -> CliAnalyticsReader:
    from codemie.repository.cli_analytics.factory import get_cli_analytics_storage

    return get_cli_analytics_storage().reader


def reader_or_none() -> CliAnalyticsReader | None:
    """The CLI analytics reader, or None when the ``cliAnalytics`` feature is off, the storage configuration is bad,
    or the storage cannot be built (logged at warning with the type of the exception only)."""
    try:
        if not feature_enabled():
            return None
        return _storage_reader()
    except Exception as exc:  # noqa: BLE001 - unconfigured or unreachable storage: Elasticsearch data stands alone
        logger.warning("CLI analytics reader unavailable: %s", type(exc).__name__)
        return None


async def pg_framework_rows(start: datetime, end: datetime) -> list[list[str | int]] | None:
    """A5 framework sessions started in [start, end) counted in the CLI analytics Postgres, per person and
    repository (``framework_sessions.aggregate_pg_rows``). ``[]`` is a complete answer: the ``cliAnalytics`` feature is
    off, no rule label is configured, or nothing was found. None is a failed read: the feature check, the label
    configuration or the storage build failed, or the query failed or exceeded ``PG_FRAMEWORK_TIMEOUT_S``. The worker
    then keeps the Elasticsearch counts, and the caller must not reuse that result as the answer for the rest of the
    hour."""
    try:
        from codemie.service.analytics.delivery_framework import get_rule_framework_labels
        from codemie.service.usage_attribution.framework_sessions import aggregate_pg_rows

        if not feature_enabled():
            return []
        labels = get_rule_framework_labels()
        if not labels:
            return []
        reader = _storage_reader()
        rows = await asyncio.wait_for(reader.get_framework_sessions(start, end, labels), PG_FRAMEWORK_TIMEOUT_S)
        return await asyncio.to_thread(aggregate_pg_rows, rows)
    except Exception as exc:  # noqa: BLE001 - A5 falls back to the Elasticsearch counts; no personal data logged
        logger.warning("A5 framework sessions from Postgres unavailable: %s", type(exc).__name__)
        return None


async def skill_names_by_session(reader: CliAnalyticsReader | None, session_ids: Sequence[str]) -> dict[str, list[str]]:
    """The Postgres skill names of the sessions, one entry for every session id Postgres knows, an empty list for a
    known session that used nothing, in one batched read bounded by ``PG_SKILL_NAMES_TIMEOUT_S``. ``reader`` is the
    injected one, else ``reader_or_none()``. Empty on any failure, on the timeout or without a reader, so the
    Elasticsearch keys classify alone.

    A session matches only when the Elasticsearch proxy session id equals ``session_dims.session_id``: true for
    sessions with a pinned ``--session-id``, not for Claude Code, whose Elasticsearch id is the CLI-generated
    ``CODEMIE_SESSION_ID``. A miss is not an error: the session has no row, and the Elasticsearch keys stay."""
    if not session_ids:
        return {}
    reader = reader if reader is not None else reader_or_none()
    if reader is None:
        return {}
    try:
        rows = await asyncio.wait_for(reader.get_skill_names_by_session(list(session_ids)), PG_SKILL_NAMES_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - the person view degrades to the Elasticsearch keys
        logger.warning("Postgres skill names unavailable for the person view: %s", type(exc).__name__)
        return {}
    return {str(r["session_id"]): [str(n) for n in r.get("skill_names") or []] for r in rows}


def postgres_names_win(extra: dict[str, dict], names: dict[str, list[str]]) -> dict[str, dict]:
    """`extra` with the skills of each session replaced by its Postgres names. The Elasticsearch keys are the skills
    installed in the working directory, not the used ones, and the classification is first-match-wins, so the two are
    never merged. A session Postgres knows takes its Postgres names even when there are none (it used nothing); only a
    session without a Postgres row keeps its Elasticsearch keys."""
    merged = {sid: dict(info) for sid, info in extra.items()}
    for sid, session_names in names.items():
        merged.setdefault(sid, {})["skills"] = list(dict.fromkeys(session_names))
    return merged
