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

"""Preview capture in the validator: the renderer child process, its startup self-test and one capture.

The validator itself never runs application code: the sandboxed renderer child does (``renderer.py``).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.manifest import build_manifest
from codemie.core.hosted_applications.validation import candidate_from_result
from codemie.hosted_apps_validator import child_process
from codemie.hosted_apps_validator.child_process import (
    drain,
    exited,
    kill_group,
    output,
    reap_orphans,
    release,
    spawn,
    spawn_settled,
    sweep_strays,
)
from codemie.hosted_apps_validator.contract import (
    CAPTURE_HARD_SECONDS,
    CAPTURE_LOCK_WAIT_SECONDS,
    MAX_CAPTURE_LOCK_WAIT_SECONDS,
)
from codemie.hosted_apps_validator.isolation import SCOPED_ABI, hide_process_secrets
from codemie.hosted_apps_validator.logging_config import LOGGER_NAME
from codemie.core.hosted_applications.binary import dimensions, image
from codemie.core.hosted_applications.preview import MAX_PREVIEW_BYTES, MAX_PREVIEW_SIDE, PREVIEW_NAME

if TYPE_CHECKING:
    from codemie.hosted_apps_validator.server import Workspace

logger = logging.getLogger(LOGGER_NAME)

# The sandboxed renderer child; the validator itself never runs application code.
RENDERER = (sys.executable, "-B", "-m", "codemie.hosted_apps_validator.renderer")
CHILD_ENVIRONMENT = {"PYTHONPATH": str(Path(__file__).parents[2])}
# The renderer prints one short JSON line; anything beyond this is read and discarded.
RENDERER_STDOUT_KEPT = 4096
RENDERER_STDERR_KEPT = 64 * 1024
MAX_PREVIEW_REQUEST = 8192
# build_manifest needs a storage prefix; the capture only reads the manifest's paths and MIME types.
CAPTURE_PREFIX = "preview-capture/manifest"
PREVIEW_KEYS = {"site_root", "entrypoint", "routing_mode"}
# The one optional key: seconds to wait for the renderer.
LOCK_WAIT_KEY = "lock_wait"
ROUTING_MODES = ("static", "spa_history")
# Lines the renderer writes to stderr: exception class names only, never page or exception text.
RENDERER_ERROR = re.compile(rb"renderer: ([A-Za-z_]\w{0,63})")
# Probe report values that describe the node, not checks that must be true.
PROBE_FACTS = ("chromium", "landlock_abi")
# Disk a capture may use in its scratch directory on the shared /work volume (3Gi). Above it the renderer's
# group is killed, so one page cannot fill the volume that every validation in the pod uses.
RENDERER_SCRATCH_BYTES = 512 * 1024 * 1024
SCRATCH_CHECK_SECONDS = 0.25


@dataclass(frozen=True)
class Preview:
    """The captured start-page picture, served as one extra file index after the result's files."""

    path: Path
    size: int


class CaptureRenderer(Protocol):
    """What capture reads from the inspector: the work root, the renderer command and the one-renderer lock."""

    root: Path
    renderer: tuple[str, ...]
    capture_lock: asyncio.Lock


class CaptureSettings(Protocol):
    """What configure_capture reads from and sets on the inspector."""

    root: Path
    renderer: tuple[str, ...]
    capture: bool
    landlock_abi: int | None


def _renderer_errors(stderr: bytes) -> list[str]:
    """The exception class names the renderer reported; every other stderr byte is dropped."""
    return [match.group(1).decode() for match in RENDERER_ERROR.finditer(stderr)]


def _single_line(stdout: bytes) -> Any:
    """The renderer's one JSON line, or None for anything else."""
    lines = stdout.splitlines()
    if len(lines) != 1:
        return None
    try:
        return json.loads(lines[0])
    except ValueError:
        return None


def require_renderer(root: Path, command: tuple[str, ...] = RENDERER) -> tuple[str, int] | None:
    """The Chromium version and the node's Landlock ABI when the renderer's sandbox self-test passes, else None.

    Never raises. An ABI the probe did not report as an integer counts as 0, the unscoped case.
    """
    scratch = root / f"renderer-check-{secrets.token_hex(8)}"
    try:
        scratch.mkdir(mode=0o700)
        process = subprocess.Popen(  # noqa: S603 - the fixed renderer argv, no shell
            [*command, "probe", str(scratch)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=CHILD_ENVIRONMENT,
            start_new_session=True,
            close_fds=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=CAPTURE_HARD_SECONDS)
        except subprocess.TimeoutExpired:
            kill_group(process.pid)
            process.communicate()
            logger.warning("Renderer self-test exceeded its time limit")
            return None
        finally:
            kill_group(process.pid)
        checks = _single_line(stdout)
        version = checks.get("chromium") if isinstance(checks, dict) else None
        passed = all(value is True for name, value in checks.items() if name not in PROBE_FACTS) if version else False
        if process.returncode or not isinstance(version, str) or not passed:
            logger.warning(
                "Renderer self-test failed: returncode=%s errors=%s", process.returncode, _renderer_errors(stderr)
            )
            return None
        abi = checks.get("landlock_abi")
        abi = abi if type(abi) is int else 0
        # Landlock ABI 6 or later (Linux 6.12) also stops the renderer from signalling the validator.
        logger.info("Renderer self-test passed (chromium=%s, landlock_abi=%s)", version, abi)
        return version, abi
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        logger.warning("Renderer self-test failed: %s", type(exc).__name__)
        return None
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _switch(environ: Mapping[str, str], name: str, default: str) -> bool:
    """A ``true``/``false`` setting; anything else refuses to start."""
    value = environ.get(name, default)
    if value not in ("true", "false"):
        raise ValueError(f"{name} must be true or false")
    return value == "true"


def capture_setting(environ: Mapping[str, str]) -> bool:
    """``VALIDATOR_PREVIEW_CAPTURE``: exactly ``true`` (the default) or ``false``; anything else refuses to start."""
    return _switch(environ, "VALIDATOR_PREVIEW_CAPTURE", "true")


def unscoped_setting(environ: Mapping[str, str]) -> bool:
    """``VALIDATOR_PREVIEW_CAPTURE_UNSCOPED``: ``true`` accepts capture below Landlock ABI 6; default ``false``.

    Below ABI 6 (Linux 6.12) a renderer process that escapes Chromium can signal the validator and the parsers.
    """
    return _switch(environ, "VALIDATOR_PREVIEW_CAPTURE_UNSCOPED", "false")


def hide_secrets() -> bool:
    """Hide this process's /proc files from the renderer, which runs as the same user; False when the kernel refuses."""
    try:
        hide_process_secrets()
    except HostedApplicationError as exc:
        logger.warning("Process secrets are not hidden: %s", type(exc).__name__)
        return False
    return True


def configure_capture(
    inspector: CaptureSettings, *, secrets_hidden: bool, environ: Mapping[str, str] = os.environ
) -> str | None:
    """Decide once, at startup, whether this validator captures previews; returns the Chromium version when it does.

    Capture is on only when all of these hold:

    - the setting allows it;
    - this process hides its secrets;
    - the validator is PID 1, so the stray sweep can run after each capture;
    - the renderer self-test passes;
    - the node has Landlock ABI 6 or later, or ``VALIDATOR_PREVIEW_CAPTURE_UNSCOPED`` accepts an older one.

    The self-test's ABI is kept on the inspector for ``/health`` even when capture stays off. Validation keeps
    running when capture is off.
    """
    enabled = capture_setting(environ)
    unscoped = unscoped_setting(environ)
    checked = None
    reason = "setting"
    if enabled:
        reason = "secrets"
        if secrets_hidden:
            reason = "init"
            if child_process.container_init():
                reason = "renderer"
                checked = require_renderer(inspector.root, inspector.renderer)
    if checked is not None:
        inspector.landlock_abi = checked[1]
        if checked[1] < SCOPED_ABI and not unscoped:
            reason, checked = "landlock", None
    inspector.capture = checked is not None
    if reason == "init":
        logger.warning("Preview capture off: the validator is not PID 1 in its container, so no stray sweep can run")
    elif reason == "landlock":
        logger.warning(
            "Preview capture off: Landlock ABI %s is below %s, so the renderer could signal the validator; "
            "VALIDATOR_PREVIEW_CAPTURE_UNSCOPED=true accepts this",
            inspector.landlock_abi,
            SCOPED_ABI,
        )
    if checked is None:
        logger.info("Preview capture off (reason=%s)", reason)
        return None
    logger.info("Preview capture on (chromium=%s, landlock_abi=%s)", checked[0], checked[1])
    return checked[0]


def preview_request(body: bytes) -> tuple[tuple[str, str, str], float]:
    """The site root, entrypoint and routing mode to capture, and the seconds to wait for the renderer.

    The wait is the optional ``lock_wait``: a number (never a boolean) from CAPTURE_LOCK_WAIT_SECONDS to
    MAX_CAPTURE_LOCK_WAIT_SECONDS, and CAPTURE_LOCK_WAIT_SECONDS when absent. Any other body is refused.
    """
    try:
        if len(body) > MAX_PREVIEW_REQUEST:
            raise ValueError
        value = json.loads(body)
        if (
            not isinstance(value, dict)
            or not PREVIEW_KEYS <= set(value) <= PREVIEW_KEYS | {LOCK_WAIT_KEY}
            or not all(isinstance(value[name], str) for name in PREVIEW_KEYS)
            or value["routing_mode"] not in ROUTING_MODES
        ):
            raise ValueError
        lock_wait = value.get(LOCK_WAIT_KEY, CAPTURE_LOCK_WAIT_SECONDS)
        if (
            type(lock_wait) not in (int, float)
            or not CAPTURE_LOCK_WAIT_SECONDS <= lock_wait <= MAX_CAPTURE_LOCK_WAIT_SECONDS
        ):
            raise ValueError
    except (ValueError, RecursionError) as exc:
        raise HostedApplicationError("INVALID_CONFIGURATION", "Invalid preview request", 422) from exc
    return (value["site_root"], value["entrypoint"], value["routing_mode"]), lock_wait


def _capture_job(workspace: Workspace, site_root: str, entrypoint: str, routing_mode: str) -> dict[str, Any] | None:
    """The renderer's job.json: the manifest the gateway would serve, and each asset's parser file; None when
    the configuration names no HTML entrypoint."""
    try:
        manifest = build_manifest(
            candidate_from_result(workspace.result),
            prefix=CAPTURE_PREFIX,
            site_root=site_root,
            entrypoint=entrypoint,
            routing_mode=routing_mode,
        )
        if not manifest["files"][manifest["entrypoint"]]["mime"].startswith("text/html"):
            return None
        local = {item["path"]: path for item, path in zip(workspace.result["files"], workspace.files, strict=True)}
        start = site_root + "/" if site_root else ""
        files = {name: str(local[start + name]) for name in manifest["files"]}
        root = (workspace.path / "parser" / "assets").resolve(strict=True)
    except (HostedApplicationError, KeyError, TypeError, ValueError, OSError):
        return None
    return {"manifest": manifest, "root": str(root), "files": files}


def _disk_bytes(directory: Path) -> int:
    """Disk blocks in use under ``directory``, in bytes; entries that vanish meanwhile count as nothing."""
    used = 0
    for parent, _, names in os.walk(directory):
        for name in names:
            with suppress(OSError):
                used += (Path(parent) / name).lstat().st_blocks * 512
    return used


async def _watch(process: asyncio.subprocess.Process, scratch: Path, key: str) -> None:
    """Wait until the renderer is reaped; return early when its scratch outgrows RENDERER_SCRATCH_BYTES."""
    while process.returncode is None:
        if await asyncio.to_thread(_disk_bytes, scratch) > RENDERER_SCRATCH_BYTES:
            logger.warning("Preview capture %s exceeded its scratch limit", key)
            return
        with suppress(TimeoutError):
            await asyncio.wait_for(exited(process), SCRATCH_CHECK_SECONDS)


async def _render(command: tuple[str, ...], key: str, job: dict[str, Any], scratch: Path) -> Any:
    """Run the renderer on ``job``; its one stdout JSON value, or None. The whole group is dead on return."""
    job_path = scratch / "job.json"
    await asyncio.to_thread(job_path.write_text, json.dumps(job))
    # Capture start (spec section 1): the hard limit counts from here, and the renderer's soft deadline from its
    # own start a moment later. The wait for the renderer lock came before, so it shortens neither limit.
    deadline = time.monotonic() + CAPTURE_HARD_SECONDS
    process = await spawn(
        *command,
        "capture",
        str(job_path),
        str(scratch),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=CHILD_ENVIRONMENT,
        start_new_session=True,
        close_fds=True,
    )
    # Both pipes are read while the renderer runs: a child blocked on a full pipe would never exit.
    stdout_task = asyncio.create_task(drain(process.stdout, RENDERER_STDOUT_KEPT))
    stderr_task = asyncio.create_task(drain(process.stderr, RENDERER_STDERR_KEPT))
    try:
        async with asyncio.timeout(max(0.0, deadline - time.monotonic())):
            await _watch(process, scratch, key)
    except TimeoutError:
        logger.warning("Preview capture %s reached its hard limit", key)
    finally:
        # The whole group, also after a normal exit: a helper the renderer started may still hold its pipes.
        kill_group(process.pid)
        if process.returncode is None:
            await asyncio.shield(exited(process))
        release(process)
        # The group is dead, so its pipes close; a pipe still open after this short wait is abandoned.
        _, pending = await asyncio.shield(asyncio.wait({stdout_task, stderr_task}, timeout=1))
        for task in pending:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    if errors := _renderer_errors(output(stderr_task) or b""):
        logger.info("Preview capture %s renderer errors: %s", key, errors)
    stdout = output(stdout_task)
    return _single_line(stdout) if process.returncode == 0 and stdout is not None else None


def _kept_preview(reported: Any, scratch: Path, target: Path) -> dict[str, Any] | None:
    """Move the renderer's picture to ``target`` when it is exactly what the renderer reported; else None."""
    if not isinstance(reported, dict) or set(reported) != {"size", "sha256", "width", "height"}:
        return None
    size, digest, width, height = (reported[name] for name in ("size", "sha256", "width", "height"))
    if (
        type(size) is not int
        or not 1 <= size <= MAX_PREVIEW_BYTES
        or not isinstance(digest, str)
        or not re.fullmatch("[0-9a-f]{64}", digest)
        or any(type(side) is not int or not 1 <= side <= MAX_PREVIEW_SIDE for side in (width, height))
    ):
        return None
    try:
        data = _read_picture(scratch / PREVIEW_NAME, size)
        if (
            data is None
            or not hmac.compare_digest(hashlib.sha256(data).hexdigest(), digest)
            or not image(data, ".jpg")
            or dimensions(data, ".jpg") != (width, height)
        ):
            return None
        _store_picture(data, target)
    except OSError:
        return None
    return {"size": size, "sha256": digest, "width": width, "height": height}


def _read_picture(picture: Path, size: int) -> bytes | None:
    """The ``size`` bytes of the regular file ``picture``; None for any other file or length.

    A renderer process can still change the scratch directory, so every check is made on one descriptor:
    a symbolic link is not followed, a file with a second hard link is refused, a FIFO does not block the
    open and is refused, and a file that grows is not read past ``size + 1`` bytes.
    """
    descriptor = os.open(picture, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != size:
            return None
        data = bytearray()
        while len(data) <= size and (chunk := os.read(descriptor, size + 1 - len(data))):
            data.extend(chunk)
    finally:
        os.close(descriptor)
    return bytes(data) if len(data) == size else None


def _store_picture(data: bytes, target: Path) -> None:
    """Write the checked bytes to ``target``: a file in scratch can change after the check, so it is not moved."""
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
    except OSError:
        target.unlink(missing_ok=True)
        raise


async def capture(  # noqa: PLR0913 - keyword-only options after the three capture inputs
    key: str,
    workspace: Workspace,
    configuration: tuple[str, str, str],
    *,
    inspector: CaptureRenderer,
    lock_wait: float,
    current: Callable[[], bool],
) -> dict[str, Any] | str:
    """Capture one picture of ``workspace``'s start page and keep it as ``workspace.preview``.

    Returns ``{"index", "size", "sha256", "width", "height"}``, or the reason no picture is kept: ``"busy"`` when
    the renderer stayed taken for ``lock_wait`` seconds, ``"no_picture"`` for every other case. The inspector's
    ``capture_lock`` lets one renderer run at a time; ``current`` tells whether ``workspace`` is still the stored
    result of ``key``.
    """
    root, command, lock = inspector.root, inspector.renderer, inspector.capture_lock
    # Chromium processes of an earlier renderer, killed at its hard limit, have exited by now.
    reap_orphans()
    job = await asyncio.to_thread(_capture_job, workspace, *configuration)
    if job is None:
        return "no_picture"
    try:
        # Bounded by the request, so that this wait plus the hard limit stays inside the API's capture request.
        await asyncio.wait_for(lock.acquire(), lock_wait)
    except TimeoutError:
        logger.info("Preview capture %s skipped: the renderer is busy", key)
        return "busy"
    # Chromium creates UNIX sockets in scratch, and a socket path has at most 108 bytes: a directory inside the
    # 64-character result key is too long. One capture runs at a time, and no key is named "capture".
    scratch = root / "capture"
    target = workspace.path / PREVIEW_NAME
    try:
        # The result must outlive the whole capture, which starts now.
        if not current() or workspace.expires - time.monotonic() < CAPTURE_HARD_SECONDS:
            return "no_picture"
        await asyncio.to_thread(shutil.rmtree, scratch, True)
        scratch.mkdir(mode=0o700)
        reported = await _render(command, key, job, scratch)
        picture = await asyncio.to_thread(_kept_preview, reported, scratch, target)
        if picture is None or not current():
            target.unlink(missing_ok=True)
            return "no_picture"
        workspace.preview = Preview(target, picture["size"])
        return {"index": len(workspace.result["files"]), **picture}
    except OSError as exc:
        logger.warning("Preview capture %s failed: %s", key, type(exc).__name__)
        return "no_picture"
    finally:
        # The lock is freed once the sweep and the scratch cleanup are done, so the next capture never shares
        # either. The callback frees it also when this task is cancelled during the wait.
        cleanup = asyncio.ensure_future(_finish_capture(key, scratch))
        cleanup.add_done_callback(lambda _: _free_renderer(lock))
        await asyncio.shield(cleanup)


async def _finish_capture(key: str, scratch: Path) -> None:
    """Sweep stray renderer processes, then delete the scratch directory; runs while the lock is held.

    Still under the lock, so no new renderer is running: a renderer process that left the renderer's session
    would otherwise live for the pod's lifetime. A parser spawn in flight would make the sweep skip, so the
    capture first waits for it to finish.
    """
    await spawn_settled()
    if killed := sweep_strays():
        logger.warning("Preview capture %s stopped %d stray renderer processes", key, killed)
    await asyncio.to_thread(shutil.rmtree, scratch, True)


def _free_renderer(lock: asyncio.Lock) -> None:
    """Let the next capture start, then reap what the sweep killed."""
    lock.release()
    reap_orphans()
