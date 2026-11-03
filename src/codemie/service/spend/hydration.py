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
"""Settle a persisted *pending* cost from the ledger when it is read (AD-28 rule 7).

A streamed turn is written with `money_spent_pending` because the LiteLLM streaming path returns no
cost; its ledger row lands minutes to hours later. Nothing re-prices (AD-22): this module asks the
spend port for the row by **the id matching the record's own grain** - a chat turn and a workflow
execution are request-level records, so the key is the request id and never a run id, whose use
here would write one call's cost into every call of the request.

Only a settled figure is ever written back. The port answers `unavailable` both when the ledger is
unactivated (permanent) and when a read failed (transient, AD-11 degrade); persisting that would
silence every later hydration after one failed read, so an unavailable answer is *presented* on
the response and the stored flags stay as they were. A record with no key at all - a turn
persisted before turns carried their request id - can never settle and is presented the same way:
no figure is coming for it, which is the literal meaning of the state (AD-28 rule 3b).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from codemie.service.spend.spend_service import spend_service

__all__ = ["Hydrated", "hydrate_request_cost"]


@dataclass(frozen=True, slots=True)
class Hydrated:
    """A record's money as it should be shown, and whether the ledger settled it on this read.

    `settled_now` is the only outcome a consumer persists; `pending` and `unavailable` are the
    flags to render, which for a presented-unavailable record differ from the stored ones.
    """

    money_spent: float | None
    pending: bool
    unavailable: bool
    settled_now: bool

    @classmethod
    def unchanged(cls, money_spent: float | None, *, pending: bool, unavailable: bool) -> Hydrated:
        return cls(money_spent=money_spent, pending=pending, unavailable=unavailable, settled_now=False)

    @classmethod
    def presented_unavailable(cls, money_spent: float | None) -> Hydrated:
        """Rendered as "final cost unavailable" on this read; nothing is stored."""
        return cls(money_spent=money_spent, pending=False, unavailable=True, settled_now=False)

    @classmethod
    def settled(cls, figure: Decimal) -> Hydrated:
        """The ledger's own figure replaces the stored subtotal - including a measured zero."""
        return cls(money_spent=float(figure), pending=False, unavailable=False, settled_now=True)


async def hydrate_request_cost(
    *,
    request_id: str | None,
    money_spent: float | None,
    pending: bool,
    unavailable: bool,
) -> Hydrated:
    """The money to show for one request-level record, settled from the ledger where it can be.

    A record that is not pending is returned as it is: a settled figure is never overwritten and
    an unavailable one is never re-asked (rule 7's suppression). The port already degrades a failed
    read to `unavailable`, so this never raises on a ledger problem.
    """
    if not pending or unavailable:
        return Hydrated.unchanged(money_spent, pending=pending, unavailable=unavailable)
    if request_id is None:
        return Hydrated.presented_unavailable(money_spent)

    cost = await spend_service.cost_for_request(request_id=request_id)
    if cost.is_settled:
        return Hydrated.settled(cost.figure)
    if cost.is_unavailable:
        return Hydrated.presented_unavailable(money_spent)
    return Hydrated.unchanged(money_spent, pending=pending, unavailable=unavailable)
