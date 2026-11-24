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

"""The optional start-page picture of an accepted version: the capture request, its checks and the pointer.

A picture is never required: every failure gives no picture, and none of it reaches the verdict.
The pointer to a stored picture lives in columns of the version row, not in the content manifest.
"""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import suppress
from typing import NamedTuple

from codemie.configs.logger import logger
from codemie.core.hosted_applications.binary import dimensions, image
from codemie.core.hosted_applications.preview import MAX_PREVIEW_BYTES, MAX_PREVIEW_SIDE, PREVIEW_NAME
from codemie.core.hosted_applications.validation import BundleCandidate
from codemie.hosted_apps_validator.contract import (
    CAPTURE_HARD_SECONDS,
    CAPTURE_LOCK_WAIT_SECONDS,
    CAPTURE_REASONS,
    MAX_CAPTURE_LOCK_WAIT_SECONDS,
    SECONDS_PER_10_MIB,
    SECONDS_PER_FILE,
)
from codemie.service.hosted_applications.validator_client import PreviewFile, ValidatorClient

# Seconds beyond the renderer's own limits: the renderer kill and its pipe wait (up to 1 s), the parser start wait
# (up to 0.5 s), the stray sweep and the removal of the scratch directory, and the pull of a picture of at most 1 MiB.
CAPTURE_OVERHEAD_SECONDS = 4
# The shortest capture request and pull: the validator's least wait for its renderer plus the hard limit.
CAPTURE_REQUEST_SECONDS = CAPTURE_LOCK_WAIT_SECONDS + CAPTURE_HARD_SECONDS + CAPTURE_OVERHEAD_SECONDS
# Time kept for the writes that follow a capture: this base plus the validation budget's own per-file and
# per-byte rates for the files to write (contract.validation_budget).
WRITE_BASE_SECONDS = 10
# What a client may hand back for a null answer: a validator reason, or the answer of an older validator.
PREVIEW_VALIDATOR_RESULTS = (*CAPTURE_REASONS, "unknown", "failed")
_MIB = 1024 * 1024


class PreviewPointer(NamedTuple):
    """Where a stored picture is and what it must hash to; the three version columns hold these values."""

    key: str
    size: int
    sha256: bytes  # The 32 raw digest bytes.


# The picture's bytes and its pointer.
Picture = tuple[bytes, PreviewPointer]


def write_seconds(candidate: BundleCandidate) -> float:
    """Seconds the report, every validated file and the manifest may take to write after a capture."""
    return (
        WRITE_BASE_SECONDS
        + SECONDS_PER_FILE * len(candidate.files)
        + SECONDS_PER_10_MIB * candidate.total_bytes / (10 * _MIB)
    )


def pointer(prefix: str, found: PreviewFile) -> PreviewPointer:
    """The pointer for a picture stored at ``<prefix>/preview.jpg``."""
    return PreviewPointer(f"{prefix}/{PREVIEW_NAME}", found.size, bytes.fromhex(found.sha256))


def _checked(body: bytes, found: PreviewFile) -> bool:
    """Whether ``body`` is exactly the JPEG the validator described, within the preview bounds."""
    return (
        len(body) == found.size
        and hashlib.sha256(body).hexdigest() == found.sha256
        and image(body, ".jpg")
        and dimensions(body, ".jpg") == (found.width, found.height)
        and 1 <= found.width <= MAX_PREVIEW_SIDE
        and 1 <= found.height <= MAX_PREVIEW_SIDE
    )


def record_outcome(result: str) -> None:
    """Count one automatic cover outcome. Never raises."""
    from codemie.service.hosted_applications.observability import hosted_request_monitoring

    with suppress(Exception):
        hosted_request_monitoring().preview_capture(result)


async def capture_preview(  # noqa: PLR0913 - keyword-only budget flags after the capture inputs
    validator: ValidatorClient,
    identifier: str,
    chosen: tuple[str, str, str],
    prefix: str,
    *,
    spare: float,
    retried: bool,
) -> Picture | None:
    """One checked picture of the start page, or None. Never raises.

    ``spare`` is the job budget still unused less ``write_seconds`` for the output writes after the capture.
    The validator may wait for its renderer for the part of ``spare`` that the capture itself does not need, up to
    MAX_CAPTURE_LOCK_WAIT_SECONDS. A capture starts only when that wait is at least CAPTURE_LOCK_WAIT_SECONDS, so the
    optional picture can never make the job overrun its budget. A retried job captures nothing, so a capture that
    crashed the validator cannot repeat.

    Every outcome without a picture is counted here, once. The worker counts the picture that it stores or
    leaves out for lack of room.
    """
    if retried:
        record_outcome("skipped_retry")
        return None
    lock_wait = min(MAX_CAPTURE_LOCK_WAIT_SECONDS, spare - CAPTURE_HARD_SECONDS - CAPTURE_OVERHEAD_SECONDS)
    if lock_wait < CAPTURE_LOCK_WAIT_SECONDS:
        record_outcome("skipped_budget")
        return None
    root, entry, mode = chosen
    seconds = lock_wait + CAPTURE_HARD_SECONDS + CAPTURE_OVERHEAD_SECONDS
    try:
        async with asyncio.timeout(seconds):
            found = await validator.capture(identifier, root, entry, mode, lock_wait=lock_wait, timeout=seconds)
            if not isinstance(found, PreviewFile):
                record_outcome(found if found in PREVIEW_VALIDATOR_RESULTS else "failed")
                return None
            body = bytearray()
            async for chunk in validator.file_chunks(identifier, found.index, found):
                body.extend(chunk)
                if len(body) > min(found.size, MAX_PREVIEW_BYTES):
                    raise ValueError("The picture is longer than announced")
    except Exception as exc:  # A picture is optional: no failure of the capture may reach the verdict.
        logger.warning("Preview capture gave no picture: %s", type(exc).__name__)
        record_outcome("failed")
        return None
    body = bytes(body)
    if not _checked(body, found):
        logger.warning("Preview capture gave no picture: %s", "PreviewMismatch")
        record_outcome("mismatch")
        return None
    return body, pointer(prefix, found)
