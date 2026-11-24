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


"""Preview renderer: takes one JPEG of an uploaded app's start page in headless Chromium, in its own sandbox.

The validator starts this module as a child process in a new process group::

    python -m codemie.hosted_apps_validator.renderer capture <job.json> <scratch>
    python -m codemie.hosted_apps_validator.renderer probe <scratch>

``job.json`` is read before the sandbox applies. It holds one JSON object::

    {
      "manifest": {...},                      # the dict that build_manifest produces; route() uses it
      "root": "/abs/dir",                     # the one directory the sandbox makes readable
      "files": {"<asset path>": "/abs/dir/<file>"}   # manifest asset path -> file inside root
    }

Every file path must resolve inside ``root``; anything else gives no preview. The browser path is
fixed here (``BROWSER``) and never comes from the job.

Standard output is exactly one JSON line. ``capture`` prints ``{"size", "sha256", "width", "height"}``
and leaves ``<scratch>/preview.jpg`` as the only new entry in scratch, or prints ``null`` and leaves
nothing new. ``capture`` exits 0 in both cases. ``probe`` prints ``{"<check>": bool, ..., "chromium":
"<version>" | null}`` and exits 0 only when every check is true and the version is known, else 1.
A usage error prints ``null`` and exits 2. Standard error carries exception class names only.
"""

from __future__ import annotations

import base64
import contextlib
import ctypes
import errno
import hashlib
import json
import os
import select
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

# urlsplit imports unicodedata lazily for a non-ASCII host; after the sandbox Python cannot read it.
import unicodedata  # noqa: F401
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from codemie.core.hosted_applications.binary import dimensions, image
from codemie.core.hosted_applications.content_policy import CONTENT_CSP, RESERVED_PREFIX
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.manifest import resolve_asset
from codemie.core.hosted_applications.paths import decode_content_path
from codemie.core.hosted_applications.preview import MAX_PREVIEW_BYTES, MAX_PREVIEW_SIDE, PREVIEW_NAME
from codemie.hosted_apps_validator.contract import (
    CAPTURE_HARD_SECONDS,
    CAPTURE_SOFT_SECONDS,
    PREVIEW_VIEWPORT,
)
from codemie.hosted_apps_validator.isolation import enforce_renderer_sandbox, landlock_abi

ORIGIN = "https://app.preview.invalid"
# Status that means "refuse the request" (Fetch.failRequest). It is not an HTTP status.
FAIL = 0
_HOST = "app.preview.invalid"
_HEADERS = {
    "Content-Security-Policy": CONTENT_CSP,
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}


def _origin_path(url: str) -> bytes | None:
    """The raw path of an origin URL, or None for any other origin, scheme, port or userinfo."""
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != _HOST:
            return None
    except ValueError:
        return None
    return (parts.path or "/").encode("utf-8", errors="surrogateescape")


def route(
    manifest: dict[str, Any], files: dict[str, Path], url: str, *, navigation: bool = False
) -> tuple[int, dict[str, str], Path | None]:
    """Answer one request: (status, headers, file to serve), or (FAIL, {}, None) to refuse it.

    ``navigation`` is True for a document navigation (Task 5 passes it for the Document
    resource type); it enables SPA history fallback exactly as the gateway does.
    """
    raw = _origin_path(url)
    if raw is None:
        return FAIL, {}, None
    try:
        path = decode_content_path(raw)
        if path.partition("/")[0].casefold() == RESERVED_PREFIX:
            raise HostedApplicationError("FILE_NOT_FOUND", "File unavailable", 404)
        asset = resolve_asset(manifest, path, html_navigation=navigation)
    except (HostedApplicationError, ValueError):
        return 404, dict(_HEADERS), None
    local = files.get(asset["path"])
    if local is None:
        return 404, dict(_HEADERS), None
    return 200, _HEADERS | {"Content-Type": asset["mime"]}, local


BROWSER = Path("/usr/lib/chromium/chromium")
# V8 old-generation heap per page, in MiB: a page that allocates without bound fails alone, well before the
# 3Gi validator container that runs every validation in the pod reaches its memory limit.
RENDERER_HEAP_MIB = 512
# Spec section 2: the fixed flag set and the heap cap. The viewport is set over CDP, so no --window-size.
FLAGS = (
    "--headless",
    "--remote-debugging-pipe",
    "--no-sandbox",
    "--no-zygote",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--mute-audio",
    f"--js-flags=--max-old-space-size={RENDERER_HEAP_MIB}",
)
# Each served file is read into memory and sent base64-encoded over the pipe; a larger file fails its request.
MAX_SERVED_FILE_BYTES = 32 * 1024 * 1024
QUALITIES = (80, 60, 40)
PROBE_CHECKS = (
    "sandbox",
    "ip_socket_denied",
    "unix_socket_allowed",
    "outside_read_denied",
    "outside_write_denied",
    "environment_clean",
    "shell_exec_denied",
    "parent_environ_denied",
)
_PROFILE = "profile"
_SETTLE_SECONDS = 0.5
_POLL_SECONDS = 0.05
_CLOSE_SECONDS = 2.0
_REAP_ROUNDS = 10
_PR_SET_CHILD_SUBREAPER = 36
_DENIED = frozenset({errno.EACCES, errno.EPERM})
# Runs in a separate browser context on about:blank, so the captured page cannot change the answer.
_UNIFORM_SCRIPT = """async (data) => {
  const bytes = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
  const bitmap = await createImageBitmap(new Blob([bytes], {type: "image/jpeg"}));
  const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
  const context = canvas.getContext("2d");
  context.drawImage(bitmap, 0, 0);
  const pixels = context.getImageData(0, 0, bitmap.width, bitmap.height).data;
  for (let i = 4; i < pixels.length; i += 1) {
    if (Math.abs(pixels[i] - pixels[i & 3]) > 2) return false;
  }
  return true;
}"""


@dataclass(frozen=True)
class Job:
    manifest: dict[str, Any]
    root: Path
    files: dict[str, Path]


def load_job(path: Path) -> Job:
    """Read ``job.json``; a malformed job or a file outside ``root`` raises ValueError."""
    try:
        body = json.loads(path.read_bytes())
    except OSError as exc:
        raise ValueError("The job is unreadable") from exc
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("manifest"), dict)
        or not isinstance(body.get("root"), str)
        or not isinstance(body.get("files"), dict)
        or not Path(body["root"]).is_absolute()
    ):
        raise ValueError("The job is malformed")
    try:
        root = Path(body["root"]).resolve(strict=True)
        files = {}
        for name, value in body["files"].items():
            if not isinstance(value, str):
                raise ValueError("The job is malformed")
            local = Path(value).resolve(strict=True)
            if not local.is_relative_to(root) or not local.is_file():
                raise ValueError("A job file is outside the root")
            files[name] = local
    except OSError as exc:
        raise ValueError("A job file is unavailable") from exc
    if not root.is_dir():
        raise ValueError("The job root is not a directory")
    return Job(body["manifest"], root, files)


def describe(data: bytes) -> dict[str, int | str] | None:
    """The result line for a picture, or None when it is too big or not a usable JPEG."""
    size = dimensions(data, ".jpg")
    if (
        len(data) > MAX_PREVIEW_BYTES
        or not image(data, ".jpg")
        or not all(0 < side <= MAX_PREVIEW_SIDE for side in size)
    ):
        return None
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "width": size[0], "height": size[1]}


def encode(shoot: Callable[[int], bytes]) -> bytes | None:
    """The first picture within MAX_PREVIEW_BYTES from ``shoot(quality)`` at quality 80, 60, then 40."""
    for quality in QUALITIES:
        data = shoot(quality)
        if len(data) <= MAX_PREVIEW_BYTES:
            return data
    return None


class CdpError(Exception):
    """Chromium refused a command, closed its pipe, or did not answer before the hard limit."""


class Browser:
    """One headless Chromium over --remote-debugging-pipe: commands go to its fd 3, replies come from its fd 4.

    Messages are NUL-terminated JSON. Every wait ends at ``deadline`` (the hard limit).
    """

    def __init__(self, profile: Path, deadline: float, on_event: Callable[[dict[str, Any]], None]):
        self.deadline = deadline
        self._on_event = on_event
        self._buffer = b""
        self._messages: deque[dict[str, Any]] = deque()
        self._last_id = 0
        commands_read, self._commands = os.pipe()
        self._replies, replies_write = os.pipe()

        def remap() -> (
            None
        ):  # In the child before exec. Both ends are duplicated first, so neither overwrites the other.
            commands, replies = os.dup(commands_read), os.dup(replies_write)
            os.dup2(commands, 3)
            os.dup2(replies, 4)

        try:
            # No env: the child inherits the sandbox's cleared environment (TMPDIR only).
            self.process = subprocess.Popen(  # noqa: S603 - the fixed BROWSER path and flags, no shell
                [str(BROWSER), *FLAGS, f"--user-data-dir={profile}", "about:blank"],
                cwd=profile.parent,
                pass_fds=(3, 4),
                # The renderer runs no threads; remap only moves the CDP pipe ends to fds 3 and 4 before exec.
                preexec_fn=remap,  # noqa: PLW1509
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except BaseException:
            os.close(self._commands)
            os.close(self._replies)
            raise
        finally:
            os.close(commands_read)
            os.close(replies_write)

    def send(self, method: str, params: dict[str, Any] | None = None, session: str | None = None) -> int:
        self._last_id += 1
        message = {"id": self._last_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        view = memoryview(json.dumps(message).encode() + b"\0")
        while view:
            view = view[os.write(self._commands, view) :]
        return self._last_id

    def _receive(self, timeout: float) -> dict[str, Any] | None:
        until = time.monotonic() + timeout
        while not self._messages:
            left = until - time.monotonic()
            if left <= 0 or not select.select([self._replies], [], [], left)[0]:
                return None
            chunk = os.read(self._replies, 1 << 20)
            if not chunk:
                raise CdpError("Chromium closed its pipe")
            *complete, self._buffer = (self._buffer + chunk).split(b"\0")
            self._messages.extend(json.loads(raw) for raw in complete)
        return self._messages.popleft()

    def call(self, method: str, params: dict[str, Any] | None = None, session: str | None = None) -> dict[str, Any]:
        """Send one command and return its result; events that arrive meanwhile go to ``on_event``."""
        ident = self.send(method, params, session)
        while True:
            message = self._receive(self.deadline - time.monotonic())
            if message is None:
                raise CdpError("No answer before the hard limit")
            if message.get("id") == ident:
                if "error" in message:
                    raise CdpError("Chromium refused a command")
                return message.get("result", {})
            self._on_event(message)

    def pump(self, timeout: float) -> None:
        """Handle at most one event, waiting up to ``timeout`` seconds for it."""
        message = self._receive(min(timeout, self.deadline - time.monotonic()))
        if message is not None:
            self._on_event(message)

    def close(self) -> None:
        """Stop Chromium: closing the pipe makes it exit; kill it when it does not exit in time."""
        with contextlib.suppress(OSError):
            self.send("Browser.close")
        os.close(self._commands)
        os.close(self._replies)
        try:
            self.process.wait(timeout=max(0.0, min(_CLOSE_SECONDS, self.deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        _reap_children()

    def open_tab(self, context: str | None = None) -> str:
        """A new about:blank tab, in ``context`` when given; returns its flat session id."""
        params = {"url": "about:blank"} | ({"browserContextId": context} if context else {})
        target = self.call("Target.createTarget", params)["targetId"]
        return self.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]


def become_subreaper() -> None:
    """Adopt the processes Chromium detaches (its crashpad handler double-forks), so _reap_children finds them."""
    if ctypes.CDLL(None, use_errno=True).prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Cannot become a child subreaper")


def _children() -> list[int]:
    children = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            parent = int(Path(f"/proc/{entry}/stat").read_bytes().rsplit(b")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            continue
        if parent == os.getpid():
            children.append(int(entry))
    return children


def _reap_children() -> None:
    """Kill and reap every remaining child, so nothing writes to scratch after the renderer cleans it."""
    for _ in range(_REAP_ROUNDS):
        children = _children()
        if not children:
            return
        for pid in children:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
        for pid in children:
            with contextlib.suppress(ChildProcessError):
                os.waitpid(pid, 0)


class _Page:
    """Answers every request of the page from the version's files, and tracks when the page is quiet."""

    def __init__(self, job: Job):
        self.job = job
        self.browser: Browser | None = None
        self.pending: set[str] = set()
        self.loaded = False
        self.activity = time.monotonic()

    def quiet(self) -> bool:
        """The load event has fired, no request is pending, and nothing happened for the settle time."""
        return self.loaded and not self.pending and time.monotonic() - self.activity >= _SETTLE_SECONDS

    def on_event(self, message: dict[str, Any]) -> None:
        method, params = message.get("method"), message.get("params") or {}
        if method == "Fetch.requestPaused":
            self._answer(params, message.get("sessionId"))
            return
        if method == "Page.javascriptDialogOpening":
            # An open alert, confirm or prompt blocks the load event and the screenshot until the hard limit.
            self.browser.send("Page.handleJavaScriptDialog", {"accept": False}, message.get("sessionId"))
            return
        if method == "Network.requestWillBeSent":
            if not params.get("request", {}).get("url", "").startswith("data:"):
                self.pending.add(params.get("requestId"))
        elif method in ("Network.loadingFinished", "Network.loadingFailed"):
            self.pending.discard(params.get("requestId"))
        elif method == "Page.loadEventFired":
            self.loaded = True
        else:
            return
        self.activity = time.monotonic()

    def _answer(self, params: dict[str, Any], session: str | None) -> None:
        url = params.get("request", {}).get("url", "")
        status, headers, local = route(
            self.job.manifest, self.job.files, url, navigation=params.get("resourceType") == "Document"
        )
        ident = params.get("requestId")
        body = b""
        try:
            if local is not None and local.stat().st_size > MAX_SERVED_FILE_BYTES:
                status = FAIL
            elif local is not None:
                body = local.read_bytes()
        except OSError:
            status = FAIL
        if status == FAIL:
            self.browser.send("Fetch.failRequest", {"requestId": ident, "errorReason": "BlockedByClient"}, session)
            return
        answer = {
            "requestId": ident,
            "responseCode": status,
            "responseHeaders": [{"name": name, "value": value} for name, value in headers.items()],
            "body": base64.b64encode(body).decode("ascii"),
        }
        self.browser.send("Fetch.fulfillRequest", answer, session)


def _uniform(browser: Browser, data: bytes) -> bool:
    """Whether every pixel is within 2 per channel of the first one, decoded in a fresh browser context."""
    session = browser.open_tab(browser.call("Target.createBrowserContext")["browserContextId"])
    expression = f"({_UNIFORM_SCRIPT})({json.dumps(base64.b64encode(data).decode('ascii'))})"
    reply = browser.call(
        "Runtime.evaluate", {"expression": expression, "awaitPromise": True, "returnByValue": True}, session
    )
    value = reply.get("result", {}).get("value")
    if "exceptionDetails" in reply or not isinstance(value, bool):
        raise CdpError("The uniform-color check failed")
    return value


def capture(job: Job, scratch: Path, started: float) -> bytes | None:
    """The start page as a JPEG within MAX_PREVIEW_BYTES, or None for a blank or failed page."""
    soft = started + CAPTURE_SOFT_SECONDS
    profile = scratch / _PROFILE
    profile.mkdir()
    page = _Page(job)
    try:
        browser = page.browser = Browser(profile, started + CAPTURE_HARD_SECONDS, page.on_event)
        try:
            session = browser.open_tab()
            width, height = PREVIEW_VIEWPORT
            metrics = {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False}
            browser.call("Page.enable", {}, session)
            browser.call("Network.enable", {}, session)
            browser.call("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]}, session)
            browser.call("Emulation.setDeviceMetricsOverride", metrics, session)
            page.loaded = False
            if browser.call("Page.navigate", {"url": ORIGIN + "/"}, session).get("errorText"):
                return None
            while not page.quiet() and (left := soft - time.monotonic()) > 0:
                browser.pump(min(_POLL_SECONDS, left))

            def shoot(quality: int) -> bytes:
                params = {"format": "jpeg", "quality": quality, "captureBeyondViewport": False}
                return base64.b64decode(browser.call("Page.captureScreenshot", params, session)["data"])

            data = encode(shoot)
            if data is None or _uniform(browser, data):
                return None
            return data
        finally:
            browser.close()
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def _denied(action: Callable[[], object]) -> bool:
    """Whether ``action`` fails with EACCES or EPERM; a missing target is no proof of a denial."""
    try:
        action()
    except OSError as exc:
        return exc.errno in _DENIED
    return False


def _allowed(action: Callable[[], object]) -> bool:
    try:
        action()
    except OSError:
        return False
    return True


def _write_outside(scratch: Path) -> None:
    escape = scratch.parent / f".renderer-probe-{os.getpid()}"
    escape.write_bytes(b"")
    escape.unlink()


def _isolation_checks(scratch: Path) -> dict[str, bool]:
    """Prove, from inside the sandbox, the denials the renderer relies on."""
    environ = Path("/proc/self/environ").read_bytes()
    return {
        "ip_socket_denied": all(
            _denied(lambda family=family: socket.socket(family).close()) for family in (socket.AF_INET, socket.AF_INET6)
        ),
        "unix_socket_allowed": _allowed(lambda: socket.socket(socket.AF_UNIX).close()),
        "outside_read_denied": _denied(lambda: Path("/etc/passwd").read_bytes()),
        "outside_write_denied": _denied(lambda: _write_outside(scratch)),
        "environment_clean": dict(os.environ) == {"TMPDIR": str(scratch)} and b"VALIDATOR_SERVICE_KEY" not in environ,
        "shell_exec_denied": _denied(
            lambda: subprocess.run(  # noqa: S603 - a fixed argv the sandbox must refuse to execute
                ["/bin/sh", "-c", "exit 0"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        ),
        "parent_environ_denied": _denied(lambda: Path(f"/proc/{os.getppid()}/environ").read_bytes()),
    }


def _chromium_version(scratch: Path) -> str | None:
    profile = scratch / _PROFILE
    profile.mkdir()
    try:
        browser = Browser(profile, time.monotonic() + CAPTURE_HARD_SECONDS, lambda _message: None)
        try:
            product = browser.call("Browser.getVersion").get("product", "")
        finally:
            browser.close()
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    version = product.rpartition("/")[2]
    return version if version and all(part.isdigit() for part in version.split(".")) else None


def _log(exc: BaseException) -> None:
    # Class names only: exception text can carry page content or paths.
    sys.stderr.write(f"renderer: {type(exc).__name__}\n")


def _emit(value: object) -> None:
    sys.stdout.write(json.dumps(value) + "\n")
    sys.stdout.flush()


def _remove_new_entries(scratch: Path, keep: set[str]) -> None:
    """Delete what this run added to ``scratch``; entries in ``keep`` stay."""
    try:
        entries = os.listdir(scratch)
    except OSError:
        return
    for name in entries:
        if name in keep:
            continue
        path = scratch / name
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def run_capture(job_path: Path, scratch_path: Path, started: float) -> None:
    """Emit the result line for one capture; every outcome, a preview or none, is a normal exit."""
    try:
        job = load_job(job_path)
        scratch = scratch_path.resolve(strict=True)
        before = set(os.listdir(scratch)) - {PREVIEW_NAME}
    except (OSError, ValueError) as exc:
        _log(exc)
        _emit(None)
        return
    result = None
    try:
        enforce_renderer_sandbox(job.root, scratch, BROWSER)
        become_subreaper()
        data = capture(job, scratch, started)
        if data is not None and (result := describe(data)) is not None:
            (scratch / PREVIEW_NAME).write_bytes(data)
    except Exception as exc:  # Any failure means no preview; the verdict never depends on it.
        _log(exc)
        result = None
    _remove_new_entries(scratch, before | ({PREVIEW_NAME} if result else set()))
    _emit(result)


def run_probe(scratch_path: Path) -> int:
    report: dict[str, object] = dict.fromkeys(PROBE_CHECKS, False) | {"chromium": None, "landlock_abi": None}
    scratch, before = None, set()
    try:
        scratch = scratch_path.resolve(strict=True)
        before = set(os.listdir(scratch))
        # Reported, not checked: ABI 6 or later also scopes signals (README section 13).
        report["landlock_abi"] = landlock_abi()
        # The probe serves no files: scratch doubles as the readable files directory.
        enforce_renderer_sandbox(scratch, scratch, BROWSER)
        become_subreaper()
        report["sandbox"] = True
        report |= _isolation_checks(scratch)
        report["chromium"] = _chromium_version(scratch)
    except Exception as exc:  # A probe that cannot finish fails; capture stays off.
        _log(exc)
    if scratch is not None:
        _remove_new_entries(scratch, before)
    _emit(report)
    return 0 if report["chromium"] and all(report[name] is True for name in PROBE_CHECKS) else 1


def main(argv: list[str]) -> int:
    started = time.monotonic()
    match argv:
        case ["capture", job_path, scratch_path]:
            run_capture(Path(job_path), Path(scratch_path), started)
            return 0
        case ["probe", scratch_path]:
            return run_probe(Path(scratch_path))
    _emit(None)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
