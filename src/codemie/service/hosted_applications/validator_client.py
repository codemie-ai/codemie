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

"""Protected streaming protocol to the shared validator; no uploaded archive is parsed here."""

from __future__ import annotations

import asyncio
import json
import re
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

import httpx

from codemie.configs.logger import logger
from codemie.core.hosted_applications.errors import HostedApplicationError, ARTIFACT_REJECTION_CODES
from codemie.core.hosted_applications.validation import candidate_from_result
from codemie.core.hosted_applications.preview import MAX_PREVIEW_BYTES, MAX_PREVIEW_SIDE
from codemie.hosted_apps_validator.contract import CAPTURE_REASONS, LIMIT_REJECTION_CODES, validation_budget

# A healthy validator is asked again after this long; an unhealthy or unreachable one is left alone as long.
HEALTH_SECONDS = 5.0
# Every validator slot is taken: this process claims no validation work for this long.
BUSY_SECONDS = 3.0
MAX_RESPONSE = 6 * 1024 * 1024
MAX_PREVIEW_RESPONSE = 4096
# A lowercase hex SHA-256: preview digests, validation ids and reservations.
HEX_SHA256 = re.compile("[0-9a-f]{64}")


@dataclass(frozen=True)
class PreviewFile:
    """One captured picture held by the validator; only the fields file_chunks and the worker need."""

    index: int
    size: int
    sha256: str
    width: int
    height: int


def unavailable():
    return HostedApplicationError("VALIDATION_UNAVAILABLE", "Artifact validation is unavailable; retry later", 503)


def not_accepted(code):
    """The validator never took the job: the worker puts it back in the queue without counting a retry."""
    return HostedApplicationError(code, "Artifact validation cannot take this upload yet; it stays queued", 503)


def _int_in(value, low, high):
    return type(value) is int and low <= value <= high


def _preview_file(value):
    """The validator's answer: a PreviewFile, or a string for no picture.

    A null answer gives the validator's reason (``off``, ``busy`` or ``no_picture``), or ``unknown`` when an older
    validator names none. Anything that is not exactly the contract gives ``failed``.
    """
    if not isinstance(value, dict) or "preview" not in value:
        return "failed"
    if value["preview"] is None:
        if set(value) == {"preview"}:
            return "unknown"
        reason = value.get("reason")
        return reason if set(value) == {"preview", "reason"} and reason in CAPTURE_REASONS else "failed"
    item = value["preview"]
    if (
        set(value) != {"preview"}
        or not isinstance(item, dict)
        or set(item) != {"index", "size", "sha256", "width", "height"}
    ):
        return "failed"
    digest = item["sha256"]
    if not (
        _int_in(item["index"], 0, 2**31)
        and _int_in(item["size"], 1, MAX_PREVIEW_BYTES)
        and _int_in(item["width"], 1, MAX_PREVIEW_SIDE)
        and _int_in(item["height"], 1, MAX_PREVIEW_SIDE)
        and isinstance(digest, str)
        and HEX_SHA256.fullmatch(digest)
    ):
        return "failed"
    return PreviewFile(item["index"], item["size"], digest, item["width"], item["height"])


def _scanner_shape(scanner):
    if not isinstance(scanner, dict) or set(scanner) != {"version", "signature_version", "signature_updated_at"}:
        raise ValueError
    return scanner


def _require_policy(value, source_digest):
    from codemie.core.hosted_applications.policy import POLICY_VERSION, POLICY_DIGEST

    if not isinstance(value, dict) or value.get("source_digest") != source_digest:
        raise ValueError
    if value.get("policy_version") != POLICY_VERSION or value.get("policy_definition_digest") != POLICY_DIGEST.hex():
        raise ValueError


def candidate_response(value, source_digest):
    try:
        _require_policy(value, source_digest)
        identifier = value["validation_id"]
        if not isinstance(identifier, str) or not HEX_SHA256.fullmatch(identifier):
            raise ValueError
        candidate = candidate_from_result(value)
        scanner = _scanner_shape(value["scanner"])
        from codemie.hosted_apps_validator.scanner import parse_scanner_version
        from datetime import datetime

        stamp = datetime.fromisoformat(scanner["signature_updated_at"])
        parse_scanner_version(
            "ClamAV "
            + scanner["version"]
            + "/"
            + scanner["signature_version"]
            + "/"
            + stamp.strftime("%a %b %d %H:%M:%S %Y")
        )
        return identifier, candidate, scanner
    except (KeyError, TypeError, ValueError, HostedApplicationError) as exc:
        raise unavailable() from exc


def rejection(code, status):
    """A validator outcome that ends validation; validator-supplied messages and excerpts are never relayed."""
    if status in (413, 422) and code in ARTIFACT_REJECTION_CODES:
        return HostedApplicationError(code, "This artifact did not pass the required security checks")
    if status == 422 and code in LIMIT_REJECTION_CODES:
        return HostedApplicationError(code, "This artifact exceeds what validation can process")
    return None


class ValidatorClient:
    def __init__(self, base_url, key, *, client=None):
        self.base_url, self.key = base_url.rstrip("/"), key
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=5),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        )
        self.healthy_until = self.paused_until = 0.0
        self.health_lock = asyncio.Lock()

    @property
    def headers(self):
        return {"x-validator-key": self.key}

    def pause(self, seconds):
        self.paused_until = max(self.paused_until, time.monotonic() + seconds)
        self.healthy_until = 0.0

    async def health(self):
        try:
            async with self.client.stream("GET", self.base_url + "/health", headers=self.headers) as response:
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 4096:
                        raise ValueError
                if response.status_code != 200:
                    raise ValueError
                return json.loads(data)
        except (httpx.HTTPError, ValueError) as exc:
            raise unavailable() from exc

    async def ready(self):
        """Cached verdict: workers claim validation jobs only while the validator and its scanner can take them."""
        if time.monotonic() < self.paused_until:
            return False
        if time.monotonic() < self.healthy_until:
            return True
        async with self.health_lock:
            if time.monotonic() < self.paused_until:
                return False
            if time.monotonic() < self.healthy_until:
                return True
            try:
                await self.health()
            except HostedApplicationError:
                self.pause(HEALTH_SECONDS)
                return False
            self.healthy_until = time.monotonic() + HEALTH_SECONDS
            return True

    async def reserve(self):
        """A validator slot for one upload, taken before any byte is sent. Every failure here leaves the job queued."""
        try:
            response = await self.client.post(self.base_url + "/reservations", headers=self.headers)
            data = response.json() if response.status_code in (201, 503) else None
        except (httpx.HTTPError, ValueError) as exc:
            self.pause(HEALTH_SECONDS)
            raise not_accepted("VALIDATOR_UNREACHABLE") from exc
        if response.status_code == 503 and isinstance(data, dict) and data.get("code") == "VALIDATOR_BUSY":
            self.pause(BUSY_SECONDS)
            raise not_accepted("VALIDATOR_BUSY")
        reservation = data.get("reservation") if isinstance(data, dict) and response.status_code == 201 else None
        if not isinstance(reservation, str) or not HEX_SHA256.fullmatch(reservation):
            self.pause(HEALTH_SECONDS)
            raise not_accepted("VALIDATOR_UNREACHABLE")
        return reservation

    async def _validate(self, chunks, *, size, digest, kind, reservation):
        headers = self.headers | {
            "Content-Type": "application/octet-stream",
            "x-artifact-kind": kind,
            "x-artifact-size": str(size),
            "x-artifact-sha256": digest.hex(),
            "x-validation-reservation": reservation,
        }
        # The validator answers only when the inspection is finished: wait as long as the job may run.
        timeout = httpx.Timeout(validation_budget(kind, size) + 15, connect=5)
        async with self.client.stream(
            "POST", self.base_url + "/validate", content=chunks, headers=headers, timeout=timeout
        ) as response:
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_RESPONSE:
                    raise ValueError
            status = response.status_code
        if status != 200:
            code = json.loads(data).get("code")
            if error := rejection(code, status):
                raise error
            if status == 503 and code == "VALIDATOR_BUSY":
                # The reservation lapsed before the upload arrived; the validator did not start it.
                self.pause(BUSY_SECONDS)
                raise not_accepted(code)
            raise ValueError
        return candidate_response(json.loads(data), digest.hex())

    @asynccontextmanager
    async def inspect(self, chunks, *, size, digest, kind):
        reservation = await self.reserve()
        identifier = None
        try:
            try:
                identifier, candidate, scanner = await self._validate(
                    chunks, size=size, digest=digest, kind=kind, reservation=reservation
                )
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
                raise unavailable() from exc
            yield identifier, candidate, scanner
        finally:
            if identifier:
                # The validator also reclaims results after the job budget; no cloud reservation is released here.
                with suppress(httpx.HTTPError):
                    await self.client.delete(self.base_url + "/results/" + identifier, headers=self.headers)

    async def file_chunks(self, identifier, index, candidate_file):
        """One validated file's bytes; the worker checks the digest of what it stored, not of the stream."""
        count = 0
        try:
            async with self.client.stream(
                "GET", self.base_url + f"/results/{identifier}/files/{index}", headers=self.headers
            ) as response:
                if response.status_code != 200:
                    raise ValueError
                async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                    count += len(chunk)
                    if count > candidate_file.size:
                        raise ValueError
                    yield chunk
            if count != candidate_file.size:
                raise ValueError
        except (httpx.HTTPError, ValueError) as exc:
            raise unavailable() from exc

    async def capture(  # noqa: PLR0913 - one argument per request field, limits keyword-only
        self,
        identifier: str,
        site_root: str,
        entrypoint: str,
        routing_mode: str,
        *,
        lock_wait: float,
        timeout: float,
    ) -> PreviewFile | str:
        """Ask the validator for one picture of the start page.

        Gives the picture, or the reason there is none: a validator reason, ``unknown`` (a null answer without a
        reason) or ``failed`` (transport error, refused request or an answer that breaks the contract). A picture
        is never required, so nothing is raised.
        """
        body = {"site_root": site_root, "entrypoint": entrypoint, "routing_mode": routing_mode, "lock_wait": lock_wait}
        try:
            async with self.client.stream(
                "POST",
                self.base_url + f"/results/{identifier}/preview",
                json=body,
                headers=self.headers,
                timeout=httpx.Timeout(timeout, connect=min(timeout, 5)),
            ) as response:
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_PREVIEW_RESPONSE:
                        return "failed"
                if response.status_code != httpx.codes.OK:
                    return "failed"
            return _preview_file(json.loads(data))
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.warning("Preview capture gave no picture: %s", type(exc).__name__)
            return "failed"

    async def close(self):
        await self.client.aclose()
