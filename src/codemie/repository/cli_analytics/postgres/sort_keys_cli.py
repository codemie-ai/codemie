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

"""Operate the stored session sort keys of the Sessions list (EPMCDME-16427, part 2).

    python -m codemie.repository.cli_analytics.postgres.sort_keys_cli backfill [--batch N] [--pause S]
    python -m codemie.repository.cli_analytics.postgres.sort_keys_cli reprice  [--batch N] [--pause S]
    python -m codemie.repository.cli_analytics.postgres.sort_keys_cli enable   (--since TIMESTAMP | --all)
    python -m codemie.repository.cli_analytics.postgres.sort_keys_cli disable
    python -m codemie.repository.cli_analytics.postgres.sort_keys_cli backfill-groups
        [--batch N] [--pause-ms MS] [--max-batches N]
    python -m codemie.repository.cli_analytics.postgres.sort_keys_cli enable-groups
    python -m codemie.repository.cli_analytics.postgres.sort_keys_cli disable-groups
    python -m codemie.repository.cli_analytics.postgres.sort_keys_cli status

`backfill` derives the sessions without stored keys, `reprice` re-derives those priced by another rate set, `disable`
clears the switch and records the operator's intent (the maintenance job never turns the readers on again) and `enable`
turns them on and clears that intent, after nulling the stamps of sessions active since the switch was cleared
(`--since`, ISO 8601, UTC when it has no offset) or of all sessions (`--all`). `--batch` must be greater than 0.
The switch starts off and the maintenance job turns it on by itself once
every session is stamped, so no `enable` is needed to roll out. The job works a time budget per run, so a large table
fills over many runs; this command speeds up the first fill. On a very large table it needs
CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS raised above the 30 s default and a smaller `--batch` (a batch of 1000 sessions
can exceed 30 s on a large `session_dims`). Every command takes the
"maintenance" advisory lock, so it never runs alongside a pod's maintenance job (it exits with 1 when the job holds
it), and writes an audit line with the user, counts, duration and WAL.

The stored model groups (EPMCDME-17051) have their own switch, which governs the readers only (the stamp always
writes the groups). `backfill-groups` derives the sessions whose groups are missing or of another revision, `--batch`
at a time with `--pause-ms` between batches (a quiet-window rollout) and at most `--max-batches` batches when given;
sessions that can never hold groups (an oversized one, or a raw model name above the limit) are skipped.
`disable-groups` turns the readers of the groups off and records the operator's intent (the maintenance job never
turns them on again),
`enable-groups` turns them on and clears it. `status` prints both switches and the groups' pending and never-valid
counts; it reads only and takes no lock. The maintenance job backfills one batch an hour and turns the groups on by
itself once none is pending.

The rates come from the service layer's catalog, which the repository cannot import: `--rates` names the `RatesFor`
callable `module:function` that resolves raw model names as a request does, `--rates-version` the callable that answers
the current catalog version (defaults: the ones the application prices and stamps with).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from codemie.configs.config import config
from codemie.repository.cli_analytics.ports import RatesFor
from codemie.repository.cli_analytics.postgres import session_sort_keys
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.rates_version import EMPTY_RATES_VERSION
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings

logger = logging.getLogger(__name__)

MAINTENANCE_LOCK = "maintenance"
# Names, resolved at run time.
DEFAULT_RATES = "codemie.service.analytics.cli_analytics_jobs:model_rates_by_name"
DEFAULT_RATES_VERSION = "codemie.service.analytics.cli_analytics_jobs:catalog_rates_version"
DEFAULT_BATCH = 1_000
DEFAULT_PAUSE_S = 0.05
DEFAULT_PAUSE_MS = 50
EXIT_LOCKED = 1
_STATUS_TIMEOUT_S = 330.0


def load_rates(spec: str) -> Callable[..., Any]:
    """Resolve `module:function` to the callable; the repository keeps no import of the service layer."""
    module, _, name = spec.partition(":")
    return getattr(importlib.import_module(module), name)


def _positive_int(text: str) -> int:
    """A batch of 0 or less would derive or null nothing and still report success, so it is refused at parse time."""
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be greater than 0, got {value}")
    return value


def _non_negative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"must not be negative, got {value}")
    return value


def _utc_timestamp(text: str) -> datetime:
    """An ISO 8601 timestamp; one without an offset is UTC, not the zone of the host the command runs on."""
    moment = datetime.fromisoformat(text)
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("backfill", "reprice"):
        sub = commands.add_parser(name)
        sub.add_argument("--batch", type=_positive_int, default=DEFAULT_BATCH, help="sessions per transaction")
        sub.add_argument("--pause", type=float, default=DEFAULT_PAUSE_S, help="seconds between batches")
        sub.add_argument("--budget", type=float, default=None, help="stop after this many seconds (default: no limit)")
        sub.add_argument("--rates", default=DEFAULT_RATES, help="module:function resolving raw model names to rates")
        sub.add_argument(
            "--rates-version", default=DEFAULT_RATES_VERSION, help="module:function answering the catalog version"
        )
    enable = commands.add_parser("enable")
    when = enable.add_mutually_exclusive_group(required=True)
    when.add_argument(
        "--since",
        type=_utc_timestamp,
        help="when the switch was cleared (ISO 8601, UTC when it has no offset); sessions active since then are "
        "re-derived (re-enable)",
    )
    when.add_argument("--all", action="store_true", help="re-derive every session")
    enable.add_argument("--batch", type=_positive_int, default=DEFAULT_BATCH)
    commands.add_parser("disable")
    groups = commands.add_parser("backfill-groups")
    groups.add_argument("--batch", type=_positive_int, default=DEFAULT_BATCH, help="sessions per transaction")
    groups.add_argument(
        "--pause-ms", type=_non_negative_int, default=DEFAULT_PAUSE_MS, help="milliseconds between batches"
    )
    groups.add_argument("--max-batches", type=_positive_int, default=None, help="stop after this many batches")
    groups.add_argument("--rates", default=DEFAULT_RATES, help="module:function resolving raw model names to rates")
    groups.add_argument(
        "--rates-version", default=DEFAULT_RATES_VERSION, help="module:function answering the catalog version"
    )
    commands.add_parser("enable-groups")
    commands.add_parser("disable-groups")
    commands.add_parser("status")
    return parser


def _current_rates_version(args: argparse.Namespace, version: Callable[[], str] | None) -> str:
    current = (version or load_rates(args.rates_version))()  # before any rates: see derive_session_sort_keys
    if current == EMPTY_RATES_VERSION:
        raise SystemExit("the catalog has no rates: refusing to price every session at zero")
    return current


async def _price_sessions(conn, args: argparse.Namespace, rates: RatesFor | None, version) -> str:
    """`backfill` and `reprice`: derive or re-price the stored sort keys."""
    current = _current_rates_version(args, version)
    work = (
        session_sort_keys.backfill_session_sort_keys
        if args.command == "backfill"
        else session_sort_keys.reprice_cost_keys
    )
    result = await work(
        conn, rates or load_rates(args.rates), current, args.batch, budget_s=args.budget, pause_s=args.pause
    )
    return (
        f"{args.command}: {result.derived} derived, {result.repriced} re-priced in {result.batches} batches, "
        f"{result.duration_s:.1f}s, WAL {result.wal_bytes} bytes"
    )


async def _backfill_groups(conn, args: argparse.Namespace, rates: RatesFor | None, version) -> str:
    current = _current_rates_version(args, version)
    done = await session_sort_keys.backfill_model_groups(
        conn,
        rates or load_rates(args.rates),
        current,
        args.batch,
        pause_s=args.pause_ms / 1000,
        max_batches=args.max_batches,
    )
    return (
        f"backfill-groups: {done.derived} derived in {done.batches} batches, {done.duration_s:.1f}s, "
        f"WAL {done.wal_bytes} bytes"
    )


async def _switch(conn, args: argparse.Namespace) -> str:
    """The commands that turn the stored readers on or off (`enable` also nulls the stamps)."""
    if args.command == "enable-groups":
        await session_sort_keys.enable_model_groups(conn)
        return "enable-groups: the stored model groups readers are on"
    if args.command == "disable-groups":
        await session_sort_keys.disable_model_groups(conn)
        return "disable-groups: the stored model groups readers are off"
    if args.command == "enable":
        nulled = await session_sort_keys.enable_session_sort_keys(
            conn, None if args.all else args.since, batch=args.batch
        )
        return f"enable: {nulled} stamps nulled, the stored readers are on"
    await session_sort_keys.disable_session_sort_keys(conn)
    return "disable: the stored readers are off"


async def run(
    engine: AnalyticsPgEngine,
    args: argparse.Namespace,
    rates: RatesFor | None = None,
    version: Callable[[], str] | None = None,
) -> str | None:
    """Run one command under the maintenance lock; the audit text, or None when a pod's job holds the lock. `status`
    only reads and takes no lock."""
    if args.command == "status":
        return await _status(engine)
    async with engine.advisory_lock(MAINTENANCE_LOCK) as conn:
        if conn is None:
            return None
        if args.command in ("backfill", "reprice"):
            return await _price_sessions(conn, args, rates, version)
        if args.command == "backfill-groups":
            return await _backfill_groups(conn, args, rates, version)
        return await _switch(conn, args)


async def _status(engine: AnalyticsPgEngine) -> str:
    async with engine.acquire() as conn:
        control = await conn.fetchrow(
            "SELECT bool_and(enabled) AS enabled, bool_or(operator_disabled) AS operator_disabled, "
            "bool_and(model_groups_enabled) AS model_groups_enabled, "
            "bool_or(model_groups_operator_disabled) AS model_groups_operator_disabled "
            "FROM session_sort_keys_control"
        )
        pending = await session_sort_keys.count_pending_model_groups(conn, timeout=_STATUS_TIMEOUT_S)
        never = await session_sort_keys.count_never_valid_model_groups(conn, timeout=_STATUS_TIMEOUT_S)
    return (
        f"status: sessions: enabled={control['enabled']} operator_disabled={control['operator_disabled']}; "
        f"model groups: pending={pending} never_valid={never} enabled={control['model_groups_enabled']} "
        f"operator_disabled={control['model_groups_operator_disabled']}"
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = AnalyticsPgSettings.from_config(config)

    async def go() -> str | None:
        engine = AnalyticsPgEngine(settings)
        try:
            return await run(engine, args)
        finally:
            await engine.close()

    outcome = asyncio.run(go())
    if outcome is None:
        logger.info("cli_analytics: the maintenance job holds the lock; try again when it is done")
        return EXIT_LOCKED
    # The audit line, and the command's report: the application's logging writes to the console.
    logger.info(f"cli_analytics: stored session sort keys by {getpass.getuser()}: {outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
