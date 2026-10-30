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

"""Value-level checks of session_dims.first_prompt against the local analytics Postgres.

Hook events go through the real ingest; only the test's own rollup keys are recomputed, and only its own
sessions are deleted afterwards. Skipped (not failed) when the local analytics Postgres is unreachable.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres import rollups
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.rollups import RollupRefresher
from codemie.repository.cli_analytics.vocabulary import strip_pasted_content
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.postgres.test_live_turns_daily import (
    _EMAIL,
    _EPOCH,
    _READ_KEYS,
    _Live,
    _live,
)
from tests.codemie.repository.cli_analytics.support import hook_event_builders as hb
from tests.codemie.repository.cli_analytics.test_vocabulary import PARITY_CORPUS

_READ_FIRST_PROMPT = "SELECT first_prompt FROM session_dims WHERE session_id = $1"
_BASE = (datetime.now(timezone.utc) - timedelta(minutes=30)).replace(microsecond=0)


def _ts(seconds: int) -> str:
    return (_BASE + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _start(session_id: str) -> dict[str, object]:
    return hb.minimal("agent.session.start", session_id, _ts(0)) | {"event_id": f"start:{session_id}"}


def _prompt(session_id: str, seconds: int, body: str, **extra: object) -> dict[str, object]:
    return hb.minimal("agent.prompt.submit", session_id, _ts(seconds)) | {
        "event_id": f"prompt:{session_id}:{seconds}",
        "prompt_id": f"p{seconds}",
        "prompt_body": body,
        **extra,
    }


async def _first_prompt(live: _Live, session_id: str, *events: dict[str, object]) -> str | None:
    """Ingest the events, recompute the session's queued keys and read its first_prompt."""
    received_ns = (datetime.now(timezone.utc) - _EPOCH) // timedelta(microseconds=1) * 1000
    await live.ingestor.ingest_hook_events(list(events), _EMAIL, received_ns)
    async with live.engine.acquire() as conn:
        keys = await conn.fetch(_READ_KEYS, [session_id])
        assert keys, "ingest marked no rollup key"
        await RollupRefresher(live.engine, batch_size=len(keys)).recompute(conn, keys)
        return await conn.fetchval(_READ_FIRST_PROMPT, session_id)


def _session() -> str:
    return f"first-prompt-{uuid.uuid4().hex[:12]}"


_PASTE = '<pasted_content id="b23a">\nStep 1: log in\nStep 2: open Control Plane\n</pasted_content id="b23a">\n'


@pytest.mark.asyncio
async def test_parity_sql_matches_python_over_the_corpus() -> None:
    async with _live() as live, live.engine.acquire() as conn:
        for text in PARITY_CORPUS:
            cleaned = await conn.fetchval("SELECT " + rollups._clean_prompt("$1::text"), text)
            assert cleaned == strip_pasted_content(text), text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param("Run the prod smoke test", "Run the prod smoke test", id="typed"),
        pytest.param(_PASTE, "Step 1: log in\nStep 2: open Control Plane", id="pasted-only"),
        pytest.param(
            "Perform a complete smoke test\n\n" + _PASTE,
            "Perform a complete smoke test\n\n\nStep 1: log in\nStep 2: open Control Plane",
            id="typed+pasted",
        ),
        pytest.param(
            'Fix this: <pasted_content id="m">err log</pasted_content id="m"> please',
            "Fix this: err log please",
            id="mid-prompt",
        ),
        pytest.param(
            '<pasted_content id="a">one</pasted_content id="a"> and <pasted_content id="b">two</pasted_content id="b">',
            "one and two",
            id="multiple-blocks",
        ),
        pytest.param(
            '<pasted_content id="b23a">\nunterminated text with no close',
            "unterminated text with no close",
            id="unterminated",
        ),
        pytest.param('text</pasted_content id="zz">', "text", id="orphan-close"),
        pytest.param('Check <pasted_content id="trunc', "Check", id="attribute-truncated"),
    ],
)
async def test_first_prompt_is_the_prompt_without_the_pasted_content_tags(body: str, expected: str) -> None:
    session_id = _session()
    async with _live(session_id) as live:
        assert await _first_prompt(live, session_id, _start(session_id), _prompt(session_id, 1, body)) == expected


@pytest.mark.asyncio
async def test_a_sentinel_wrapped_in_a_paste_block_is_skipped_and_the_next_prompt_wins() -> None:
    session_id = _session()
    wrapped = '<pasted_content id="a">\n/clear\n</pasted_content id="a">'
    async with _live(session_id) as live:
        first = await _first_prompt(
            live, session_id, _start(session_id), _prompt(session_id, 1, wrapped), _prompt(session_id, 2, "real prompt")
        )
    assert first == "real prompt"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            '<pasted_content id="x">This session is being continued from a previous conversation that ran out of '
            "context.</pasted_content>",
            id="continuation",
        ),
        pytest.param(
            '<pasted_content id="x">Caveat: The messages below were generated by the user while running local '
            "commands.</pasted_content>",
            id="caveat",
        ),
    ],
)
async def test_a_raw_prefix_prompt_wrapped_in_a_paste_block_is_skipped_and_the_next_prompt_wins(body: str) -> None:
    session_id = _session()
    async with _live(session_id) as live:
        first = await _first_prompt(
            live, session_id, _start(session_id), _prompt(session_id, 1, body), _prompt(session_id, 2, "real prompt")
        )
    assert first == "real prompt"


@pytest.mark.asyncio
async def test_a_first_prompt_that_cleans_to_empty_is_skipped_and_the_next_prompt_wins() -> None:
    session_id = _session()
    async with _live(session_id) as live:
        first = await _first_prompt(
            live,
            session_id,
            _start(session_id),
            _prompt(session_id, 1, '<pasted_content id="a">'),
            _prompt(session_id, 2, "later prompt"),
        )
    assert first == "later prompt"


@pytest.mark.asyncio
async def test_only_prompts_that_clean_to_empty_leave_first_prompt_unset() -> None:
    session_id = _session()
    async with _live(session_id) as live:
        first = await _first_prompt(
            live, session_id, _start(session_id), _prompt(session_id, 1, '<pasted_content id="a">')
        )
    assert first is None


@pytest.mark.asyncio
async def test_a_recompute_whose_only_earlier_prompt_cleans_to_empty_keeps_the_stored_first_prompt() -> None:
    session_id = _session()
    async with _live(session_id) as live:
        stored = await _first_prompt(live, session_id, _start(session_id), _prompt(session_id, 5, "stored prompt"))
        assert stored == "stored prompt"
        after = await _first_prompt(live, session_id, _prompt(session_id, 1, '<pasted_content id="a">'))
    assert after == "stored prompt"


@pytest.mark.asyncio
async def test_old_and_new_cli_events_both_roll_up() -> None:
    old, new = _session(), _session()
    async with _live(old, new) as live:
        from_old = await _first_prompt(live, old, _start(old), _prompt(old, 1, _PASTE))
        from_new = await _first_prompt(
            live,
            new,
            _start(new),
            _prompt(new, 1, _PASTE, prompt_original="Step 1: log in\nStep 2: open Control Plane"),
        )
    assert from_old == from_new == "Step 1: log in\nStep 2: open Control Plane"


_READ_SESSION_TITLE = "SELECT session_title, attrs FROM session_dims WHERE session_id = $1"


def _session_start(session_id: str, seconds: int, **extra: object) -> dict[str, object]:
    return hb.minimal("agent.session.start", session_id, _ts(seconds)) | {
        "event_id": f"start:{session_id}:{seconds}",
        **extra,
    }


async def _session_title(live: _Live, session_id: str, *events: dict[str, object]) -> tuple[str | None, object]:
    """Ingest the events, recompute the session's queued keys and read its session_title and attrs."""
    received_ns = (datetime.now(timezone.utc) - _EPOCH) // timedelta(microseconds=1) * 1000
    await live.ingestor.ingest_hook_events(list(events), _EMAIL, received_ns)
    async with live.engine.acquire() as conn:
        keys = await conn.fetch(_READ_KEYS, [session_id])
        assert keys, "ingest marked no rollup key"
        await RollupRefresher(live.engine, batch_size=len(keys)).recompute(conn, keys)
        row = await conn.fetchrow(_READ_SESSION_TITLE, session_id)
    assert row is not None
    return row["session_title"], row["attrs"]


@pytest.mark.asyncio
async def test_session_title_is_set_from_the_session_start_event() -> None:
    session_id = _session()
    async with _live(session_id) as live:
        title, _ = await _session_title(live, session_id, _session_start(session_id, 0, session_title="My name"))
    assert title == "My name"


@pytest.mark.asyncio
async def test_the_latest_session_title_wins() -> None:
    session_id = _session()
    async with _live(session_id) as live:
        title, _ = await _session_title(
            live,
            session_id,
            _session_start(session_id, 0, session_title="My name"),
            _session_start(session_id, 5, session_title="Renamed"),
        )
    assert title == "Renamed"


@pytest.mark.asyncio
async def test_a_300_character_session_title_is_stored_as_200() -> None:
    session_id = _session()
    async with _live(session_id) as live:
        title, _ = await _session_title(live, session_id, _session_start(session_id, 0, session_title="x" * 300))
    assert title == "x" * 200


@pytest.mark.asyncio
async def test_a_session_title_is_trimmed_of_spaces_tabs_and_line_breaks() -> None:
    session_id = _session()
    async with _live(session_id) as live:
        title, _ = await _session_title(
            live, session_id, _session_start(session_id, 0, session_title="  \n\t My name \r\n ")
        )
    assert title == "My name"


@pytest.mark.asyncio
async def test_a_recompute_from_events_without_a_name_keeps_the_stored_session_title() -> None:
    session_id = _session()
    async with _live(session_id) as live:
        stored, _ = await _session_title(live, session_id, _session_start(session_id, 0, session_title="My name"))
        assert stored == "My name"
        after, _ = await _session_title(live, session_id, _prompt(session_id, 3, "a prompt"))
    assert after == "My name"


@pytest.mark.asyncio
async def test_a_whitespace_only_latest_session_title_keeps_the_earlier_real_name() -> None:
    session_id = _session()
    async with _live(session_id) as live:
        title, _ = await _session_title(
            live,
            session_id,
            _session_start(session_id, 0, session_title="My name"),
            _session_start(session_id, 5, session_title=" \n\t "),
        )
    assert title == "My name"


@pytest.mark.asyncio
async def test_a_no_break_space_latest_session_title_keeps_the_earlier_real_name() -> None:
    session_id = _session()
    async with _live(session_id) as live:
        title, _ = await _session_title(
            live,
            session_id,
            _session_start(session_id, 0, session_title="Release plan"),
            _session_start(session_id, 5, session_title="\u00a0"),
        )
    assert title == "Release plan"


@pytest.mark.asyncio
async def test_session_title_stays_out_of_session_dims_attrs() -> None:
    session_id = _session()
    async with _live(session_id) as live:
        title, attrs = await _session_title(live, session_id, _session_start(session_id, 0, session_title="My name"))
    assert title == "My name"
    assert "session_title" not in json.loads(attrs or "{}")


_PRICING = "codemie.service.analytics.handlers.coding_agent_pricing.llm_service"
_TYPED = "Run the prod smoke test"
_STEPS = "Step 1: log in\nStep 2: open Control Plane"


async def _served(
    session_id: str, *events: dict[str, object], stored_first_prompt: str | None = None
) -> tuple[str, str, str]:
    """The `prompt` the sessions list and the session detail serve, plus the session's stored first_prompt."""
    email = f"{session_id}@example.com"
    window = LocalAnalyticsFilter(
        start_dt=_BASE.replace(tzinfo=None) - timedelta(hours=1),
        end_dt=_BASE.replace(tzinfo=None) + timedelta(hours=1),
        users=[email],
    )
    received_ns = (datetime.now(timezone.utc) - _EPOCH) // timedelta(microseconds=1) * 1000
    usage = hb.usage_request(
        session_id=session_id, timestamp=_ts(2), event_id=f"usage:{session_id}", request_id=f"req-{session_id}"
    )
    async with _live(session_id) as live:
        # The list serves sessions that have cost rows, so the session needs one usage request.
        await live.ingestor.ingest_hook_events([*events, usage], email, received_ns)
        async with live.engine.acquire() as conn:
            keys = await conn.fetch(_READ_KEYS, [session_id])
            assert keys, "ingest marked no rollup key"
            await RollupRefresher(live.engine, batch_size=len(keys)).recompute(conn, keys)
            if stored_first_prompt is not None:
                await conn.execute(
                    "UPDATE session_dims SET first_prompt = $2 WHERE session_id = $1", session_id, stored_first_prompt
                )
            stored = await conn.fetchval(_READ_FIRST_PROMPT, session_id)
        with (
            patch(f"{_PRICING}.get_all_llm_model_info", return_value=[]),
            patch(f"{_PRICING}.catalog_source", return_value="litellm"),
        ):
            handler = LocalAnalyticsHandler(PostgresCliAnalyticsReader(live.engine))
            sessions, _, _ = await handler.get_sessions(window, 0, 20, "cost_usd", None)
            row = next(s for s in sessions["sessions"] if s["trace_id"] == session_id)
            detail, _, _ = await handler.get_session_detail(session_id)
    return row["prompt"], detail["prompt"], stored


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(_TYPED, _TYPED, id="typed"),
        pytest.param(_PASTE, _STEPS, id="pasted-only"),
        pytest.param("Smoke test\n\n" + _PASTE, "Smoke test\n\n\n" + _STEPS, id="typed+pasted"),
    ],
)
async def test_the_sessions_list_and_detail_serve_the_first_prompt_without_the_tags(body: str, expected: str) -> None:
    session_id = _session()
    listed, detailed, _ = await _served(session_id, _start(session_id), _prompt(session_id, 1, body))
    assert listed == detailed == expected


@pytest.mark.asyncio
async def test_a_named_session_is_served_under_its_name_in_the_list_and_the_detail() -> None:
    session_id = _session()
    listed, detailed, stored = await _served(
        session_id,
        _session_start(session_id, 0, session_title="Release plan"),
        _prompt(session_id, 1, _PASTE),
    )
    assert listed == detailed == "Release plan"
    assert stored == _STEPS


@pytest.mark.asyncio
async def test_a_no_break_space_latest_title_leaves_the_earlier_name_served() -> None:
    session_id = _session()
    listed, detailed, _ = await _served(
        session_id,
        _session_start(session_id, 0, session_title="Release plan"),
        _session_start(session_id, 5, session_title="\u00a0"),
        _prompt(session_id, 1, _TYPED),
    )
    assert listed == detailed == "Release plan"


@pytest.mark.asyncio
async def test_a_stored_first_prompt_that_still_holds_the_markup_is_served_clean() -> None:
    session_id = _session()
    listed, detailed, stored = await _served(
        session_id,
        _start(session_id),
        _prompt(session_id, 1, _TYPED),
        stored_first_prompt="Smoke test\n\n" + _PASTE,
    )
    assert "pasted_content" in stored
    assert listed == detailed == "Smoke test\n\n\n" + _STEPS
