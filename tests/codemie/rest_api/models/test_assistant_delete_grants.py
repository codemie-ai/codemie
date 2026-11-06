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


from unittest.mock import MagicMock, patch

import pytest

from codemie.core.ability import Action
from codemie.rest_api.models.assistant import Assistant
from codemie.service.assistant import assistant_share_service as share_module

ASSISTANT_ID = "00000000-0000-0000-0000-000000000001"


def _assistant():
    a = MagicMock()
    a.id = ASSISTANT_ID
    a.project = "proj1"
    return a


def _rows(n=2):
    return [MagicMock(principal_type="USER", principal_id=f"uid-{i}", permission_level=Action.READ) for i in range(n)]


@pytest.fixture()
def delete_env():
    """Patch everything delete_assistant touches besides the share service."""
    with (
        patch("codemie.service.guardrail.guardrail_service.GuardrailService.remove_guardrail_assignments_for_entity"),
        patch("codemie.rest_api.models.settings.Settings.prune_ms_teams_assistant_id", return_value=0),
        patch(
            "codemie.service.settings.scheduler_settings_service.SchedulerSettingsService."
            "delete_integrations_by_resource",
            return_value=0,
        ),
        patch.object(Assistant, "find_by_id", return_value=_assistant()) as find,
    ):
        yield find.return_value


def _share_env(rows, insert_strict=None):
    session = MagicMock()
    session.execute.return_value.all.return_value = rows
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    # delete_assistant owns the transaction; the share service only uses the session it is handed.
    return (
        session,
        patch("codemie.rest_api.models.assistant.Session", return_value=ctx),
        patch.object(share_module.activity_event_repository, "insert_strict", side_effect=insert_strict),
    )


def test_delete_assistant_removes_grants_with_one_event_each_before_deleting(delete_env):
    session, session_patch, strict_patch = _share_env(_rows(2))
    user = MagicMock(id="owner-1", username="owner")
    calls = []
    session.delete.side_effect = lambda obj: calls.append("delete")
    with session_patch, strict_patch as strict:
        strict.side_effect = lambda *a, **k: calls.append("event")
        Assistant.delete_assistant("assistant-1", user=user)

    assert calls == ["event", "event", "delete"]
    session.delete.assert_called_once_with(delete_env)
    delete_env.delete.assert_not_called()  # the row goes through the caller's session, not its own commit
    session.commit.assert_called_once()
    assert all(c.args[0].actor_id == "owner-1" for c in strict.call_args_list)


def test_failed_audit_insert_aborts_assistant_delete(delete_env):
    session, session_patch, strict_patch = _share_env(_rows(2), insert_strict=RuntimeError("audit down"))
    with session_patch, strict_patch, pytest.raises(RuntimeError, match="audit down"):
        Assistant.delete_assistant("assistant-1", user=MagicMock(id="owner-1", username="owner"))

    session.commit.assert_not_called()
    session.delete.assert_not_called()


def test_delete_without_user_records_system_actor(delete_env):
    session, session_patch, strict_patch = _share_env(_rows(1))
    with session_patch, strict_patch as strict:
        Assistant.delete_assistant("assistant-1")

    event = strict.call_args.args[0]
    assert event.actor_id is None
    assert event.attributes["actor_username"] == "system"
    session.delete.assert_called_once_with(delete_env)


def test_failed_assistant_delete_rolls_back_grant_removal(delete_env):
    session, session_patch, strict_patch = _share_env(_rows(2))
    session.delete.side_effect = RuntimeError("db down")
    with session_patch, strict_patch, pytest.raises(RuntimeError, match="db down"):
        Assistant.delete_assistant("assistant-1", user=MagicMock(id="owner-1", username="owner"))

    session.commit.assert_not_called()
