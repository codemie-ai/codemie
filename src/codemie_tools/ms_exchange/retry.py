# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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
"""Bounded retry for transient Exchange failures (Graph and EWS)."""

from __future__ import annotations

import time
from typing import Callable, TypeVar

from codemie_tools.ms_exchange.errors import graph_status

T = TypeVar("T")

MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 1.0
MAX_RETRY_AFTER_SECONDS = 30
MAX_TOTAL_SECONDS = 90  # wall-clock budget across all attempts; no retry starts a sleep past it
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR_MIN = 500


def _retry_after(exc: BaseException) -> float | None:
    headers = getattr(exc, "response_headers", None) or {}
    for key, value in dict(headers).items():
        if str(key).lower() == "retry-after":
            if isinstance(value, (list, tuple, set)):
                value = next(iter(value), None)
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _transient_delay(exc: BaseException) -> float | None:
    """None if the error must not be retried; else the server hint (0 when absent)."""
    from exchangelib import errors as ews

    if isinstance(exc, ews.ErrorServerBusy):
        return float(exc.back_off or 0)
    if isinstance(exc, (ews.ErrorInternalServerTransientError, ews.ErrorTimeoutExpired)):
        return 0.0
    status = graph_status(exc)
    if status is not None and (status == _HTTP_TOO_MANY_REQUESTS or status >= _HTTP_SERVER_ERROR_MIN):
        return _retry_after(exc) or 0.0
    return None


def call_with_retry(fn: Callable[[], T]) -> T:
    """Call fn, retrying only transient errors; the last original error is re-raised."""
    attempt = 0
    started = time.monotonic()
    while True:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - classified below, re-raised if not transient
            hint = _transient_delay(exc)
            if hint is None or attempt >= MAX_RETRIES:
                raise
            delay = min(hint if hint > 0 else BASE_BACKOFF_SECONDS * (2**attempt), MAX_RETRY_AFTER_SECONDS)
            if time.monotonic() - started + delay >= MAX_TOTAL_SECONDS:
                raise
            time.sleep(delay)
            attempt += 1
