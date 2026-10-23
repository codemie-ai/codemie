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

"""Per-user limits on computations: at most N an hour for any period and settings, and for custom settings one at a
time and at most M an hour.

Only computations that actually run count: display-only overrides and cache hits never do, and a computation refused
before its worker starts (by these limits, by a busy server or by a shutdown) gives back what it counted. The counters
live in the shared cache (Redis, or in-process without it).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from codemie.service.usage_attribution.cache import SharedCache
from codemie.service.usage_attribution.constants import CACHE_NAMESPACE, CUSTOM_COMPUTATION_WINDOW_S


class CustomLimitError(Exception):
    """A computation would exceed the per-user limits.

    ``message`` is the 429 detail. ``note`` names the limit in a few words and ``option`` says what to do instead of
    waiting, for a page that keeps showing an earlier analysis (see ``refusal_note``).
    """

    def __init__(self, message: str, retry_after: int, note: str = "", option: str = ""):
        super().__init__(message)
        self.message = message
        self.retry_after = max(1, int(retry_after))
        self.note = note or "a limit on analyses is reached"
        self.option = option


@dataclass
class CustomSlot:
    user_id: str
    token: str


@dataclass
class Admission:
    """What a computation counted against its user's limits, and the custom slot it holds."""

    user_id: str
    custom: CustomSlot | None = None
    # Set when its worker starts: from then on the computation counts.
    started: bool = False


def _active_key(user_id: str) -> str:
    return f"{CACHE_NAMESPACE}:custom:active:{user_id}"


def _count_key(user_id: str) -> str:
    return f"{CACHE_NAMESPACE}:custom:count:{user_id}"


def _all_count_key(user_id: str) -> str:
    return f"{CACHE_NAMESPACE}:computations:count:{user_id}"


def _amount(n: int, word: str, plural: str = "") -> str:
    return f"{n} {word if n == 1 else plural or word + 's'}"


async def count_computation(cache: SharedCache, user_id: str, max_per_hour: int) -> None:
    """Count a computation the user starts (a period or settings not cached yet); raises CustomLimitError (429)."""
    count, left = await cache.incr(_all_count_key(user_id), CUSTOM_COMPUTATION_WINDOW_S)
    if count > max_per_hour:
        await cache.decr(_all_count_key(user_id))
        raise CustomLimitError(
            f"You started {max_per_hour} analyses in the last hour, the most allowed. Choose a period that was "
            "already analysed, or wait.",
            retry_after=math.ceil(left),
            note=f"the limit of {_amount(max_per_hour, 'analysis', 'analyses')} an hour is reached",
            option="choose a period that was already analysed",
        )


async def start_custom_computation(cache: SharedCache, user_id: str, max_per_hour: int, timeout_s: int) -> CustomSlot:
    """Take the user's single slot and count the computation; raises CustomLimitError (429)."""
    token = await cache.acquire(_active_key(user_id), timeout_s + 30)
    if token is None:
        left = await cache.ttl(_active_key(user_id))
        raise CustomLimitError(
            "A computation with your custom settings is already running. Wait for it to finish.",
            retry_after=min(30, math.ceil(left) or 10),
            note="a computation with your custom settings is already running",
        )
    count, left = await cache.incr(_count_key(user_id), CUSTOM_COMPUTATION_WINDOW_S)
    if count > max_per_hour:
        await cache.decr(_count_key(user_id))
        await cache.release(_active_key(user_id), token)
        raise CustomLimitError(
            f"You started {max_per_hour} computations with custom settings in the last hour, the most allowed. "
            "Reset to the defaults or wait.",
            retry_after=math.ceil(left),
            note=f"the limit of {_amount(max_per_hour, 'computation')} with custom settings an hour is reached",
            option="reset to the defaults",
        )
    return CustomSlot(user_id=user_id, token=token)


async def finish_custom_computation(cache: SharedCache, slot: CustomSlot | None) -> None:
    if slot is not None:
        await cache.release(_active_key(slot.user_id), slot.token)


async def admit_computation(
    cache: SharedCache, user_id: str, max_per_hour: int, custom: tuple[int, int] | None = None
) -> Admission:
    """Count a computation against the user's hourly budget and, with custom settings (``custom`` is their hourly
    limit and how long the slot is held), take the user's single slot and count it there too.

    Raises CustomLimitError (429) with nothing counted. Pass the admission to ``end_computation`` once it is over.
    """
    await count_computation(cache, user_id, max_per_hour)
    if custom is None:
        return Admission(user_id)
    try:
        slot = await start_custom_computation(cache, user_id, *custom)
    except CustomLimitError:
        await cache.decr(_all_count_key(user_id))
        raise
    return Admission(user_id, slot)


async def end_computation(cache: SharedCache, admission: Admission | None) -> None:
    """Release the custom slot. A computation refused before its worker started gives back what it counted."""
    if admission is None:
        return
    if not admission.started:
        await cache.decr(_all_count_key(admission.user_id))
        if admission.custom is not None:
            await cache.decr(_count_key(admission.user_id))
    await finish_custom_computation(cache, admission.custom)


def wait_text(seconds: float) -> str:
    """How long to wait, in words: seconds under a minute, otherwise minutes rounded up."""
    left = max(1, math.ceil(seconds))
    return _amount(left, "second") if left < 60 else _amount(math.ceil(left / 60), "minute")


def refusal_note(limit: CustomLimitError) -> str:
    """The refusal in a few words with the time to wait, to follow "Showing the analysis from {time}: "."""
    text = f"{limit.note}; try again in {wait_text(limit.retry_after)}"
    return f"{text}, or {limit.option}." if limit.option else f"{text}."
