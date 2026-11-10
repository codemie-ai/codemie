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

"""Bounded ClamD INSTREAM protocol; every non-clean or unhealthy result fails closed."""

from __future__ import annotations

import asyncio
import re
import struct
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

from codemie.core.hosted_applications.errors import HostedApplicationError


def unavailable():
    return HostedApplicationError(
        "VALIDATION_UNAVAILABLE", "The malware scanner is unavailable; validation will retry", 503
    )


def validate_clamd_configuration(path: Path):
    """Require the same immutable reviewed configuration mounted into ClamD and the validator."""
    try:
        if path.is_symlink() or path.stat().st_size > 16384:
            raise ValueError
        options = {}
        for line in path.read_text(encoding="ascii").splitlines():
            line = line.partition("#")[0].strip()
            if not line:
                continue
            key, value = line.split(None, 1)
            if key in options:
                raise ValueError
            options[key] = value.strip()
        from codemie.core.hosted_applications.policy import POLICY_DEFINITION

        required = POLICY_DEFINITION["scanner"]["configuration"]
        if any(options.get(key) != value for key, value in required.items()):
            raise ValueError
        if any(key in options for key in ("ExcludePath", "ExcludePUA", "IncludePUA")):
            raise ValueError
    except (OSError, ValueError) as exc:
        raise unavailable() from exc


def parse_scanner_version(value: str, *, now=None, maximum_age_seconds=48 * 3600):
    now = now or datetime.now(timezone.utc)
    try:
        match = re.fullmatch(r"ClamAV (\d+[.]\d+[.]\d+(?:-[a-zA-Z\d.]+)?)/(\d{1,12})/(.{20,40})", value, flags=re.ASCII)
        if not match:
            raise ValueError("Invalid scanner version")
        version, signature, stamp = match.groups()
        if version.split(".")[:2] not in (["1", "4"], ["1", "5"]):
            raise ValueError("Unapproved scanner engine")
        # clamd formats this date in its own local time: the deployments pin TZ=UTC on the clamd container.
        updated = datetime.strptime(stamp, "%a %b %d %H:%M:%S %Y").replace(tzinfo=timezone.utc)
        if updated > now + timedelta(minutes=5) or now - updated > timedelta(seconds=maximum_age_seconds):
            raise ValueError("Stale scanner signatures")
        return {"version": version, "signature_version": signature, "signature_updated_at": updated.isoformat()}
    except (ValueError, TypeError) as exc:
        raise unavailable() from exc


def scan_verdict(value: bytes) -> None:
    """Accept only a clean stream. Signature names never leave the validator, only a fixed code.

    A ``Heuristics.Limits.Exceeded`` result means the engine stopped at a size, file, depth or time limit:
    the upload could not be scanned completely, which is a rejection but not a malware finding.
    """
    if value == b"stream: OK":
        return
    if value.startswith(b"stream: ") and value.endswith(b" FOUND"):
        if value.removeprefix(b"stream: ").startswith(b"Heuristics.Limits.Exceeded"):
            raise HostedApplicationError(
                "SCAN_LIMIT_EXCEEDED", "The malware scanner could not scan the whole upload within its limits"
            )
        raise HostedApplicationError("MALWARE_DETECTED", "Malware was detected; upload a clean artifact")
    raise ValueError("Non-clean scanner result")


class ClamAvScanner:
    def __init__(self, host: str, port: int, *, maximum_age_seconds=48 * 3600, configuration=None):
        self.configuration = configuration
        self.host, self.port, self.maximum_age_seconds = host, port, maximum_age_seconds

    async def _command(self, command):
        writer = None
        try:
            async with asyncio.timeout(5):
                reader, writer = await asyncio.open_connection(self.host, self.port, limit=4096)
                writer.write(command)
                await writer.drain()
                value = await reader.readuntil(b"\x00")
                if len(value) > 4096:
                    raise ValueError("Scanner response limit")
                return value[:-1].decode("ascii", errors="strict")
        except (OSError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
            raise unavailable() from exc
        finally:
            if writer:
                writer.close()
                with suppress(OSError):
                    await writer.wait_closed()

    async def health(self):
        if self.configuration is not None:
            validate_clamd_configuration(self.configuration)
        if await self._command(b"zPING\x00") != "PONG":
            raise unavailable()
        return parse_scanner_version(await self._command(b"zVERSION\x00"), maximum_age_seconds=self.maximum_age_seconds)

    async def scan(self, path: Path):
        from codemie.core.hosted_applications.policy import POLICY_DEFINITION

        # Do not cut off a healthy engine before its reviewed scan-time limit.
        # The whole validation job has its own bounded timeout and capacity slot.
        timeout = int(POLICY_DEFINITION["scanner"]["configuration"]["MaxScanTime"]) / 1000 + 10
        writer = None
        try:
            async with asyncio.timeout(timeout):
                reader, writer = await asyncio.open_connection(self.host, self.port, limit=4096)
                writer.write(b"zINSTREAM\x00")
                with path.open("rb") as file:
                    while chunk := await asyncio.to_thread(file.read, 1024 * 1024):
                        writer.write(struct.pack(">I", len(chunk)))
                        writer.write(chunk)
                        await writer.drain()
                writer.write(struct.pack(">I", 0))
                await writer.drain()
                scan_verdict((await reader.readuntil(b"\x00"))[:-1])
        except (OSError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
            raise unavailable() from exc
        finally:
            if writer:
                writer.close()
                with suppress(OSError):
                    await writer.wait_closed()
