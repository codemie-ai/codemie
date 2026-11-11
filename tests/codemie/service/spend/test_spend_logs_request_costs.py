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

"""A page of requests costed from the spend-logs ledger, as figures over real rows."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from codemie.enterprise.litellm.spend_ledger import SpendLogsLedger
from codemie.repository.spend_logs_model import LiteLLMSpendLog as Row
from codemie.service.spend.coverage import LedgerCoverage
from codemie.service.spend.ledger import RequestCost

ACTIVATED = LedgerCoverage(coverage_floor=datetime(2026, 1, 1, tzinfo=timezone.utc), ready=True)
CALLED_AT = datetime(2026, 8, 10, tzinfo=timezone.utc)


class _SyncSessionAsAsync:
    """The repository's `AsyncSession` surface over a synchronous SQLite session, counting statements."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self.statements = 0

    async def execute(self, statement):
        self.statements += 1
        return self._session.execute(statement)


def _call(row_id: str, codemie_request_id: str | None, spend: str) -> Row:
    """One LLM call's spend-log row; a CodeMie request with several calls has several of these."""
    return Row(
        request_id=row_id,
        start_time=CALLED_AT,
        end_time=CALLED_AT,
        spend=Decimal(spend),
        status="success",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        attr_source="explicit",
        attr_form="principal",
        attr_user_id="u-1",
        codemie_request_id=codemie_request_id,
    )


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Row.metadata.create_all(engine, tables=[Row.__table__])
    with Session(engine) as sync_session:
        sync_session.add_all(
            [
                _call("call-1", "q-1", "0.25"),
                _call("call-2", "q-1", "0.50"),
                _call("call-3", "q-failed", "0"),
                _call("call-4", "q-other", "9.00"),
                _call("call-5", None, "7.00"),
            ]
        )
        sync_session.commit()
        yield _SyncSessionAsAsync(sync_session)


def _costs(session, request_ids, coverage=ACTIVATED) -> dict[str, RequestCost]:
    @asynccontextmanager
    async def session_factory():
        yield session

    ledger = SpendLogsLedger(coverage, session_factory=session_factory)
    return asyncio.run(ledger.costs_for_requests(request_ids))


class TestTheSpendLogsLedgerCostsAPageOfRequests:
    def test_a_request_costs_the_sum_of_its_calls(self, session):
        assert _costs(session, ["q-1"]) == {"q-1": RequestCost.settled(Decimal("0.75"))}

    def test_a_failed_request_is_a_settled_zero(self, session):
        assert _costs(session, ["q-failed"]) == {"q-failed": RequestCost.settled(Decimal("0"))}

    def test_a_request_with_no_row_yet_is_pending(self, session):
        assert _costs(session, ["q-streaming"]) == {"q-streaming": RequestCost.pending()}

    def test_every_request_on_the_page_is_answered_from_one_statement(self, session):
        costs = _costs(session, ["q-1", "q-failed", "q-streaming"])

        assert costs == {
            "q-1": RequestCost.settled(Decimal("0.75")),
            "q-failed": RequestCost.settled(Decimal("0")),
            "q-streaming": RequestCost.pending(),
        }
        assert session.statements == 1

    def test_an_unactivated_ledger_is_unavailable_without_reading(self, session):
        costs = _costs(session, ["q-1"], coverage=LedgerCoverage())

        assert costs == {"q-1": RequestCost.unavailable()}
        assert session.statements == 0

    def test_no_ids_reads_nothing(self, session):
        assert _costs(session, []) == {}
        assert session.statements == 0
