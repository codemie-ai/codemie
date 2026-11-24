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


"""`_record_activity_event` stamps project/label and the actor snapshot without ever raising (EPMCDME-14325)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from codemie.service.activity.activity_models import SYSTEM_ACTOR_ID
from codemie.service.marketplace_management.models import EntityKind, ReviewDecision
from codemie.service.marketplace_management.queue_service import TRANSITIONS, MarketplaceQueueService
from codemie.rest_api.models.user_management import UserDB

REPO = "codemie.service.marketplace_management.queue_service.activity_event_repository"


def _session(*, user=None):
    session = MagicMock()
    session.get.side_effect = lambda model, _id: {UserDB: user}[model]
    return session


def _record(session, **kwargs):
    kwargs.setdefault("actor", "alice@example.com")
    kwargs.setdefault("actor_id", "u-1")
    kwargs.setdefault("entity_kind", EntityKind.ASSISTANT)
    kwargs.setdefault("entity_label", "Helper")
    kwargs.setdefault("project_name", "acme")
    with patch(REPO) as repo:
        MarketplaceQueueService._record_activity_event(
            session,
            rule=TRANSITIONS[ReviewDecision.APPROVE],
            entity_id="a-1",
            **kwargs,
        )
    repo.insert.assert_called_once()
    return repo.insert.call_args.args[0]


def test_stamps_project_name_and_entity_label_it_is_given():
    event = _record(_session(user=SimpleNamespace(email="a@x.io", name="Alice")))

    assert (event.project_name, event.entity_label) == ("acme", "Helper")


def test_an_entity_less_kind_carries_no_entity_label_or_project():
    event = _record(_session(user=SimpleNamespace(email="a@x.io", name="Alice")), entity_kind=EntityKind.WORKFLOW)

    assert (event.entity_type, event.entity_id, event.entity_label, event.project_name) == (None, None, None, None)


def test_actor_email_and_display_name_come_from_the_users_row():
    event = _record(_session(user=SimpleNamespace(email="a@x.io", name="Alice")))

    assert (event.actor_id, event.actor_email, event.actor_display_name) == ("u-1", "a@x.io", "Alice")


def test_passed_email_wins_over_the_users_row_and_blank_name_becomes_none():
    event = _record(_session(user=SimpleNamespace(email="row@x.io", name="")), actor_email="passed@x.io")

    assert (event.actor_email, event.actor_display_name) == ("passed@x.io", None)


def test_user_row_without_name_for_a_caller_without_email_still_records():
    event = _record(_session(user=SimpleNamespace(email="row@x.io", name=None)), actor="bob")

    assert (event.actor_id, event.actor_email, event.actor_display_name) == ("u-1", "row@x.io", None)


def test_unresolvable_email_falls_back_to_an_email_shaped_actor_string():
    event = _record(_session(user=None), actor="alice@example.com")

    assert (event.actor_id, event.actor_email) == (None, "alice@example.com")


def test_missing_users_row_drops_actor_id_even_with_passed_email():
    event = _record(_session(user=None), actor_email="p@x.io")

    assert (event.actor_id, event.actor_email) == (None, "p@x.io")


def test_unresolvable_email_drops_actor_id_instead_of_raising():
    event = _record(_session(user=None), actor="a-username")

    assert (event.actor_id, event.actor_email, event.actor_display_name) == (None, None, None)
    assert event.attributes["actor"] == "a-username"


def test_missing_actor_is_not_looked_up():
    session = _session()
    event = _record(session, actor="system", actor_id=None)

    assert (event.actor_id, event.actor_email, event.actor_display_name) == (None, None, None)
    assert session.get.call_args_list == []


def test_system_actor_without_users_row_is_looked_up_and_dropped():
    session = _session()
    event = _record(session, actor="system", actor_id=SYSTEM_ACTOR_ID)

    assert (event.actor_id, event.actor_email, event.actor_display_name) == (None, None, None)
    assert session.get.call_args_list == [call(UserDB, SYSTEM_ACTOR_ID)]


def test_a_users_row_is_looked_up_once_and_supplies_the_snapshot():
    session = _session(user=SimpleNamespace(email="a@x.io", name="Alice"))
    event = _record(session)

    assert (event.actor_id, event.actor_email, event.actor_display_name) == ("u-1", "a@x.io", "Alice")
    assert session.get.call_args_list == [call(UserDB, "u-1")]


def test_recorded_user_id_keeps_an_id_that_has_a_users_row():
    session = _session(user=SimpleNamespace(email="a@x.io", name="Alice"))

    assert MarketplaceQueueService._recorded_user_id(session, "u-1") == "u-1"


def test_recorded_user_id_drops_an_id_without_a_users_row():
    assert MarketplaceQueueService._recorded_user_id(_session(user=None), "u-1") is None


def test_recorded_user_id_does_not_look_up_a_missing_id():
    session = _session()

    assert MarketplaceQueueService._recorded_user_id(session, None) is None
    assert session.get.call_args_list == []
