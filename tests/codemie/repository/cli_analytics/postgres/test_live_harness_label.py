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

"""Harness label of the Session Detail (EPMCDME-16533), against the local analytics Postgres.

The harness comes from `otel_resources.service_name`, reached through the session's earliest log or span; a session
without that row falls back to the `platform` of its hook events (`session_dims.platform`). Each OTel session carries
a resource attribute unique to the test, so its `otel_resources` row is its own: deleting it never touches other
data, and it is removed afterwards on every path. Skipped (not failed) when the local analytics Postgres is
unreachable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.postgres.test_live_turns_daily import _JSON, _Interaction, _Live, _live
from tests.codemie.repository.cli_analytics.support import hook_event_builders as hb

_PRICING = "codemie.service.analytics.handlers.coding_agent_pricing.llm_service"
_DELETE_RESOURCE = "DELETE FROM otel_resources WHERE attrs->>'service.instance.id' = $1"
_DELETE_SESSION_RESOURCE = """
    DELETE FROM otel_resources WHERE resource_id IN (SELECT resource_id FROM log_events WHERE session_id = $1)
"""
_PLATFORM = "claude-code"  # what the CodeMie CLI sends as the `platform` of its hook events


def _resource(service_name: str | None, instance: str) -> dict[str, object]:
    """The resource attributes; `service_name` None leaves `service.name` out altogether."""
    name = [] if service_name is None else [{"key": "service.name", "value": {"stringValue": service_name}}]
    return {
        "attributes": [
            *name,
            {"key": "service.version", "value": {"stringValue": "2.1.0"}},
            {"key": "service.instance.id", "value": {"stringValue": instance}},
        ]
    }


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="milliseconds").replace("+00:00", "Z")


@asynccontextmanager
async def _live_handler(session_id: str) -> AsyncIterator[tuple[_Live, LocalAnalyticsHandler]]:
    """The live database (the session cleaned up afterwards), an empty pricing catalog, the real handler on the reader."""
    async with _live(session_id) as live:
        with (
            patch(f"{_PRICING}.get_all_llm_model_info", return_value=[]),
            patch(f"{_PRICING}.catalog_source", return_value="litellm"),
        ):
            yield live, LocalAnalyticsHandler(PostgresCliAnalyticsReader(live.engine))


def _hook_events(session_id: str, started: datetime, platform: str) -> list[dict[str, object]]:
    """A hooks-only session reduced to its start event: it alone carries the `platform` the harness falls back to."""
    return [
        hb.minimal("agent.session.start", session_id, _iso(started))
        | {"event_id": f"start:{session_id}", "platform": platform},
    ]


async def _ingest_otel(live: _Live, turn: _Interaction, service_name: str | None, instance: str) -> None:
    """The interaction's span and logs under a resource of this service name and this test's unique instance id."""
    resource = _resource(service_name, instance)
    traces = {
        "resourceSpans": [
            {
                "resource": resource,
                "scopeSpans": [{"scope": {"name": "com.anthropic.claude_code.tracing"}, "spans": [turn.span()]}],
            }
        ]
    }
    logs = {
        "resourceLogs": [
            {
                "resource": resource,
                "scopeLogs": [
                    {
                        "scope": {"name": "com.anthropic.claude_code.events"},
                        "logRecords": [turn.user_prompt(), turn.api_request()],
                    }
                ],
            }
        ]
    }
    await live.ingestor.ingest_otlp("traces", json.dumps(traces).encode(), _JSON)
    await live.ingestor.ingest_otlp("logs", json.dumps(logs).encode(), _JSON)
    await live.turns(turn.session_id)  # recompute: the session's cost_daily row is what the detail meta starts from


@asynccontextmanager
async def _otel_session(
    service_name: str | None,
    span_name: str = "claude_code.interaction",
) -> AsyncIterator[tuple[_Live, LocalAnalyticsHandler, PostgresCliAnalyticsReader, str]]:
    """An ingested OTel session under a resource of its own; the resource row is removed afterwards on every path.

    `span_name` decides the platform derived from the span names: a name outside the known prefixes derives none.
    """
    turn = _Interaction(str(uuid.uuid4()), name=span_name)
    instance = f"harness-label-{uuid.uuid4().hex}"
    async with _live_handler(turn.session_id) as (live, handler):
        try:
            await _ingest_otel(live, turn, service_name, instance)
            yield live, handler, PostgresCliAnalyticsReader(live.engine), turn.session_id
        finally:
            async with live.engine.acquire() as conn:
                await conn.execute(_DELETE_RESOURCE, instance)  # a no-op when the test already deleted the row


async def _harness(
    handler: LocalAnalyticsHandler, reader: PostgresCliAnalyticsReader, session_id: str
) -> tuple[str | None, str]:
    meta = await reader.get_session_detail_meta(session_id)
    assert len(meta) == 1
    detail, _, _ = await handler.get_session_detail(session_id)
    assert detail is not None
    return meta[0]["harness_service_name"], detail["harness_name"]


@pytest.mark.asyncio
async def test_codemie_claude_service_name_has_its_label_override() -> None:
    async with _otel_session("codemie-claude") as (live, handler, reader, session_id):
        started = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
        await live.ingest(hooks=_hook_events(session_id, started, _PLATFORM))  # hooks say claude-code ...
        await live.turns(session_id)
        harness = await _harness(handler, reader, session_id)

    # EPMCDME-16533: ... the OTel service name wins, and its label override gives "CodeMie Claude" instead of the
    # generic "Codemie Claude".
    assert harness == ("codemie-claude", "CodeMie Claude")


@pytest.mark.asyncio
async def test_claude_code_service_name_has_its_label_override() -> None:
    async with _otel_session("claude-code") as (_, handler, reader, session_id):
        harness = await _harness(handler, reader, session_id)

    # EPMCDME-16533: the primary OTel path: ("claude-code", "Claude Code").
    assert harness == ("claude-code", "Claude Code")


async def _hooks_only_harness(platform: str) -> tuple[str | None, str]:
    """The harness of a session known only by a hook start event of this `platform` (no OTel resource)."""
    session_id = f"harness-label-hooks-{uuid.uuid4().hex}"
    started = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    # _live_handler patches the pricing catalog even though a hooks-only session has no usage rows:
    # get_session_detail always builds CatalogPricer(), whose constructor reads the real llm_service.catalog_source().
    async with _live_handler(session_id) as (live, handler):
        await live.ingest(hooks=_hook_events(session_id, started, platform))
        await live.turns(session_id)
        return await _harness(handler, PostgresCliAnalyticsReader(live.engine), session_id)


@pytest.mark.asyncio
async def test_hooks_only_session_takes_its_harness_from_the_hook_platform() -> None:
    # EPMCDME-16533: no otel_resources row exists, so the label falls back to session_dims.platform (the hook field).
    assert await _hooks_only_harness(_PLATFORM) == ("claude-code", "Claude Code")


@pytest.mark.asyncio
async def test_hooks_only_session_of_copilot_cli_has_its_label_override() -> None:
    # EPMCDME-16533: copilot-cli has a label override, so it is not humanized into "Copilot Cli".
    assert await _hooks_only_harness("copilot-cli") == ("copilot-cli", "Copilot CLI")


@pytest.mark.asyncio
async def test_hooks_only_session_platform_is_trimmed_before_the_label_lookup() -> None:
    # EPMCDME-16533: the padding of the hook platform is trimmed in SQL, so the override still applies.
    assert await _hooks_only_harness("  claude-code ") == ("claude-code", "Claude Code")
    assert await _hooks_only_harness("\t\u00a0claude-code \u00a0") == ("claude-code", "Claude Code")  # tab and NBSP


@pytest.mark.asyncio
async def test_hooks_only_session_with_a_blank_platform_has_no_harness() -> None:
    # EPMCDME-16533: a whitespace-only platform is no platform: no raw harness and the "Unknown" label.
    assert await _hooks_only_harness("   ") == (None, "Unknown")
    assert await _hooks_only_harness("\t\u00a0") == (None, "Unknown")  # tab and NBSP only


@pytest.mark.asyncio
async def test_otel_session_whose_resource_row_is_gone_falls_back_to_the_session_platform() -> None:
    async with _otel_session("claude-code") as (live, handler, reader, session_id):
        assert await _harness(handler, reader, session_id) == ("claude-code", "Claude Code")  # before the purge
        async with live.engine.acquire() as conn:
            await conn.execute(_DELETE_SESSION_RESOURCE, session_id)
        harness = await _harness(handler, reader, session_id)

    # EPMCDME-16533: an OTel-only session (no hook event) still has session_dims.platform 'claude-code', derived from
    # its `claude_code.` span names, so the purge of otel_resources no longer turns the label into "Unknown".
    assert harness == ("claude-code", "Claude Code")


@pytest.mark.asyncio
async def test_otel_service_name_wins_over_a_different_hook_platform() -> None:
    async with _otel_session("claude-code") as (live, handler, reader, session_id):
        started = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
        await live.ingest(hooks=_hook_events(session_id, started, "copilot-cli"))
        await live.turns(session_id)
        harness = await _harness(handler, reader, session_id)

    # EPMCDME-16533: the two sources differ and both exist, so the OTel service name is what decides.
    assert harness == ("claude-code", "Claude Code")


@pytest.mark.asyncio
@pytest.mark.parametrize("service_name", [None, "", "   "], ids=["absent", "empty", "blank"])
async def test_resource_without_a_usable_service_name_falls_back_to_the_hook_platform(service_name: str | None) -> None:
    async with _otel_session(service_name) as (live, handler, reader, session_id):
        started = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
        await live.ingest(hooks=_hook_events(session_id, started, "copilot-cli"))
        await live.turns(session_id)
        harness = await _harness(handler, reader, session_id)

    # EPMCDME-16533: the resource row exists, but its service.name is no name: the platform decides.
    assert harness == ("copilot-cli", "Copilot CLI")


@pytest.mark.asyncio
@pytest.mark.parametrize("service_name", [None, "", "   "], ids=["absent", "empty", "blank"])
async def test_resource_without_a_usable_service_name_and_no_platform_has_no_harness(service_name: str | None) -> None:
    # The span name carries none of the known client prefixes, so no platform is derived from it either.
    async with _otel_session(service_name, span_name="other.interaction") as (live, handler, reader, session_id):
        harness = await _harness(handler, reader, session_id)

    # EPMCDME-16533: no evidence of the harness at all: no raw name and the "Unknown" label.
    assert harness == (None, "Unknown")


@pytest.mark.asyncio
async def test_earliest_resource_with_a_usable_service_name_wins_over_an_earlier_blank_one() -> None:
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    blank_first = _Interaction(session_id, ts=now - timedelta(minutes=40))
    named_later = _Interaction(session_id, ts=now - timedelta(minutes=20))
    instances = [f"harness-label-{uuid.uuid4().hex}" for _ in range(2)]
    async with _live_handler(session_id) as (live, handler):
        try:
            await _ingest_otel(live, blank_first, "   ", instances[0])
            await _ingest_otel(live, named_later, "codemie-claude", instances[1])
            harness = await _harness(handler, PostgresCliAnalyticsReader(live.engine), session_id)
        finally:
            async with live.engine.acquire() as conn:
                for instance in instances:
                    await conn.execute(_DELETE_RESOURCE, instance)

    # EPMCDME-16533: the earliest resource has a blank name; the later named one is used, not the platform
    # ("claude-code", derived from the span names) and not "Unknown".
    assert harness == ("codemie-claude", "CodeMie Claude")


@pytest.mark.asyncio
async def test_a_later_blank_platform_does_not_replace_the_valid_one() -> None:
    session_id = f"harness-label-hooks-{uuid.uuid4().hex}"
    started = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    later = hb.minimal("agent.session.end", session_id, _iso(started + timedelta(minutes=10)))
    async with _live_handler(session_id) as (live, handler):
        await live.ingest(
            hooks=[*_hook_events(session_id, started, _PLATFORM), later | {"event_id": "end", "platform": "   "}]
        )
        await live.turns(session_id)
        harness = await _harness(handler, PostgresCliAnalyticsReader(live.engine), session_id)

    # EPMCDME-16533: the whitespace-only platform of the later event is no value, so the valid one is kept.
    assert harness == ("claude-code", "Claude Code")


@pytest.mark.asyncio
async def test_a_platform_longer_than_200_characters_is_stored_cut_to_200() -> None:
    session_id = f"harness-label-hooks-{uuid.uuid4().hex}"
    started = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    async with _live_handler(session_id) as (live, _):
        await live.ingest(hooks=_hook_events(session_id, started, "x" * 250))
        await live.turns(session_id)
        async with live.engine.acquire() as conn:
            platform = await conn.fetchval("SELECT platform FROM session_dims WHERE session_id = $1", session_id)

    assert platform == "x" * 200
