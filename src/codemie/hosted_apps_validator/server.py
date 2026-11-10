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

"""Shared bounded inspection service; no database, storage, or platform credentials."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import http.client
import json
import logging
import os
import secrets
import shutil
import signal
import socket
import ssl
import stat
import subprocess
import sys
import time
import urllib.request
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse, Response
from starlette.routing import Route

from codemie.core.hosted_applications.errors import HostedApplicationError, ARTIFACT_REJECTION_CODES
from codemie.core.hosted_applications.validation import candidate_from_result
from codemie.hosted_apps_validator.contract import LIMIT_REJECTION_CODES, RESULT_GRACE_SECONDS, validation_budget
from codemie.hosted_apps_validator.logging_config import LOGGER_NAME, configure_logging
from codemie.hosted_apps_validator.scanner import ClamAvScanner
from codemie.core.hosted_applications.policy import POLICY_VERSION, POLICY_DIGEST

logger = logging.getLogger(LOGGER_NAME)

MAX_RESULT = 6 * 1024 * 1024
# Parser stderr kept for the log; anything beyond is read and discarded so the child never blocks on it.
STDERR_KEPT = 64 * 1024
# A slot granted by POST /reservations waits this long for its upload before it is free again.
RESERVATION_SECONDS = 60
WILDCARD_HOSTS = {"0.0.0.0": "127.0.0.1", "::": "::1", "": "127.0.0.1"}
PARSER_CODES = ARTIFACT_REJECTION_CODES | LIMIT_REJECTION_CODES | {"VALIDATION_UNAVAILABLE"}
INSPECTION_FAILED = "Artifact inspection failed safely"


def busy():
    return HostedApplicationError("VALIDATOR_BUSY", "Validation capacity is busy; retry later", 503)


@dataclass
class Workspace:
    path: Path
    expires: float
    result: dict
    files: list[Path] = field(default_factory=list)


def running_as_root() -> bool:
    """Whether this process has root privileges, which the validator never runs with."""
    return os.getuid() == 0


def private_workspace(root: Path) -> Path:
    """A fresh ``jobs`` directory that only this process can use; earlier runs' leftovers are removed.

    One validator process owns its scratch volume, so nothing under ``jobs`` can belong to a live
    job at startup. The mounted volume's own permissions never decide who can read uploads.
    """
    if running_as_root():
        raise ValueError("The validator must run as an unprivileged user")
    jobs = root / "jobs"
    if jobs.is_symlink():
        raise ValueError("The validator workspace must not be a symbolic link")
    shutil.rmtree(jobs, ignore_errors=True)
    jobs.mkdir(mode=0o700, parents=True)
    os.chmod(jobs, 0o700)
    info = jobs.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("The validator workspace must be a private directory owned by the validator")
    return jobs


def reported_files(result, assets: Path) -> tuple[dict, list[Path]]:
    """The parser's result as a checked candidate, and each reported file as a path inside ``assets``.

    The parser is untrusted: a path that leaves the workspace, a link, a missing file or a size that
    differs from the report fails closed before any reported file is opened.
    """
    try:
        candidate = candidate_from_result(result)
        base = assets.resolve(strict=True)
        files = []
        for item in candidate.files:
            path = base / item.path
            info = path.lstat()
            if path.resolve(strict=True) != path or not stat.S_ISREG(info.st_mode) or info.st_size != item.size:
                raise ValueError("Unexpected parser output")
            files.append(path)
    except (KeyError, TypeError, ValueError, OSError, HostedApplicationError) as exc:
        raise HostedApplicationError("VALIDATION_UNAVAILABLE", INSPECTION_FAILED, 503) from exc
    return candidate.to_dict(), files


def require_isolation(root: Path) -> None:
    """Refuse to serve unless the parser sandbox really blocks network, outside files and programs."""
    workspace = root / "isolation-check"
    # The probe tries to create this sibling: writable for the validator, outside the parser's sandbox.
    outside = root / "isolation-check-outside"
    workspace.mkdir(mode=0o700)
    try:
        source = workspace / "source"
        source.write_bytes(b"isolation check")
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "codemie.hosted_apps_validator.sandbox",
                str(source),
                str(workspace / "out"),
                "probe",
            ],
            capture_output=True,
            timeout=30,
            check=False,
            env={"PYTHONPATH": str(Path(__file__).parents[2])},
        )
        try:
            checks = json.loads(completed.stdout)
        except ValueError:
            checks = None
        if (
            completed.returncode
            or not isinstance(checks, dict)
            or not checks
            or not all(value is True for value in checks.values())
        ):
            stderr = completed.stderr.decode("utf-8", "replace").strip()
            logger.error(
                "Parser isolation probe failed: returncode=%s checks=%s stderr=%s",
                completed.returncode,
                checks,
                stderr,
            )
            raise RuntimeError(f"Parser isolation is not enforced on this node: {checks}")
        logger.info("Parser isolation probe passed: %s", checks)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        outside.unlink(missing_ok=True)


async def _drain(stream, limit: int) -> bytes:
    """Read ``stream`` to EOF, keeping only its first ``limit`` bytes."""
    kept = bytearray()
    while chunk := await stream.read(64 * 1024):
        if len(kept) < limit:
            kept.extend(chunk[: limit - len(kept)])
    return bytes(kept)


class Inspector:
    """Each validation occupies one slot, by its key, from reservation until its result is deleted or expires."""

    def __init__(self, root: Path, scanner, secret: str, capacity=2):
        if len(secret) < 32 or not 1 <= capacity <= 4:
            raise ValueError("Invalid validator configuration")
        self.root, self.scanner, self.secret = private_workspace(root), scanner, secret
        self.capacity = capacity
        self.reservations = {}  # key -> monotonic expiry of a slot granted before its upload arrives
        self.validating = set()  # keys whose upload is being received and inspected
        self.workspaces = {}  # key -> finished result, until the client deletes it or it expires
        self.lock = asyncio.Lock()

    def occupied(self):
        return len(self.reservations.keys() | self.validating | self.workspaces.keys())

    def authorize(self, request):
        values = request.headers.getlist("x-validator-key")
        if len(values) != 1 or not hmac.compare_digest(values[0], self.secret):
            raise HostedApplicationError("VALIDATION_UNAVAILABLE", "Validator authentication failed", 503)

    async def _reclaim(self, now):
        for key in [key for key, expiry in self.reservations.items() if expiry <= now]:
            del self.reservations[key]
        # A result past its job budget is useless to the client; a stream still reading it keeps its open file.
        for key in [key for key, item in self.workspaces.items() if item.expires <= now]:
            self.workspaces.pop(key)
            await asyncio.to_thread(shutil.rmtree, self.root / key, True)

    async def reserve(self):
        """Hold a free slot for a client that sends its upload next; busy when every slot is taken."""
        async with self.lock:
            now = time.monotonic()
            await self._reclaim(now)
            if self.occupied() >= self.capacity:
                raise busy()
            key = secrets.token_hex(32)
            self.reservations[key] = now + RESERVATION_SECONDS
            return key

    async def start(self, reservation):
        """The slot for one upload: its earlier reservation, or a free slot taken now."""
        async with self.lock:
            now = time.monotonic()
            if reservation is not None:
                expiry = self.reservations.pop(reservation, None)
                if expiry is None or expiry <= now:
                    raise busy()
                key = reservation
            else:
                await self._reclaim(now)
                if self.occupied() >= self.capacity:
                    raise busy()
                key = secrets.token_hex(32)
            self.validating.add(key)
            path = self.root / key
            path.mkdir(mode=0o700)
            return key, path

    async def reservation(self, request):
        self.authorize(request)
        key = await self.reserve()
        return JSONResponse({"reservation": key}, status_code=201, headers={"Cache-Control": "no-store"})

    async def parser(self, source, output, kind):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-B",
            "-m",
            "codemie.hosted_apps_validator.sandbox",
            str(source),
            str(output),
            kind,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PYTHONPATH": str(Path(__file__).parents[2])},
            start_new_session=True,
            close_fds=True,
        )
        # Drained while stdout is read: a child blocked on a full stderr pipe would never close stdout.
        stderr_task = asyncio.create_task(_drain(process.stderr, STDERR_KEPT))
        try:
            # Read bounded stdout concurrently, never communicate() unbounded attacker-derived output.
            data = bytearray()
            while chunk := await process.stdout.read(64 * 1024):
                data.extend(chunk)
                if len(data) > MAX_RESULT:
                    raise HostedApplicationError("VALIDATION_LIMIT_EXCEEDED", "Validation result exceeds its bound")
            await process.wait()
            # Bounded like stdout: the parser is untrusted and its stderr is diagnostic, never parsed.
            stderr = (await stderr_task).decode("utf-8", "replace").strip()
            if process.returncode == -signal.SIGXCPU:
                # The same upload exhausts the parser's CPU limit again on every attempt.
                logger.warning("Parser exceeded its CPU limit (kind=%s): %s", kind, stderr)
                raise HostedApplicationError("VALIDATION_LIMIT_EXCEEDED", "Artifact inspection exceeded its CPU limit")
            try:
                result = json.loads(data)
            except ValueError as exc:
                logger.error("Parser produced unparseable output (kind=%s): %s", kind, stderr)
                raise HostedApplicationError("VALIDATION_UNAVAILABLE", INSPECTION_FAILED, 503) from exc
            if process.returncode:
                if not isinstance(result, dict) or result.get("code") not in PARSER_CODES:
                    logger.error(
                        "Parser exited %s with an unrecognized result (kind=%s): %s", process.returncode, kind, stderr
                    )
                    raise HostedApplicationError("VALIDATION_UNAVAILABLE", INSPECTION_FAILED, 503)
                logger.warning("Parser rejected the upload (kind=%s): %s", kind, result["code"])
                raise HostedApplicationError(
                    result["code"],
                    "Artifact inspection rejected this upload",
                    503 if result["code"] == "VALIDATION_UNAVAILABLE" else 422,
                )
            return result
        finally:
            if process.returncode is None:
                # Only our own parser's group: it runs in its own session (start_new_session=True), and while
                # returncode is None it is not reaped yet, so its pid and group id cannot belong to anyone else.
                os.killpg(process.pid, signal.SIGKILL)  # NOSONAR - kills only the parser group started above
                await asyncio.shield(process.wait())
            if not stderr_task.done():
                stderr_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stderr_task

    @staticmethod
    def _validation_request(request) -> tuple[str, int, str]:
        """The upload's kind, declared size and SHA-256 digest; an incomplete or oversized declaration is refused."""
        kind = request.headers.get("x-artifact-kind")
        if kind not in ("html", "zip") or request.headers.get("content-type") != "application/octet-stream":
            raise HostedApplicationError("INVALID_ARTIFACT", "Invalid validation request", 422)
        try:
            size = int(request.headers["x-artifact-size"])
            digest = request.headers["x-artifact-sha256"]
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError
            if not 0 < size <= (20 if kind == "html" else 100) * 1024 * 1024:
                raise ValueError
        except (KeyError, ValueError) as exc:
            raise HostedApplicationError("INVALID_ARTIFACT", "Invalid validation request", 422) from exc
        return kind, size, digest

    @staticmethod
    async def _receive(request, source: Path, size: int, digest: str) -> None:
        """Write the upload to ``source``; more bytes than declared, or a different digest, is refused."""
        source_hash = hashlib.sha256()
        count = 0
        with source.open("xb") as file:
            stream = request.stream().__aiter__()
            while True:
                try:
                    chunk = await asyncio.wait_for(anext(stream), timeout=15)
                except StopAsyncIteration:
                    break
                count += len(chunk)
                if count > size:
                    raise HostedApplicationError("INVALID_ARTIFACT", "Artifact size mismatch")
                source_hash.update(chunk)
                await asyncio.to_thread(file.write, chunk)
        if count != size or not hmac.compare_digest(source_hash.hexdigest(), digest):
            raise HostedApplicationError("INVALID_ARTIFACT", "Artifact integrity mismatch")

    async def validate(self, request):
        self.authorize(request)
        kind, size, digest = self._validation_request(request)
        # The API's job budget for this upload: neither the inspection nor its result is useful after it.
        budget = validation_budget(kind, size)
        started = time.monotonic()
        key, path = await self.start(request.headers.get("x-validation-reservation"))
        try:
            source = path / "source"
            async with asyncio.timeout(budget):
                scanner_before = await self.scanner.health()
                await self._receive(request, source, size, digest)
                await self.scanner.scan(source)
                # The parser may write only inside its own directory, never next to the uploaded source.
                assets = path / "parser" / "assets"
                result, files = reported_files(await self.parser(source, assets, kind), assets)
                for file in files:
                    await self.scanner.scan(file)
                scanner_after = await self.scanner.health()
                if scanner_before != scanner_after:
                    raise HostedApplicationError(
                        "VALIDATION_UNAVAILABLE", "Scanner changed during inspection; retry", 503
                    )
                result.update(
                    {
                        "validation_id": key,
                        "scanner": scanner_after,
                        "source_digest": digest,
                        "policy_version": POLICY_VERSION,
                        "policy_definition_digest": POLICY_DIGEST.hex(),
                    }
                )
                self.workspaces[key] = Workspace(path, started + budget + RESULT_GRACE_SECONDS, result, files)
                logger.info(
                    "Validation %s succeeded (kind=%s, size=%d, elapsed=%.1fs)",
                    key,
                    kind,
                    size,
                    time.monotonic() - started,
                )
                return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except HostedApplicationError as exc:
            logger.warning(
                "Validation %s rejected (kind=%s, size=%d, elapsed=%.1fs): %s",
                key,
                kind,
                size,
                time.monotonic() - started,
                exc.code,
            )
            await asyncio.shield(asyncio.to_thread(shutil.rmtree, path, True))
            raise
        except BaseException:
            logger.exception(
                "Validation %s failed (kind=%s, size=%d, elapsed=%.1fs)", key, kind, size, time.monotonic() - started
            )
            await asyncio.shield(asyncio.to_thread(shutil.rmtree, path, True))
            raise
        finally:
            self.validating.discard(key)

    def workspace(self, request):
        self.authorize(request)
        key = request.path_params["key"]
        workspace = self.workspaces.get(key)
        if workspace is None or workspace.expires <= time.monotonic():
            raise HostedApplicationError("VALIDATION_UNAVAILABLE", "Validation workspace expired", 503)
        return key, workspace

    async def file(self, request):
        _, workspace = self.workspace(request)
        index = request.path_params["index"]
        if not 0 <= index < len(workspace.result["files"]):
            raise HostedApplicationError("VALIDATION_UNAVAILABLE", "Validation file unavailable", 503)
        item = workspace.result["files"][index]

        async def chunks():
            # An open file stays readable when its workspace is deleted or expires meanwhile.
            with workspace.files[index].open("rb") as file:
                while chunk := await asyncio.to_thread(file.read, 1024 * 1024):
                    yield chunk

        return StreamingResponse(
            chunks(),
            media_type="application/octet-stream",
            headers={"Content-Length": str(item["size"]), "Cache-Control": "no-store"},
        )

    async def delete(self, request):
        key, workspace = self.workspace(request)
        async with self.lock:
            self.workspaces.pop(key, None)
            await asyncio.to_thread(shutil.rmtree, workspace.path, True)
        return Response(status_code=204)

    async def health(self, request):
        self.authorize(request)
        return JSONResponse(await self.scanner.health(), headers={"Cache-Control": "no-store"})


def create_app(inspector):
    async def failure(request, exc):
        return JSONResponse(
            {"code": exc.code, "message": exc.message}, status_code=exc.status, headers={"Cache-Control": "no-store"}
        )

    async def timeout_failure(request, exc):
        return JSONResponse(
            {"code": "VALIDATION_UNAVAILABLE", "message": "Validation exceeded its time budget"},
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )

    async def live(request):
        # Container liveness: unauthenticated and information-free; scanner detail stays behind /health.
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    return Starlette(
        routes=[
            Route("/livez", live, methods=["GET"]),
            Route("/health", inspector.health, methods=["GET"]),
            Route("/reservations", inspector.reservation, methods=["POST"]),
            Route("/validate", inspector.validate, methods=["POST"]),
            Route("/results/{key}/files/{index:int}", inspector.file, methods=["GET"]),
            Route("/results/{key}", inspector.delete, methods=["DELETE"]),
        ],
        exception_handlers={HostedApplicationError: failure, TimeoutError: timeout_failure},
    )


def configured_inspector():
    return Inspector(
        Path(os.environ.get("VALIDATOR_WORKSPACE", "/work")),
        ClamAvScanner(
            os.environ["CLAMD_HOST"],
            int(os.environ.get("CLAMD_PORT", "3310")),
            configuration=Path(os.environ["CLAMD_CONFIGURATION"]),
        ),
        os.environ["VALIDATOR_SERVICE_KEY"],
    )


def _listener(environ) -> dict:
    host = environ.get("VALIDATOR_BIND_HOST", "127.0.0.1")
    try:
        port = int(environ.get("VALIDATOR_PORT", "8091"))
    except ValueError as exc:
        raise ValueError("VALIDATOR_PORT must be a port number") from exc
    if not 1 <= port <= 65535:
        raise ValueError("VALIDATOR_PORT must be a port number")
    certificate = environ.get("VALIDATOR_TLS_CERT_FILE", "")
    key = environ.get("VALIDATOR_TLS_KEY_FILE", "")
    if bool(certificate) != bool(key):
        raise ValueError("Set both VALIDATOR_TLS_CERT_FILE and VALIDATOR_TLS_KEY_FILE, or neither")
    options = {"host": host, "port": port, "access_log": False, "log_level": "warning"}
    if certificate:
        options.update(ssl_certfile=certificate, ssl_keyfile=key)
    return options


def server_options(environ=os.environ) -> dict:
    """uvicorn listener: loopback by default, TLS when both a certificate and a key are mounted."""
    return _listener(environ)


def certificate_host_name(certificate: str) -> str | None:
    """The DNS name the mounted certificate is issued for: its first DNS subject alternative name,
    or its common name when it has no alternative names. None when it names no usable host.

    The validator image carries only the standard library and pure-Python wheels, so the PEM file is
    decoded by the interpreter's own ssl module (the same decoder behind ``getpeercert``).
    """
    decoded = ssl._ssl._test_decode_cert(certificate)
    alternatives = decoded.get("subjectAltName", ())
    names = [value for kind, value in alternatives if kind == "DNS"]
    if not alternatives:
        names = [value for rdn in decoded.get("subject", ()) for key, value in rdn if key == "commonName"]
    return next((name for name in names if name and "*" not in name), None)


def _secure_status(host: str, port: int, certificate: str) -> int | None:
    """Status of /livez over TLS, verified against exactly the mounted certificate and the name it is issued for.

    The probe dials the loopback address but checks the certificate's own host name, so hostname
    verification stays on. None when the certificate names no host the check could use.
    """
    name = certificate_host_name(certificate)
    if name is None:
        return None
    context = ssl.create_default_context(cafile=certificate)
    context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    with (
        socket.create_connection((host, port), timeout=3) as raw,
        context.wrap_socket(raw, server_hostname=name) as tls,
    ):
        connection = http.client.HTTPConnection(name, port, timeout=3)
        connection.sock = tls
        connection.request("GET", "/livez")
        return connection.getresponse().status


def probe(environ=os.environ) -> int:
    """Container health command: 0 when the local listener answers /livez, 1 otherwise."""
    options = _listener(environ)
    host = WILDCARD_HOSTS.get(options["host"], options["host"])
    authority = f"[{host}]" if ":" in host else host
    try:
        if "ssl_certfile" in options:
            status = _secure_status(host, options["port"], options["ssl_certfile"])
        else:
            # No certificate is mounted, so the listener itself serves plain HTTP; the probe only calls its own
            # information-free /livez in the same container and sends no credentials.
            url = f"http://{authority}:{options['port']}/livez"  # NOSONAR - same-container probe of a plain listener
            with urllib.request.urlopen(url, timeout=3) as response:
                status = response.status
    except (OSError, ValueError, http.client.HTTPException):
        return 1
    return 0 if status == 204 else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--probe"]:
        sys.exit(probe())
    import uvicorn

    configure_logging()
    inspector = configured_inspector()
    # Fail at startup, visibly, on a node whose kernel cannot sandbox the parser.
    require_isolation(inspector.root)
    logger.info("Validator ready (capacity=%d)", inspector.capacity)
    uvicorn.run(create_app(inspector), **server_options())
