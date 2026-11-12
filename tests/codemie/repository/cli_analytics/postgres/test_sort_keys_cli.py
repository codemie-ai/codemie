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

"""The argument parsing of the stored sort keys command: a naive time is UTC and a batch is positive."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.repository.cli_analytics.postgres import sort_keys_cli


def _parse(*argv: str):
    return sort_keys_cli._parser().parse_args(list(argv))


def test_a_since_without_an_offset_is_utc_whatever_the_zone_of_the_host(monkeypatch) -> None:
    monkeypatch.setenv("TZ", "Pacific/Auckland")  # far from UTC: a host-local reading would shift the instant
    time.tzset()
    try:
        args = _parse("enable", "--since", "2026-11-05T10:00:00")
    finally:
        monkeypatch.undo()
        time.tzset()

    assert args.since == datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc)


def test_a_since_with_an_offset_is_converted_to_utc() -> None:
    args = _parse("enable", "--since", "2026-11-05T10:00:00+02:00")

    assert args.since == datetime(2026, 11, 5, 8, 0, tzinfo=timezone.utc)
    assert args.since.utcoffset().total_seconds() == 0


@pytest.mark.parametrize("command", ["backfill", "reprice", "enable"])
@pytest.mark.parametrize("batch", ["0", "-5"])
def test_a_batch_of_zero_or_less_is_refused_for_every_command(command: str, batch: str, capsys) -> None:
    argv = [command, "--all"] if command == "enable" else [command]

    with pytest.raises(SystemExit) as exit_info:
        _parse(*argv, "--batch", batch)

    assert exit_info.value.code == 2
    assert "must be greater than 0" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["backfill", "reprice", "enable"])
def test_a_positive_batch_and_the_default_are_accepted(command: str) -> None:
    argv = [command, "--all"] if command == "enable" else [command]

    assert _parse(*argv, "--batch", "250").batch == 250
    assert _parse(*argv).batch == sort_keys_cli.DEFAULT_BATCH


def test_the_default_rate_settings_resolve_to_the_service_callables() -> None:
    from codemie.service.analytics import cli_analytics_jobs

    assert sort_keys_cli.load_rates(sort_keys_cli.DEFAULT_RATES) is cli_analytics_jobs.model_rates_by_name
    assert sort_keys_cli.load_rates(sort_keys_cli.DEFAULT_RATES_VERSION) is cli_analytics_jobs.catalog_rates_version


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["backfill", "reprice"])
async def test_a_command_without_explicit_callables_runs_with_the_default_rates(command: str) -> None:
    from codemie.service.analytics import cli_analytics_jobs

    @asynccontextmanager
    async def _lock(_name):
        yield MagicMock()

    engine = MagicMock()
    engine.advisory_lock = _lock
    result = MagicMock(derived=1, repriced=1, batches=1, duration_s=0.1, wal_bytes=1)
    work = AsyncMock(return_value=result)
    target = "backfill_session_sort_keys" if command == "backfill" else "reprice_cost_keys"

    with (
        patch.object(sort_keys_cli.session_sort_keys, target, work),
        patch.object(cli_analytics_jobs, "catalog_rates_version", return_value="v-default"),
    ):
        text = await sort_keys_cli.run(engine, _parse(command))

    assert text.startswith(f"{command}:")
    _conn, rates, version, _batch = work.await_args.args
    assert rates is cli_analytics_jobs.model_rates_by_name
    assert version == "v-default"


# ── the stored model groups: backfill-groups, enable-groups, disable-groups, status ─────────────────────────────────


def test_backfill_groups_takes_a_batch_a_pause_in_milliseconds_and_an_optional_cap_on_batches() -> None:
    args = _parse("backfill-groups")
    assert (args.batch, args.pause_ms, args.max_batches) == (sort_keys_cli.DEFAULT_BATCH, 50, None)
    args = _parse("backfill-groups", "--batch", "250", "--pause-ms", "2000", "--max-batches", "3")
    assert (args.batch, args.pause_ms, args.max_batches) == (250, 2000, 3)


@pytest.mark.parametrize("option", ["--batch", "--max-batches"])
@pytest.mark.parametrize("value", ["0", "-5"])
def test_backfill_groups_refuses_a_batch_or_a_cap_of_zero_or_less(option: str, value: str, capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        _parse("backfill-groups", option, value)

    assert exit_info.value.code == 2
    assert "must be greater than 0" in capsys.readouterr().err


def test_a_negative_pause_is_refused(capsys) -> None:
    with pytest.raises(SystemExit):
        _parse("backfill-groups", "--pause-ms", "-1")
    assert "must not be negative" in capsys.readouterr().err


def _engine_with_lock(held: bool = False):
    @asynccontextmanager
    async def _lock(_name):
        yield None if held else MagicMock()

    engine = MagicMock()
    engine.advisory_lock = _lock
    return engine


@pytest.mark.asyncio
async def test_backfill_groups_runs_under_the_lock_with_the_pause_in_seconds_and_reports() -> None:
    work = AsyncMock(return_value=MagicMock(derived=7, batches=2, duration_s=0.5, wal_bytes=10))
    args = _parse("backfill-groups", "--batch", "50", "--pause-ms", "250", "--max-batches", "2")

    with patch.object(sort_keys_cli.session_sort_keys, "backfill_model_groups", work):
        text = await sort_keys_cli.run(_engine_with_lock(), args, lambda names: {}, lambda: "v1")

    assert text.startswith("backfill-groups: 7 derived in 2 batches")
    _conn, _rates, version, batch = work.await_args.args
    assert (version, batch) == ("v1", 50)
    assert work.await_args.kwargs["pause_s"] == 0.25 and work.await_args.kwargs["max_batches"] == 2


@pytest.mark.asyncio
async def test_a_command_yields_to_a_job_that_holds_the_lock() -> None:
    assert await sort_keys_cli.run(_engine_with_lock(held=True), _parse("backfill-groups"), None, lambda: "v1") is None


@pytest.mark.asyncio
async def test_backfill_groups_refuses_an_empty_catalog() -> None:
    from codemie.repository.cli_analytics.postgres.rates_version import EMPTY_RATES_VERSION

    with pytest.raises(SystemExit, match="no rates"):
        await sort_keys_cli.run(_engine_with_lock(), _parse("backfill-groups"), None, lambda: EMPTY_RATES_VERSION)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "target"), [("enable-groups", "enable_model_groups"), ("disable-groups", "disable_model_groups")]
)
async def test_enable_and_disable_groups_flip_only_the_groups_switch(command: str, target: str) -> None:
    work = AsyncMock()
    with patch.object(sort_keys_cli.session_sort_keys, target, work):
        text = await sort_keys_cli.run(_engine_with_lock(), _parse(command))

    work.assert_awaited_once()
    assert text.startswith(f"{command}:")


@pytest.mark.asyncio
async def test_status_reports_the_sessions_switch_and_the_groups_line_without_the_lock() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "enabled": True,
            "operator_disabled": False,
            "model_groups_enabled": False,
            "model_groups_operator_disabled": True,
        }
    )

    @asynccontextmanager
    async def _acquire():
        yield conn

    engine = MagicMock()
    engine.acquire = _acquire
    engine.advisory_lock = MagicMock(side_effect=AssertionError("status takes no lock"))

    with (
        patch.object(sort_keys_cli.session_sort_keys, "count_pending_model_groups", AsyncMock(return_value=12)),
        patch.object(sort_keys_cli.session_sort_keys, "count_never_valid_model_groups", AsyncMock(return_value=3)),
    ):
        text = await sort_keys_cli.run(engine, _parse("status"))

    assert "sessions: enabled=True operator_disabled=False" in text
    assert "model groups: pending=12 never_valid=3 enabled=False operator_disabled=True" in text
