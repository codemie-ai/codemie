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

"""Helpers for the untrusted child processes the validator starts (the parser and the renderer)."""

from __future__ import annotations

import asyncio
import os
import signal
from contextlib import suppress
from pathlib import Path
from typing import Any

# The sweep stops processes in rounds and kills once QUIET_ROUNDS rounds in a row stop nothing new and see no new
# process created, or after SWEEP_ROUNDS rounds. One quiet read is not enough: a link of a forking chain can live
# as long as one read.
SWEEP_ROUNDS = 20
QUIET_ROUNDS = 3
# /proc/<pid>/stat states of a stopped or traced process.
STOPPED_STATES = (b"T", b"t")
# How often a capture checks whether a spawn in flight has finished, before it sweeps.
SPAWN_SETTLE_PAUSE = 0.01

# Children this process started and waits for itself (asyncio reaps them): reap_orphans never takes their status.
_owned: set[int] = set()


class _Spawning:
    """Children being started right now; their pids are not known yet, so reap_orphans waits for the next round."""

    count = 0


_spawning = _Spawning()


async def spawn(*command: str, **options: Any) -> asyncio.subprocess.Process:
    """``asyncio.create_subprocess_exec``, with the child recorded as one this process waits for itself."""
    _spawning.count += 1
    try:
        process = await asyncio.create_subprocess_exec(*command, **options)
        _owned.add(process.pid)
        return process
    finally:
        _spawning.count -= 1


def release(process: asyncio.subprocess.Process) -> None:
    """Forget a child from ``spawn`` once asyncio has reaped it."""
    _owned.discard(process.pid)


def _process_ids() -> list[int]:
    """Every process that /proc lists; none where /proc is unavailable."""
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []
    return [int(entry) for entry in entries if entry.isdigit()]


def _last_pid() -> int | None:
    """The pid most recently created in this pid namespace (the fifth field of /proc/loadavg); None when unreadable."""
    try:
        return int(Path("/proc/loadavg").read_text().split()[4])
    except (OSError, IndexError, ValueError):
        return None


def _status(pid: int) -> tuple[bytes, int, int] | None:
    """State, parent pid and session id of ``pid`` from /proc; None when it is gone or unreadable.

    The command name in /proc/<pid>/stat can hold spaces and ")", so the fields are read after its last ")".
    """
    try:
        fields = Path(f"/proc/{pid}/stat").read_bytes().rsplit(b")", 1)[1].split()
        return fields[0], int(fields[1]), int(fields[3])
    except (OSError, IndexError, ValueError):
        return None


def _zombie_children() -> list[int]:
    """Children of this process that have exited and wait to be reaped; none where /proc is unavailable."""
    found = []
    for pid in _process_ids():
        status = _status(pid)
        if status is not None and status[0] == b"Z" and status[1] == os.getpid():
            found.append(pid)
    return found


def container_init() -> bool:
    """True when this process is PID 1, so /proc lists only the processes of its own container."""
    return os.getpid() == 1


def _unspared() -> dict[int, bytes]:
    """State of every live process in a session that nothing here started, from a fresh read of /proc."""
    statuses = {pid: status for pid in _process_ids() if pid != os.getpid() and (status := _status(pid))}
    spared = {0, os.getsid(0)}
    for pid in _owned:
        with suppress(OSError):
            spared.add(os.getsid(pid))
    spared.update(session for _, parent, session in statuses.values() if parent == 0)
    return {pid: state for pid, (state, _, session) in statuses.items() if state != b"Z" and session not in spared}


def sweep_strays() -> int:
    """Stop, then kill, every process in a session that nothing here started; returns how many were killed.

    A renderer process that calls ``setsid()`` leaves the renderer's group, so the group kill misses it, and
    ``reap_orphans`` reaps only processes that have exited. A process cannot join another existing session,
    so such a process is always in a session of its own. The sweep spares:

    - the validator's own session;
    - the sessions of the children it waits for (a parser runs in its own session while a capture ends);
    - session 0 and the sessions of processes with parent pid 0: both come from outside the container, such
      as ``kubectl exec``.

    A process that keeps forking a child and exiting is gone before a kill from one read of /proc reaches it,
    and its child was not in that read. So each round reads /proc again and stops (``SIGSTOP``) every such
    process that is not stopped yet; a stopped process cannot fork. A round can still see nothing: a link created
    after the listing of /proc is not in it, and its parent is already a zombie. So a round counts as quiet only
    when it stops nothing and no process was created while it ran (``_last_pid`` did not move; where it cannot be
    read, no round is quiet). When
    ``QUIET_ROUNDS`` rounds in a row are quiet, or after ``SWEEP_ROUNDS`` rounds, every process seen is killed.
    Only a cgroup kill is complete: a chain that outruns every one of the rounds still slips through.

    It runs only as PID 1, where /proc lists this container alone, and does nothing while a ``spawn`` is in
    flight, whose child is not recorded yet (``spawn_settled`` waits for that). Runs on the event loop thread,
    like ``reap_orphans``.
    """
    if _spawning.count or not container_init():
        return 0
    seen: set[int] = set()
    quiet = 0
    last = _last_pid()
    for _ in range(SWEEP_ROUNDS):
        stopped = 0
        for pid, state in _unspared().items():
            seen.add(pid)
            if state in STOPPED_STATES:
                continue
            with suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGSTOP)  # NOSONAR - a process of this container in a session nothing here started
                stopped += 1
        created = _last_pid()
        quiet = 0 if stopped or created is None or created != last else quiet + 1
        last = created
        if quiet == QUIET_ROUNDS:
            break
    killed = 0
    for pid in seen:
        with suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)  # NOSONAR - a process of this container in a session nothing here started
            killed += 1
    return killed


async def spawn_settled() -> None:
    """Wait until no ``spawn`` is in flight, so a sweep can run now.

    No time cap: a spawn ends when its fork and exec end, and a sweep skipped under CPU pressure would leave a
    stray renderer process alive for the pod's lifetime.
    """
    while _spawning.count:
        await asyncio.sleep(SPAWN_SETTLE_PAUSE)


def reap_orphans() -> int:
    """Reap exited children this process did not start; returns how many.

    The validator is PID 1 in its container. When it kills a renderer at the hard limit, Chromium's processes
    are reparented to it; the crashpad handler runs in its own session, so the group kill misses it, and it
    exits by itself once the browser is gone. Without this they stay zombies and use up the pod's PIDs.
    Runs on the event loop thread, so no ``spawn`` can record a child in the middle of a round.
    """
    if _spawning.count:
        return 0
    reaped = 0
    for pid in _zombie_children():
        if pid in _owned:
            continue
        with suppress(ChildProcessError):
            if os.waitpid(pid, os.WNOHANG)[0] == pid:
                reaped += 1
    return reaped


async def drain(stream: asyncio.StreamReader, limit: int) -> bytes:
    """Read ``stream`` to EOF, keeping only its first ``limit`` bytes."""
    kept = bytearray()
    while chunk := await stream.read(64 * 1024):
        if len(kept) < limit:
            kept.extend(chunk[: limit - len(kept)])
    return bytes(kept)


async def exited(process: asyncio.subprocess.Process) -> None:
    """Wait until ``process`` is reaped. ``process.wait()`` also waits for its pipes, which a leftover helper holds."""
    while process.returncode is None:
        await asyncio.sleep(0.05)


def output(task: asyncio.Task[bytes]) -> bytes | None:
    """What a pipe-draining task read, or None when it was abandoned or failed."""
    if not task.done() or task.cancelled() or task.exception() is not None:
        return None
    return task.result()


def kill_group(pid: int) -> None:
    """Kill a child's whole process group, which ``start_new_session=True`` created for that child only."""
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)  # NOSONAR - kills only a group started by this validator
