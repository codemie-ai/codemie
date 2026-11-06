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

import asyncio
from contextlib import contextmanager

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.dialects import postgresql

from codemie.core.ability import Action
from codemie.core.exceptions import ExtendedHTTPException
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
    return a


def _db_user(uid, user_type="internal", is_active=True, deleted_at=None):
    u = MagicMock()
    u.id = uid
    u.user_type = user_type
    u.is_active = is_active
    u.deleted_at = deleted_at
    return u


@contextmanager
def _env(users=None, execute_results=None, insert_strict=None, settings=None):
    """Patch DB access. execute_results: list of results, one per session.execute call."""
    users = users if users is not None else {}
    session = MagicMock()
    results = []
    for r in execute_results or []:
        res = MagicMock()
        res.scalar_one_or_none.return_value = r
        res.first.return_value = r
        res.all.return_value = r if isinstance(r, list) else []
        results.append(res)
    session.execute.side_effect = results
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    with (
        patch(f"{MODULE}.UserRepository") as repo,
        patch(f"{MODULE}.Session", return_value=ctx),
        patch(f"{MODULE}.config") as cfg,
        patch(f"{MODULE}.activity_event_repository.insert_strict", side_effect=insert_strict) as strict,
        patch(f"{MODULE}.Settings.get_by_ids", return_value=settings or []) as get_settings,
        patch.object(AssistantShareService, "list_shared_with", return_value=[]),
    ):
        cfg.EXTERNAL_USER_TYPE = "external"
        repo.return_value.get_by_id.side_effect = lambda _s, uid: users.get(uid)
        yield session, strict, get_settings


def _users(*ids):
    return {uid: _db_user(uid) for uid in ids}


# (9) owner check on all four public entry points
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
    with _env() as (session, strict, _):
        with pytest.raises(ExtendedHTTPException) as exc:
            call(_owner("other"), _assistant("owner-1"))
    assert exc.value.code == 403
    session.execute.assert_not_called()
    strict.assert_not_called()


def test_grant_external_user_raises_400():
    users = {"uid-ext": _db_user("uid-ext", user_type="external")}
    with _env(users=users) as (session, strict, _):
        with pytest.raises(ExtendedHTTPException) as exc:
            AssistantShareService.grant(_owner(), _assistant(), ["uid-ext"])
    assert exc.value.code == 400
    session.execute.assert_not_called()


# (1) duplicate ids -> one insert, one event
def test_grant_duplicate_user_ids_inserts_once_and_writes_one_event():
    with _env(users=_users("uid-1"), execute_results=["perm-1"]) as (session, strict, _):
        AssistantShareService.grant(_owner(), _assistant(), ["uid-1", "uid-1"])
    assert session.execute.call_count == 1
    strict.assert_called_once()
    session.commit.assert_called_once()


def test_grant_insert_is_conflict_safe_on_unique_constraint():
    with _env(users=_users("uid-1"), execute_results=["perm-1"]) as (session, _, _):
        AssistantShareService.grant(_owner(), _assistant(), ["uid-1"])
    stmt = session.execute.call_args.args[0]
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT ON CONSTRAINT unique_resource_principal DO NOTHING" in sql
    assert "RETURNING" in sql


# (2) one invalid id -> 404 and nothing written
def test_grant_with_one_unknown_id_writes_nothing():
    with _env(users=_users("uid-1")) as (session, strict, _):
        with pytest.raises(ExtendedHTTPException) as exc:
            AssistantShareService.grant(_owner(), _assistant(), ["uid-1", "uid-missing"])
    assert exc.value.code == 404
    session.execute.assert_not_called()
    session.commit.assert_not_called()
    strict.assert_not_called()


# (3)+(4) repeat grant / pre-existing row: conflict path returns no row -> no event, no error
def test_grant_existing_row_is_skipped_without_event():
    with _env(users=_users("uid-1", "uid-2"), execute_results=[None, "perm-2"]) as (session, strict, _):
        AssistantShareService.grant(_owner(), _assistant(), ["uid-1", "uid-2"])
    assert session.execute.call_count == 2
    strict.assert_called_once()
    assert strict.call_args.args[0].attributes["principal_id"] == "uid-2"
    session.commit.assert_called_once()


def test_grant_repeat_writes_no_event():
    with _env(users=_users("uid-1"), execute_results=[None]) as (session, strict, _):
        AssistantShareService.grant(_owner(), _assistant(), ["uid-1"])
    strict.assert_not_called()


# (5) strict audit failure -> no commit, error surfaces
def test_grant_audit_failure_rolls_back():
    with _env(users=_users("uid-1"), execute_results=["perm-1"], insert_strict=RuntimeError("db down")) as (
        session,
        _,
        _,
    ):
        with pytest.raises(RuntimeError):
            AssistantShareService.grant(_owner(), _assistant(), ["uid-1"])
    session.commit.assert_not_called()


def test_grant_event_payload():
    with _env(users=_users("uid-1"), execute_results=["perm-1"]) as (_, strict, _):
        AssistantShareService.grant(_owner(), _assistant(), ["uid-1"])
    event, session_arg = strict.call_args.args
    assert session_arg is not None
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
    assert attrs["project"] == "proj"
    assert attrs["before"] is None
    assert attrs["after"] == "read"
    assert attrs["occurred_at"].endswith("+00:00")


# (6) revoke: event per deleted row only
def test_revoke_writes_event_only_for_deleted_rows():
    deleted = MagicMock(principal_type="USER", principal_id="uid-1", permission_level=Action.READ)
    with _env(execute_results=[deleted, None]) as (session, strict, _):
        AssistantShareService.revoke(_owner(), _assistant(), ["uid-1", "uid-nobody"])
    assert session.execute.call_count == 2
    strict.assert_called_once()
    event = strict.call_args.args[0]
    assert event.event_type == AssistantEvent.GRANT_REMOVED
    assert event.attributes["before"] == "read"
    assert event.attributes["after"] is None
    session.commit.assert_called_once()


def test_revoke_no_existing_grant_writes_no_event():
    with _env(execute_results=[None]) as (_, strict, _):
        AssistantShareService.revoke(_owner(), _assistant(), ["uid-nobody"])
    strict.assert_not_called()


def test_remove_all_grants_audits_each_deleted_row():
    rows = [
        MagicMock(principal_type="USER", principal_id="uid-1", permission_level=Action.READ),
        MagicMock(principal_type="USER", principal_id="uid-2", permission_level=Action.READ),
    ]
    with _env(execute_results=[rows]) as (session, strict, _):
        AssistantShareService.remove_all_grants(_owner(), ASSISTANT_ID)
    assert strict.call_count == 2
    session.commit.assert_called_once()


def test_revoke_permission_deletes_given_row_without_owner_check():
    perm = MagicMock(id="perm-1", resource_id=ASSISTANT_ID)
    deleted = MagicMock(principal_type="USER", principal_id="uid-1", permission_level=Action.READ)
    with (
        _env(execute_results=[deleted]) as (session, strict, _),
        patch(f"{MODULE}.Assistant.find_by_id", return_value=_assistant("someone-else")),
    ):
        AssistantShareService.revoke_permission(_owner("admin"), perm)
    strict.assert_called_once()
    assert strict.call_args.args[0].attributes["project"] == "proj"
    session.commit.assert_called_once()


# (7) personal integration guard
def _toolkit(setting_id, setting_type=SettingType.PROJECT):
    tk = MagicMock()
    tk.settings = MagicMock(id=setting_id, setting_type=setting_type)
    tk.tools = []
    return tk


def test_grant_rejects_assistant_with_personal_integration():
    stored = MagicMock(id="s1", setting_type=SettingType.USER)
    asst = _assistant(toolkits=[_toolkit("s1")])
    with _env(users=_users("uid-1"), settings=[stored]) as (session, strict, get_settings):
        with pytest.raises(ExtendedHTTPException) as exc:
            AssistantShareService.grant(_owner(), asst, ["uid-1"])
    assert exc.value.code == 400
    assert "personal integration" in exc.value.message.lower()
    get_settings.assert_called_once_with(["s1"])
    session.execute.assert_not_called()
    strict.assert_not_called()


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
    with _env(execute_results=["perm-1"]):
        assert AssistantShareService.has_named_grants(ASSISTANT_ID) is True
    with _env(execute_results=[None]):
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


# (8) search cap
def test_search_sharable_users_for_owner_caps_at_20():
    users = [MagicMock(id=f"u{i}", username=f"u{i}", email="e", name="n", picture=None) for i in range(30)]
    mock_repo = MagicMock()
    mock_repo.aquery_active_users = AsyncMock(return_value=users)
    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    session_ctx.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("codemie.clients.postgres.get_async_session", return_value=session_ctx),
        patch("codemie.repository.user_repository.user_repository", mock_repo),
    ):
        result = asyncio.run(AssistantShareService.search_sharable_users_for_owner(_owner(), _assistant(), "al"))
    assert len(result) == 20


def test_remove_all_grants_with_caller_session_does_not_commit():
    rows = [MagicMock(principal_type="USER", principal_id="uid-1", permission_level=Action.READ)]
    with _env(execute_results=[rows]) as (session, strict, _):
        AssistantShareService.remove_all_grants(_owner(), ASSISTANT_ID, "proj", session)
    strict.assert_called_once()
    session.commit.assert_not_called()


def test_list_shared_with_includes_orphan_grants_so_owner_can_revoke():
    perms = [MagicMock(principal_id="uid-gone"), MagicMock(principal_id="uid-1")]
    live = MagicMock(id="uid-1", username="alice", email="a@b.com", picture=None)
    live.name = "Alice"
    with (
        patch(f"{MODULE}.Permission.get_all_by_fields", return_value=perms),
        patch(f"{MODULE}.UserRepository") as repo,
        patch(f"{MODULE}.Session"),
    ):
        repo.return_value.get_by_id.side_effect = lambda _s, uid: live if uid == "uid-1" else None
        result = AssistantShareService.list_shared_with(ASSISTANT_ID)
    assert {u["id"] for u in result} == {"uid-1", "uid-gone"}
