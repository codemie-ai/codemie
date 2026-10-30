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
"""The single retry owner for Exchange writes (independent of the read policy in retry.py).

One Deadline covers the whole tool call. A write is submitted at most MAX_SUBMISSIONS times and
only again after the transport classifier proved the previous attempt changed nothing.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

from codemie_tools.ms_exchange.errors import (
    DeadlineExhaustedError,
    ExchangeError,
    WriteRateLimitedError,
    WriteUnknownError,
)

T = TypeVar("T")

DEADLINE_SECONDS = 90.0
MAX_SUBMISSIONS = 3
MAX_LOCAL_DELAY_SECONDS = 30.0
_JITTER_BASE_SECONDS = 1.0  # full jitter: uniform(0, base * 2**(retry - 1))


class Deadline:
    """Client-side completion limit for one tool call; not a cancel or rollback promise."""

    def __init__(self, seconds: float = DEADLINE_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._end = clock() + seconds

    def remaining(self) -> float:
        return max(0.0, self._end - self._clock())

    def expired(self) -> bool:
        return self.remaining() <= 0.0


@dataclass(frozen=True)
class Retry:
    """The attempt was proven to change nothing; hint_seconds is the server's wait, if it gave one.

    ``on_exhausted`` is the error to raise when no submission or time is left to retry (default: rate limited,
    right for a throttled attempt). A retry that is not about throttling, such as a tag conflict, passes its own
    Not applied error so that running out of submissions does not report rate limiting.
    """

    hint_seconds: float | None = None
    on_exhausted: ExchangeError | None = None


@dataclass(frozen=True)
class Stop:
    """Do not submit again; raise this error."""

    error: ExchangeError


Classify = Callable[[BaseException], "Retry | Stop"]


def _valid_hint(hint: float | None) -> float | None:
    if hint is None or not math.isfinite(hint) or hint < 0:
        return None
    return float(hint)


def _wait_seconds(hint: float | None, retry_number: int, rand: Callable[[float, float], float]) -> float:
    server_wait = _valid_hint(hint)
    if server_wait is not None:
        return server_wait  # honored exactly: never shortened, not capped
    upper = _JITTER_BASE_SECONDS * 2 ** (retry_number - 1)
    return min(MAX_LOCAL_DELAY_SECONDS, rand(0, upper))


def _decide(exc: Exception, classify: Classify) -> Retry | Stop:
    try:
        decision = classify(exc)
    except Exception:
        if isinstance(exc, ExchangeError) and exc.OUTCOME:
            raise exc from None
        raise WriteUnknownError from None
    if not isinstance(decision, (Retry, Stop)):
        raise WriteUnknownError from None
    return decision


def run_write(
    submit: Callable[[], T],
    classify: Classify,
    deadline: Deadline,
    *,
    sleep: Callable[[float], None] = time.sleep,
    rand: Callable[[float, float], float] = random.uniform,
) -> T:
    """Call submit() until it succeeds, is stopped, or the 3-submission / deadline limits are hit.

    submit takes no arguments and runs one submission. classify maps the exception it raised to
    Retry (proven no mutation) or Stop(error). Never raises a raw exception from a submission.
    """
    for submission in range(1, MAX_SUBMISSIONS + 1):
        if deadline.expired():
            raise DeadlineExhaustedError() if submission == 1 else WriteRateLimitedError()
        try:
            return submit()
        except Exception as exc:
            decision = _decide(exc, classify)
            if isinstance(decision, Stop):
                raise decision.error from None
            exhausted = decision.on_exhausted or WriteRateLimitedError()
            if submission == MAX_SUBMISSIONS or deadline.expired():
                raise exhausted from None
            wait = _wait_seconds(decision.hint_seconds, submission, rand)
            if wait > deadline.remaining():
                raise exhausted from None
            sleep(wait)
    raise WriteUnknownError  # pragma: no cover - loop always returns or raises
