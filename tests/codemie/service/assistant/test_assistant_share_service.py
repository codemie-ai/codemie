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

"""Assistant sharing on the shared named-grant module: delegation, assistant-only rules, response shapes."""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from codemie.configs import config
from codemie.core.ability import Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.permission import PrincipalType
from codemie.rest_api.models.settings import SettingType
from codemie.service.activity.activity_models import ActivityDomain, ActivityEntityType, AssistantEvent
from codemie.service.assistant.assistant_share_service import AssistantShareService

MODULE = "codemie.service.assistant.assistant_share_service"
ASSISTANT_ID = "00000000-0000-0000-0000-000000000001"


def _owner(user_id="owner-1"):
    u = MagicMock()
    u.id = user_id
    u.username = "owner"
    u.name = "Owner"
    u.is_external_user = False
    u.project_names = []
    return u


def _assistant(owner_id="owner-1", toolkits=None, mcp_servers=None, bedrock=None, bedrock_agentcore_runtime=None):
    a = MagicMock()
    a.id = ASSISTANT_ID
    a.project = "proj"
    a.toolkits = toolkits or []
    a.mcp_servers = mcp_servers or []
    a.bedrock = bedrock
    a.bedrock_agentcore_runtime = bedrock_agentcore_runtime
    a.is_owned_by = lambda u: u.id == owner_id
    a.is_global = False
    a.shared = False
    return a


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None

    def scalars(self):
        return self


class _Session:
    """Answers statements in order; the first answer of a grant/revoke is the locked assistant row."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.statements, self.added = [], []
        self.commits = 0

    def execute(self, statement):
        self.statements.append(statement)
        return _Result(self.answers.pop(0) if self.answers else [])

    def add_all(self, rows):
        self.added.extend(rows)

    def flush(self):
        pass

    def commit(self):
        self.commits += 1


@contextmanager
def _env(*answers, settings=None):
    session = _Session(*answers)
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    with (
        patch(f"{MODULE}.Session", return_value=ctx),
        patch(f"{MODULE}.BaseModelWithSQLSupport.get_engine"),
        patch(f"{MODULE}.Settings.get_by_ids", return_value=settings or []) as get_settings,
        patch.object(config, "ACTIVITY_EVENTS_ENABLED", True),
    ):
        yield session, get_settings


def _user_row(uid, eligible=True):
    return SimpleNamespace(id=uid, eligible=eligible, grant_source=None, source_link_id=None)


def _upserted(uid, inserted=True):
    return SimpleNamespace(
        id="p", principal_type=PrincipalType.USER, principal_id=uid, permission_level=Action.READ, inserted=inserted
    )


def _deleted(uid):
    return SimpleNamespace(
        principal_type=PrincipalType.USER,
        principal_id=uid,
        permission_level=Action.READ,
        grant_source="name",
        source_link_id=None,
    )


LISTED = [{"id": "uid-1", "username": "u", "email": None, "name": "U", "picture": None, "grant_source": "name"}]


@pytest.mark.parametrize(
    "call",
    [
        lambda o, a: AssistantShareService.grant(o, a, ["uid-1"]),
        lambda o, a: AssistantShareService.revoke(o, a, ["uid-1"]),
        lambda o, a: AssistantShareService.list_for_owner(o, a),
        lambda o, a: asyncio.run(AssistantShareService.search_sharable_users_for_owner(o, a, "al")),
    ],
)
def test_public_methods_reject_non_owner_with_403(call):
    with _env() as (session, _):
        with pytest.raises(ExtendedHTTPException) as exc:
            call(_owner("other"), _assistant("owner-1"))
    assert exc.value.code == 403
    assert exc.value.message == "Only the assistant owner can share or revoke access"
    assert session.statements == [] and session.added == []


def test_grant_locks_the_assistant_then_runs_the_shared_statements_and_commits():
    locked = _assistant()
    with (
        _env([locked], [_user_row("uid-1")], [_upserted("uid-1")]) as (session, _),
        patch.object(AssistantShareService, "list_shared_with", return_value=LISTED),
    ):
        assert AssistantShareService.grant(_owner(), _assistant(), ["uid-1", "uid-1"]) == LISTED
    lock_sql = str(session.statements[0].compile(dialect=postgresql.dialect()))
    assert "FOR NO KEY UPDATE" in lock_sql and "assistants" in lock_sql
    upsert_sql = str(session.statements[2].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT ON CONSTRAINT unique_resource_principal DO UPDATE" in upsert_sql
    assert session.commits == 1 and len(session.added) == 1


def test_grant_of_a_concurrently_deleted_assistant_is_404():
    with _env([]) as (session, _):
        with pytest.raises(ExtendedHTTPException) as exc:
            AssistantShareService.grant(_owner(), _assistant(), ["uid-1"])
    assert exc.value.code == 404 and session.commits == 0


@pytest.mark.parametrize("eligible", [False])
def test_grant_ineligible_user_raises_400_and_writes_nothing(eligible):
    with _env([_assistant()], [_user_row("uid-x", eligible=eligible)]) as (session, _):
        with pytest.raises(ExtendedHTTPException) as exc:
            AssistantShareService.grant(_owner(), _assistant(), ["uid-x"])
    assert exc.value.code == 400
    assert len(session.statements) == 2 and session.commits == 0 and session.added == []


def test_grant_with_one_unknown_id_writes_nothing():
    with _env([_assistant()], [_user_row("uid-1")]) as (session, _):
        with pytest.raises(ExtendedHTTPException) as exc:
            AssistantShareService.grant(_owner(), _assistant(), ["uid-1", "uid-missing"])
    assert exc.value.code == 404
    assert session.commits == 0 and session.added == []


def test_grant_existing_row_is_skipped_without_event():
    with (
        _env([_assistant()], [_user_row("uid-1"), _user_row("uid-2")], [_upserted("uid-2")]) as (session, _),
        patch.object(AssistantShareService, "list_shared_with", return_value=[]),
    ):
        AssistantShareService.grant(_owner(), _assistant(), ["uid-1", "uid-2"])
    assert [event.attributes["principal_id"] for event in session.added] == ["uid-2"]


def test_grant_audit_failure_rolls_back():
    with (
        _env([_assistant()], [_user_row("uid-1")], [_upserted("uid-1")]) as (session, _),
        patch(
            "codemie.service.permission.named_grant_service.activity_event_repository.insert_many_strict",
            side_effect=RuntimeError("db down"),
        ),
    ):
        with pytest.raises(RuntimeError):
            AssistantShareService.grant(_owner(), _assistant(), ["uid-1"])
    assert session.commits == 0


def test_grant_event_payload_keeps_the_assistant_contract():
    with (
        _env([_assistant()], [_user_row("uid-1")], [_upserted("uid-1")]) as (session, _),
        patch.object(AssistantShareService, "list_shared_with", return_value=[]),
    ):
        AssistantShareService.grant(_owner(), _assistant(), ["uid-1"])
    event = session.added[0]
    assert event.domain == ActivityDomain.ASSISTANT
    assert event.event_type == AssistantEvent.GRANT_ADDED
    assert event.entity_type == ActivityEntityType.ASSISTANT
    assert event.entity_id == ASSISTANT_ID
    assert event.actor_id == "owner-1"
    attrs = event.attributes
    assert attrs["actor_id"] == "owner-1"
    assert attrs["actor_username"] == "owner"
    assert attrs["principal_type"] == "USER"
    assert attrs["principal_id"] == "uid-1"
    assert attrs["assistant_id"] == ASSISTANT_ID
    assert attrs["resource_type"] == "assistant" and attrs["resource_id"] == ASSISTANT_ID
    assert attrs["project"] == "proj"
    assert attrs["grant_source"] == "name"
    assert attrs["before"] is None
    assert attrs["after"] == "read"
    assert attrs["occurred_at"].endswith("+00:00")


def test_revoke_writes_event_only_for_deleted_rows():
    with (
        _env([_assistant()], [_deleted("uid-1")]) as (session, _),
        patch.object(AssistantShareService, "list_shared_with", return_value=[]),
    ):
        AssistantShareService.revoke(_owner(), _assistant(), ["uid-1", "uid-nobody"])
    assert len(session.added) == 1
    event = session.added[0]
    assert event.event_type == AssistantEvent.GRANT_REMOVED
    assert event.attributes["before"] == "read" and event.attributes["after"] is None
    assert session.commits == 1


def test_remove_all_grants_audits_each_deleted_row_and_commits_its_own_session():
    with _env([_deleted("uid-1"), _deleted("uid-2")]) as (session, _):
        AssistantShareService.remove_all_grants(_owner(), ASSISTANT_ID)
    assert len(session.added) == 2 and session.commits == 1


def test_remove_all_grants_with_caller_session_does_not_commit():
    session = _Session([_deleted("uid-1")])
    with patch.object(config, "ACTIVITY_EVENTS_ENABLED", True):
        AssistantShareService.remove_all_grants(_owner(), ASSISTANT_ID, "proj", session)
    assert len(session.added) == 1 and session.commits == 0


def test_list_shared_with_keeps_orphans_and_adds_grant_source():
    rows = [
        SimpleNamespace(
            principal_id="uid-gone", id=None, username=None, email=None, name=None, picture=None, grant_source=None
        ),
        SimpleNamespace(
            principal_id="uid-1",
            id="uid-1",
            username="alice",
            email="a@b",
            name="Alice",
            picture=None,
            grant_source="name",
        ),
    ]
    with _env(rows):
        result = AssistantShareService.list_shared_with(ASSISTANT_ID)
    assert {u["id"] for u in result} == {"uid-1", "uid-gone"}
    assert {k for u in result for k in u} == {"id", "username", "email", "name", "picture", "grant_source"}


# (7) personal integration guard
def _toolkit(setting_id, setting_type=SettingType.PROJECT):
    tk = MagicMock()
    tk.settings = MagicMock(id=setting_id, setting_type=setting_type)
    tk.tools = []
    return tk


def test_grant_rejects_assistant_with_personal_integration():
    stored = MagicMock(id="s1", setting_type=SettingType.USER)
    asst = _assistant(toolkits=[_toolkit("s1")])
    with _env([asst], settings=[stored]) as (session, get_settings):
        with pytest.raises(ExtendedHTTPException) as exc:
            AssistantShareService.grant(_owner(), asst, ["uid-1"])
    assert exc.value.code == 400
    assert "personal integration" in exc.value.message.lower()
    get_settings.assert_called_once_with(["s1"])
    # Only the lock ran: the descriptor's before_grant refuses before any grant statement.
    assert len(session.statements) == 1 and session.added == [] and session.commits == 0


def test_personal_integration_uses_stored_type_not_payload():
    # payload default is USER; stored row is PROJECT -> not personal
    tool = MagicMock(settings=MagicMock(id="s2", setting_type=SettingType.USER))
    tk = MagicMock(settings=None, tools=[tool])
    mcp = MagicMock(settings=MagicMock(id="s3"), mcp_connect_auth_token=None)
    asst = _assistant(toolkits=[tk], mcp_servers=[mcp])
    stored = [
        MagicMock(id="s2", setting_type=SettingType.PROJECT),
        MagicMock(id="s3", setting_type=SettingType.PROJECT),
    ]
    with patch(f"{MODULE}.Settings.get_by_ids", return_value=stored) as get_settings:
        assert AssistantShareService.assistant_uses_personal_integration(asst) is False
    assert sorted(get_settings.call_args.args[0]) == ["s2", "s3"]


def test_personal_integration_detected_on_mcp_server():
    mcp = MagicMock(settings=MagicMock(id="s3"), mcp_connect_auth_token=None)
    asst = _assistant(mcp_servers=[mcp])
    with patch(f"{MODULE}.Settings.get_by_ids", return_value=[MagicMock(id="s3", setting_type=SettingType.USER)]):
        assert AssistantShareService.assistant_uses_personal_integration(asst) is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"bedrock": MagicMock(bedrock_aws_settings_id="aws1")},
        {"bedrock_agentcore_runtime": MagicMock(aws_settings_id="aws1")},
    ],
)
def test_personal_integration_detected_on_bedrock_settings(kwargs):
    asst = _assistant(**kwargs)
    with patch(
        f"{MODULE}.Settings.get_by_ids", return_value=[MagicMock(id="aws1", setting_type=SettingType.USER)]
    ) as get_settings:
        assert AssistantShareService.assistant_uses_personal_integration(asst) is True
    assert get_settings.call_args.args[0] == ["aws1"]


def test_personal_integration_without_settings_skips_lookup():
    with patch(f"{MODULE}.Settings.get_by_ids") as get_settings:
        assert AssistantShareService.assistant_uses_personal_integration(_assistant()) is False
    get_settings.assert_not_called()


def test_has_named_grants():
    with _env(["perm-1"]):
        assert AssistantShareService.has_named_grants(ASSISTANT_ID) is True
    with _env([]):
        assert AssistantShareService.has_named_grants(ASSISTANT_ID) is False


@pytest.mark.parametrize("enabled,refused", [(True, True), (False, False)])
def test_personal_integration_guard_is_inactive_when_user_management_disabled(enabled, refused):
    with (
        patch(f"{MODULE}.config.ENABLE_USER_MANAGEMENT", enabled),
        patch.object(AssistantShareService, "has_named_grants", return_value=True),
        patch.object(AssistantShareService, "assistant_uses_personal_integration", return_value=True),
    ):
        if refused:
            with pytest.raises(ExtendedHTTPException):
                AssistantShareService.ensure_no_personal_integration_if_shared(ASSISTANT_ID, _assistant())
        else:
            AssistantShareService.ensure_no_personal_integration_if_shared(ASSISTANT_ID, _assistant())


def test_search_sharable_users_for_owner_limits_in_sql():
    users = [MagicMock(id=f"u{i}", username=f"u{i}", email="e", name="n", picture=None) for i in range(20)]
    mock_repo = MagicMock()
    mock_repo.aquery_active_users = AsyncMock(return_value=users)
    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    session_ctx.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("codemie.clients.postgres.get_async_session", return_value=session_ctx),
        patch("codemie.service.permission.named_grant_service.user_repository", mock_repo),
    ):
        result = asyncio.run(AssistantShareService.search_sharable_users_for_owner(_owner(), _assistant(), "al"))
    assert len(result) == 20
    assert mock_repo.aquery_active_users.await_args.kwargs["limit"] == 20
    # The assistant recipient rule (eligible internal users) is the SQL condition: externals are never offered.
    condition = mock_repo.aquery_active_users.await_args.kwargs["condition"]
    assert "user_type" in str(condition) and config.EXTERNAL_USER_TYPE in condition.compile().params.values()
