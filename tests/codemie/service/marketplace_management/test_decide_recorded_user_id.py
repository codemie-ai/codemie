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

"""`decide()` writes only a recorded users id into its users-FK columns (EPMCDME-17100).

Database-free: the session is a mock, so these tests see exactly what `decide()` hands to the CAS UPDATE,
the review-decision ledger row and the activity event. The PostgreSQL round-trip lives in
`test_withdraw_without_users_row_pg.py`.
"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from codemie.service.marketplace_management import queue_service
from codemie.service.marketplace_management.models import MarketplaceReviewDecision
from codemie.service.marketplace_management.queue_service import MarketplaceQueueService

_CURRENT = {
    "marketplace_status": "APPROVED",
    "marketplace_version": 3,
    "marketplace_decided_by": None,
    "marketplace_decided_at": None,
    "name": "Helper",
    "is_global": True,
}
_UPDATED = {
    "to_version": 4,
    "marketplace_status": "WITHDRAWN",
    "marketplace_submitted_at": None,
    "is_global": False,
    "is_verified": False,
    "name": "Helper",
    "project": "acme",
}


def _result(row):
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    return result


def _withdraw(*, recorded_id, decided_by_email, actor="alice"):
    """Run `withdraw()` against a mock session; return what reached each write."""
    session = MagicMock()
    session.exec.side_effect = [_result(_CURRENT), _result(_UPDATED)]

    @contextmanager
    def _get_session():
        yield session

    service = MarketplaceQueueService()
    with (
        patch.object(queue_service, "get_session", _get_session),
        patch.object(queue_service, "track_marketplace_metric") as track_metric,
        patch.object(MarketplaceQueueService, "_find_replay", return_value=None),
        patch.object(MarketplaceQueueService, "_side_effects", return_value=None),
        patch.object(MarketplaceQueueService, "_derive_key", return_value="idem") as derive_key,
        patch.object(MarketplaceQueueService, "_record_activity_event") as record_event,
        patch.object(MarketplaceQueueService, "_recorded_user_id", return_value=recorded_id) as recorded_user_id,
    ):
        service.withdraw(assistant_id="a-1", actor=actor, decided_by_id="u-raw", decided_by_email=decided_by_email)

    cas_params = session.exec.call_args_list[1].kwargs["params"]
    (ledger,) = [c.args[0] for c in session.add.call_args_list if isinstance(c.args[0], MarketplaceReviewDecision)]
    return SimpleNamespace(
        session=session,
        cas_id=cas_params["decided_by_id"],
        ledger=ledger,
        event=record_event.call_args.kwargs,
        derive_key=derive_key,
        recorded_user_id=recorded_user_id,
        metric=track_metric.call_args.kwargs,
    )


def test_an_id_without_a_users_row_is_written_as_null_and_the_email_kept():
    out = _withdraw(recorded_id=None, decided_by_email="e@x.io")

    assert (out.cas_id, out.ledger.decided_by_id, out.event["actor_id"]) == (None, None, None)
    assert (out.ledger.decided_by_email, out.event["actor_email"]) == ("e@x.io", "e@x.io")


def test_an_id_without_a_users_row_and_no_email_records_the_actor_string():
    out = _withdraw(recorded_id=None, decided_by_email=None, actor="bob")

    assert (out.cas_id, out.ledger.decided_by_id, out.event["actor_id"]) == (None, None, None)
    assert (out.ledger.decided_by_email, out.event["actor_email"]) == ("bob", "bob")


def test_the_metric_keeps_the_raw_actor_values_when_the_email_falls_back():
    out = _withdraw(recorded_id=None, decided_by_email=None, actor="bob")

    assert (out.metric["actor_id"], out.metric["actor_email"]) == ("u-raw", None)


def test_an_id_with_a_users_row_is_kept_and_no_email_is_invented():
    out = _withdraw(recorded_id="u-raw", decided_by_email=None)

    assert (out.cas_id, out.ledger.decided_by_id, out.event["actor_id"]) == ("u-raw", "u-raw", "u-raw")
    assert (out.ledger.decided_by_email, out.event["actor_email"]) == (None, None)


@pytest.mark.parametrize(("recorded_id", "decided_by_email"), [(None, "e@x.io"), (None, None), ("u-raw", None)])
def test_the_idempotency_key_and_the_lookup_use_the_raw_id(recorded_id, decided_by_email):
    out = _withdraw(recorded_id=recorded_id, decided_by_email=decided_by_email)

    assert out.derive_key.call_args.kwargs["decided_by_id"] == "u-raw"
    out.recorded_user_id.assert_called_once_with(out.session, "u-raw")
