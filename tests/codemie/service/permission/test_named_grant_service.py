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

"""Statement-level tests of the generic named-grant service; a recording session stands in for PostgreSQL."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.permission import PrincipalType, ResourceType
from codemie.rest_api.security.user import User
from codemie.service.permission import named_grant_service as svc

RT = ResourceType.HOSTED_APPLICATION
RID = uuid4()
ACTOR = User(id="owner-1", username="owner", name="Owner Name")
RESOURCE = SimpleNamespace(id=RID, created_by=SimpleNamespace(id="owner-1"), security_state="clear")


def sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": False}))


class Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self.rows))


class RecordingSession:
    """Sync session; answers statements in order from `answers` and records everything."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.statements = []
        self.added = []
        self.flushes = 0

    def execute(self, statement):
        self.statements.append(statement)
        return Result(self.answers.pop(0) if self.answers else [])

    def add(self, row):
        self.added.append(row)

    def add_all(self, rows):
        self.added.extend(rows)

    def flush(self):
        self.flushes += 1


class AsyncRecordingSession(RecordingSession):
    async def execute(self, statement):
        return super().execute(statement)

    async def flush(self):
        self.flushes += 1


@pytest.fixture(autouse=True)
def events_on(monkeypatch):
    monkeypatch.setattr(config, "ACTIVITY_EVENTS_ENABLED", True)


def user_row(uid, eligible=True, grant_source=None, source_link_id=None, permission_level=None):
    if permission_level is None and grant_source is not None:
        permission_level = svc.Action.READ
    return SimpleNamespace(
        id=uid,
        eligible=eligible,
        grant_source=grant_source,
        source_link_id=source_link_id,
        permission_level=permission_level,
    )


def upserted(uid, inserted=True, level=None):
    return SimpleNamespace(
        id=str(uuid4()),
        principal_type=PrincipalType.USER,
        principal_id=uid,
        permission_level=level or svc.Action.READ,
        inserted=inserted,
    )


def deleted(uid, source=None, link=None):
    return SimpleNamespace(
        principal_type=PrincipalType.USER,
        principal_id=uid,
        permission_level=svc.Action.READ,
        grant_source=source,
        source_link_id=link,
    )


def grant_args(**over):
    args = {"resource_type": RT, "resource": RESOURCE, "project": "proj", "owner": ACTOR, "user_ids": ["u1", "u2"]}
    args.update(over)
    return args


def test_grant_deduplicates_ids_in_order():
    session = RecordingSession([user_row("u2"), user_row("u1")], [upserted("u2"), upserted("u1")])
    svc.grant(session, **grant_args(user_ids=["u2", "u1", "u2", "u1"]))
    insert_params = session.statements[1].compile(dialect=postgresql.dialect()).params
    principals = [v for k, v in insert_params.items() if k.startswith("principal_id")]
    assert principals == ["u2", "u1"]


def test_the_owner_never_grants_access_to_themselves():
    """CR-015: the owner's own id is dropped, so no self-grant row makes a private resource look shared."""
    session = RecordingSession([user_row("u1")], [upserted("u1")])
    svc.grant(session, **grant_args(user_ids=[ACTOR.id, "u1", ACTOR.id]))
    insert_params = session.statements[1].compile(dialect=postgresql.dialect()).params
    assert [v for k, v in insert_params.items() if k.startswith("principal_id")] == ["u1"]


def test_granting_only_to_the_owner_writes_nothing():
    session = RecordingSession()
    assert svc.grant(session, **grant_args(user_ids=[ACTOR.id])) == []
    assert session.statements == []


def test_validation_select_joins_permissions_and_filters_eligibility():
    session = RecordingSession([user_row("u1"), user_row("u2")], [upserted("u1"), upserted("u2")])
    svc.grant(session, **grant_args())
    text = sql(session.statements[0])
    assert "LEFT OUTER JOIN" in text and "permissions" in text
    assert "user_type" in text and "is_active" in text and "deleted_at" in text
    assert "resource_type" in text and "principal_type" in text


def test_unknown_user_is_404():
    session = RecordingSession([user_row("u1")])
    with pytest.raises(ExtendedHTTPException) as err:
        svc.grant(session, **grant_args())
    assert err.value.code == 404
    assert len(session.statements) == 1


@pytest.mark.parametrize("ineligible", ["external", "inactive", "deleted"])
def test_ineligible_user_is_400(ineligible):
    session = RecordingSession([user_row("u1"), user_row("u2", eligible=False)])
    with pytest.raises(ExtendedHTTPException) as err:
        svc.grant(session, **grant_args())
    assert err.value.code == 400
    assert "u2" in err.value.message
    assert len(session.statements) == 1


def test_counts_are_compared_after_dedup():
    session = RecordingSession([user_row("u1")], [upserted("u1")])
    svc.grant(session, **grant_args(user_ids=["u1", "u1"]))
    assert len(session.statements) == 2


def test_upsert_statement_shape():
    session = RecordingSession([user_row("u1"), user_row("u2")], [upserted("u1"), upserted("u2")])
    svc.grant(session, **grant_args())
    text = sql(session.statements[1])
    assert "ON CONFLICT ON CONSTRAINT unique_resource_principal DO UPDATE" in text
    assert "grant_source=%(param_1)s" in text.replace(" ", "") or "grant_source = " in text
    assert "WHERE permissions.grant_source = " in text
    assert "RETURNING" in text and "(xmax = 0) AS inserted" in text
    params = session.statements[1].compile(dialect=postgresql.dialect()).params
    assert "name" in params.values() and "link" in params.values()
    assert text.count("INSERT INTO") == 1


def test_upsert_raises_a_level_the_existing_grant_does_not_cover():
    """A by-name grant at another level is no longer silently kept: the conflict branch sets the requested level
    when the stored one does not cover it (or the row came from a link)."""
    statement = svc.upsert_statement(RT, RID, ACTOR, ["u1"], svc.Action.WRITE)
    text = sql(statement)
    assert "permission_level = excluded.permission_level" in text
    assert "permissions.permission_level NOT IN" in text
    params = statement.compile(dialect=postgresql.dialect()).params
    assert params["permission_level_m0"] == svc.Action.WRITE


def test_upsert_at_read_keeps_a_name_grant_that_already_covers_read():
    text = sql(svc.upsert_statement(RT, RID, ACTOR, ["u1"]))
    assert "permissions.grant_source = " in text and "NOT IN" in text
    params = svc.upsert_statement(RT, RID, ACTOR, ["u1"]).compile(dialect=postgresql.dialect()).params
    not_in = [value for key, value in params.items() if key.startswith("permission_level_") and isinstance(value, list)]
    assert not_in == [[svc.Action.READ, svc.Action.WRITE, svc.Action.DELETE]] or sorted(v.value for v in not_in[0]) == [
        "delete",
        "read",
        "write",
    ]


def test_raising_a_read_grant_to_write_records_read_to_write():
    session = RecordingSession(
        [user_row("u1", grant_source="name", permission_level=svc.Action.READ)],
        [upserted("u1", inserted=False, level=svc.Action.WRITE)],
    )
    assert svc.grant(session, **grant_args(user_ids=["u1"]), level=svc.Action.WRITE) == ["u1"]
    attrs = session.added[0].attributes
    assert attrs["before"] == "read" and attrs["after"] == "write"
    assert (
        svc.upsert_statement(RT, RID, ACTOR, ["u1"], svc.Action.WRITE)
        .compile(dialect=postgresql.dialect())
        .params["permission_level_m0"]
        == svc.Action.WRITE
    )


def test_validation_statement_reads_the_existing_level():
    assert "permissions.permission_level" in sql(svc.validation_statement(RT, RID, ["u1"]))


def test_upsert_rows_carry_the_contract_the_grant_readers_match():
    session = RecordingSession([user_row("u1")], [upserted("u1")])
    svc.grant(session, **grant_args(user_ids=["u1"]))
    params = session.statements[1].compile(dialect=postgresql.dialect()).params
    by_column = {k.rsplit("_m", 1)[0]: v for k, v in params.items() if k.endswith("_m0")}
    assert by_column["principal_type"] == PrincipalType.USER
    assert by_column["resource_type"] == ResourceType.HOSTED_APPLICATION
    assert by_column["permission_level"] == svc.Action.READ
    assert by_column["principal_id"] == "u1"
    assert by_column["resource_id"] == RID
    assert by_column["grant_source"] == "name"


def test_grant_rows_are_one_read_user_row_per_id():
    rows = svc.grant_rows(RT, RID, ACTOR, ["u1", "u2"])
    assert [r["principal_id"] for r in rows] == ["u1", "u2"]
    assert {(r["principal_type"], r["resource_type"], r["permission_level"]) for r in rows} == {
        (PrincipalType.USER, RT, svc.Action.READ)
    }
    assert rows[0]["created_by"].id == "owner-1"


@pytest.mark.parametrize("operation", [svc.grant, svc.revoke])
def test_a_non_owner_is_refused_before_any_statement(operation):
    session = RecordingSession()
    stranger = User(id="someone-else", username="s")
    with pytest.raises(ExtendedHTTPException) as err:
        operation(session, **grant_args(owner=stranger))
    assert err.value.code == 403
    assert session.statements == [] and session.added == []


def test_owner_check_comes_before_before_grant():
    suspended = SimpleNamespace(id=RID, created_by=SimpleNamespace(id="owner-1"), security_state="suspended")
    with pytest.raises(ExtendedHTTPException) as err:
        svc.grant(RecordingSession(), **grant_args(resource=suspended, owner=User(id="x", username="x")))
    assert err.value.code == 403


def test_grant_runs_before_grant_and_revoke_does_not():
    from codemie.core.hosted_applications.errors import HostedApplicationError

    suspended = SimpleNamespace(id=RID, created_by=SimpleNamespace(id="owner-1"), security_state="suspended")
    session = RecordingSession()
    with pytest.raises(HostedApplicationError) as err:
        svc.grant(session, **grant_args(resource=suspended))
    assert err.value.status == 409 and session.statements == []
    revoked = RecordingSession([deleted("u1")])
    assert svc.revoke(revoked, **grant_args(resource=suspended, user_ids=["u1"])) == ["u1"]


@pytest.mark.asyncio
async def test_async_twins_check_the_owner():
    for operation in (svc.agrant, svc.arevoke):
        session = AsyncRecordingSession()
        with pytest.raises(ExtendedHTTPException) as err:
            await operation(session, **grant_args(owner=User(id="x", username="x")))
        assert err.value.code == 403 and session.statements == []


def test_new_grants_emit_none_to_read_events_with_one_flush():
    session = RecordingSession([user_row("u1"), user_row("u2")], [upserted("u1"), upserted("u2")])
    svc.grant(session, **grant_args())
    assert session.flushes == 1
    assert len(session.added) == 2
    event = session.added[0]
    assert event.domain == svc.descriptor_for(RT).activity_domain
    assert event.event_type == svc.descriptor_for(RT).event_added
    assert event.entity_id == str(RID)
    attrs = event.attributes
    assert attrs["resource_type"] == "hosted_application"
    assert attrs["resource_id"] == str(RID)
    assert attrs["project"] == "proj"
    assert attrs["grant_source"] == "name"
    assert attrs["before"] is None and attrs["after"] == svc.Action.READ.value
    assert attrs["principal_id"] == "u1" and attrs["actor_id"] == "owner-1"
    assert "assistant_id" not in attrs


def test_link_upgrade_event_is_read_to_read_with_previous_source():
    link_id = uuid4()
    session = RecordingSession(
        [user_row("u1", grant_source="link", source_link_id=link_id)], [upserted("u1", inserted=False)]
    )
    svc.grant(session, **grant_args(user_ids=["u1"]))
    attrs = session.added[0].attributes
    assert attrs["before"] == svc.Action.READ.value and attrs["after"] == svc.Action.READ.value
    assert attrs["previous_grant_source"] == "link"
    assert attrs["previous_source_link_id"] == str(link_id)


def test_already_named_grant_produces_no_event():
    session = RecordingSession([user_row("u1", grant_source="name")], [])
    svc.grant(session, **grant_args(user_ids=["u1"]))
    assert session.added == [] and session.flushes == 0


def test_revoke_is_one_delete_returning_and_missing_is_noop():
    session = RecordingSession([deleted("u1")])
    svc.revoke(session, **grant_args(user_ids=["u1", "u2", "u1"]))
    assert len(session.statements) == 1
    text = sql(session.statements[0])
    assert text.startswith("DELETE FROM") and "RETURNING" in text and "principal_id IN" in text
    assert len(session.added) == 1
    attrs = session.added[0].attributes
    assert attrs["before"] == svc.Action.READ.value and attrs["after"] is None
    assert session.added[0].event_type == svc.descriptor_for(RT).event_removed
    assert session.flushes == 1

    empty = RecordingSession([])
    svc.revoke(empty, **grant_args(user_ids=["u9"]))
    assert empty.added == [] and empty.flushes == 0


def test_revoke_permission_deletes_by_id():
    permission = SimpleNamespace(id=uuid4(), resource_type=RT, resource_id=RID)
    session = RecordingSession([deleted("u1", source="name")])
    svc.revoke_permission(ACTOR, permission, session, "proj")
    text = sql(session.statements[0])
    assert text.startswith("DELETE FROM") and "RETURNING" in text and len(session.statements) == 1
    assert session.added[0].attributes["grant_source"] == "name"
    assert session.flushes == 1
    empty = RecordingSession([])
    svc.revoke_permission(ACTOR, permission, empty, "proj")
    assert empty.added == []


def test_remove_all_grants_system_actor_and_single_delete():
    session = RecordingSession([deleted("u1"), deleted("u2")])
    svc.remove_all_grants(None, RT, RID, "proj", session)
    assert len(session.statements) == 1
    assert "DELETE FROM" in sql(session.statements[0]) and "RETURNING" in sql(session.statements[0])
    assert len(session.added) == 2 and session.flushes == 1
    attrs = session.added[0].attributes
    assert attrs["actor_id"] is None and attrs["actor_username"] == "system"
    assert session.added[0].actor_id is None


@pytest.mark.asyncio
async def test_async_twins_share_statements():
    link_id = uuid4()
    session = AsyncRecordingSession(
        [user_row("u1", grant_source="link", source_link_id=link_id)], [upserted("u1", inserted=False)]
    )
    await svc.agrant(session, **grant_args(user_ids=["u1"]))
    assert session.flushes == 1
    assert session.added[0].attributes["previous_grant_source"] == "link"

    session = AsyncRecordingSession([deleted("u1")])
    await svc.arevoke(session, **grant_args(user_ids=["u1"]))
    assert session.flushes == 1

    session = AsyncRecordingSession([deleted("u1"), deleted("u2")])
    await svc.aremove_all_grants(None, RT, RID, "proj", session)
    assert len(session.added) == 2 and session.flushes == 1

    permission = SimpleNamespace(id=uuid4(), resource_type=RT, resource_id=RID)
    session = AsyncRecordingSession([deleted("u1")])
    await svc.arevoke_permission(ACTOR, permission, session, "proj")
    assert session.flushes == 1


def test_list_shared_with_sorted_with_orphans():
    rows = [
        SimpleNamespace(
            principal_id="u2", id="u2", username="zed", email="z@x", name="Zed", picture=None, grant_source="link"
        ),
        SimpleNamespace(
            principal_id="gone", id=None, username=None, email=None, name=None, picture=None, grant_source="link"
        ),
        SimpleNamespace(
            principal_id="u1", id="u1", username="amy", email="a@x", name=None, picture="p", grant_source="name"
        ),
    ]
    session = RecordingSession(rows)
    result = svc.list_shared_with(session, RT, RID)
    text = sql(session.statements[0])
    assert "LEFT OUTER JOIN" in text and "FROM" in text
    by_id = {u["id"]: u for u in result}
    assert by_id["gone"] == {
        "id": "gone",
        "username": "gone",
        "email": None,
        "name": None,
        "picture": None,
        "grant_source": "link",
    }
    assert set(by_id["u1"]) == {"id", "username", "email", "name", "picture", "grant_source"}
    assert by_id["u1"]["grant_source"] == "name" and by_id["u2"]["grant_source"] == "link"
    assert "permissions.grant_source" in text
    assert [u["id"] for u in result] == sorted(by_id, key=lambda i: by_id[i]["name"] or by_id[i]["username"])


@pytest.mark.asyncio
async def test_alist_shared_with_matches_sync():
    rows = [
        SimpleNamespace(
            principal_id="u1", id="u1", username="amy", email="a@x", name="Amy", picture=None, grant_source="name"
        )
    ]
    result = await svc.alist_shared_with(AsyncRecordingSession(rows), RT, RID)
    assert result[0]["username"] == "amy"


@pytest.mark.asyncio
async def test_search_sharable_users_limits_in_sql():
    users = [SimpleNamespace(id="u1", username="amy", email="a@x", name="Amy", picture=None)]
    session = AsyncRecordingSession(users)
    result = await svc.search_sharable_users(session, RT, "am")
    text = sql(session.statements[0])
    assert "LIMIT" in text
    params = session.statements[0].compile(dialect=postgresql.dialect()).params
    assert 20 in params.values() and svc.SEARCH_RESULT_LIMIT == 20
    assert "external" in params.values()
    assert result == [{"id": "u1", "username": "amy", "email": "a@x", "name": "Amy", "picture": None}]


@pytest.mark.asyncio
async def test_search_sharable_users_filters_by_the_types_recipient_rule(monkeypatch):
    """The recipient search offers whom the type may grant: the descriptor's recipient filter (S-18, finding 1)."""
    from dataclasses import replace

    from codemie.service.permission import named_grant_types

    custom = replace(named_grant_types.descriptor_for(RT), recipient=lambda users: users.is_auditor)
    monkeypatch.setitem(named_grant_types._cache, RT, custom)
    session = AsyncRecordingSession([])
    await svc.search_sharable_users(session, RT, "am")
    where = sql(session.statements[0]).split("WHERE", 1)[1]
    assert "users.is_auditor" in where and "users.user_type" not in where


# ---- link grants ----------------------------------------------------------------------------------------------------


def link_row(uid):
    return SimpleNamespace(
        id=str(uuid4()), principal_type=PrincipalType.USER, principal_id=uid, permission_level=svc.Action.READ
    )


OPENER = User(id="opener-1", username="opener", name="Opener")
LINK_OWNER = CreatedByUser(id="owner-1", username="owner", name="Owner Name")


def test_link_grant_statement_is_one_insert_do_nothing_with_link_source():
    link_id = uuid4()
    statement = svc.link_grant_statement(RT, RID, OPENER, link_id, LINK_OWNER)
    text = sql(statement)
    assert "ON CONFLICT ON CONSTRAINT unique_resource_principal DO NOTHING" in text
    assert "DO UPDATE" not in text and "RETURNING" in text
    params = statement.compile(dialect=postgresql.dialect()).params
    assert params["grant_source_m0"] == "link" and params["source_link_id_m0"] == link_id
    assert params["principal_id_m0"] == "opener-1"


def test_a_link_grant_row_is_created_by_the_link_owner_not_the_opener():
    """AR-6: the person who gained access never "owns" the row that grants it."""
    statement = svc.link_grant_statement(RT, RID, OPENER, uuid4(), LINK_OWNER)
    params = statement.compile(dialect=postgresql.dialect()).params
    assert params["created_by_m0"] == LINK_OWNER and params["principal_id_m0"] == OPENER.id


def test_the_opener_stays_the_audit_actor_of_a_link_grant():
    session = RecordingSession([link_row("opener-1")])
    svc.link_grant(session, RT, RESOURCE, OPENER, uuid4(), "proj", LINK_OWNER)
    event = session.added[0]
    assert event.actor_id == "opener-1" and event.attributes["principal_id"] == "opener-1"


def test_link_grant_writes_one_added_event_when_a_row_is_inserted():
    link_id = uuid4()
    session = RecordingSession([link_row("owner-1")])
    assert svc.link_grant(session, RT, RESOURCE, ACTOR, link_id, "proj", LINK_OWNER) is True
    assert len(session.statements) == 1 and session.flushes == 1
    event = session.added[0]
    assert event.event_type == svc.descriptor_for(RT).event_added
    attrs = event.attributes
    assert attrs["grant_source"] == "link" and attrs["source_link_id"] == str(link_id)
    assert attrs["before"] is None and attrs["after"] == svc.Action.READ.value
    assert attrs["principal_id"] == "owner-1" and attrs["project"] == "proj"


def test_link_grant_over_an_existing_row_writes_no_event():
    session = RecordingSession([])
    assert svc.link_grant(session, RT, RESOURCE, ACTOR, uuid4(), "proj", LINK_OWNER) is False
    assert session.added == [] and session.flushes == 0


def test_link_grant_writes_nothing_with_activity_events_off(monkeypatch):
    monkeypatch.setattr(config, "ACTIVITY_EVENTS_ENABLED", False)
    session = RecordingSession([link_row("owner-1")])
    assert svc.link_grant(session, RT, RESOURCE, ACTOR, uuid4(), "proj", LINK_OWNER) is True
    assert session.added == [] and session.flushes == 0


def test_remove_link_statement_filters_link_source_and_link_id():
    link_id = uuid4()
    statement = svc.remove_link_statement(RT, RID, link_id)
    text = sql(statement)
    assert text.startswith("DELETE FROM") and "RETURNING" in text
    assert "permissions.grant_source = %(grant_source_1)s" in text
    assert "permissions.source_link_id = %(source_link_id_1)s" in text
    params = statement.compile(dialect=postgresql.dialect()).params
    assert params["grant_source_1"] == "link" and params["source_link_id_1"] == link_id


def test_remove_link_grants_one_removed_event_per_row():
    link_id = uuid4()
    session = RecordingSession([deleted("u1", "link", link_id), deleted("u2", "link", link_id)])
    svc.remove_link_grants(ACTOR, RT, RID, link_id, "proj", session)
    assert len(session.statements) == 1 and session.flushes == 1 and len(session.added) == 2
    attrs = session.added[0].attributes
    assert session.added[0].event_type == svc.descriptor_for(RT).event_removed
    assert attrs["grant_source"] == "link" and attrs["previous_source_link_id"] == str(link_id)
    assert attrs["before"] == svc.Action.READ.value and attrs["after"] is None


def test_remove_link_grants_without_rows_is_a_noop():
    session = RecordingSession([])
    svc.remove_link_grants(ACTOR, RT, RID, uuid4(), "proj", session)
    assert session.added == [] and session.flushes == 0


@pytest.mark.asyncio
async def test_async_link_twins_share_statements():
    link_id = uuid4()
    session = AsyncRecordingSession([link_row("owner-1")])
    assert await svc.alink_grant(session, RT, RESOURCE, ACTOR, link_id, "proj", LINK_OWNER) is True
    assert session.flushes == 1 and session.added[0].attributes["source_link_id"] == str(link_id)

    session = AsyncRecordingSession([deleted("u1", "link", link_id)])
    await svc.aremove_link_grants(ACTOR, RT, RID, link_id, "proj", session)
    assert session.flushes == 1 and len(session.added) == 1


# ---- one module for every named-grant type (assistants moved onto it) ----------------------------------------------

ASSISTANT_ID = "00000000-0000-0000-0000-0000000000a1"
ASSISTANT = SimpleNamespace(
    id=ASSISTANT_ID,
    project="proj",
    toolkits=[],
    mcp_servers=[],
    bedrock=None,
    bedrock_agentcore_runtime=None,
    is_owned_by=lambda user: user.id == "owner-1",
)


@pytest.fixture
def no_personal_integration(monkeypatch):
    from codemie.service.assistant.assistant_share_service import AssistantShareService

    monkeypatch.setattr(AssistantShareService, "assistant_uses_personal_integration", staticmethod(lambda _a: False))


def test_assistant_grant_events_keep_assistant_id_beside_resource_type(no_personal_integration):
    session = RecordingSession([user_row("u1")], [upserted("u1")])
    svc.grant(session, **grant_args(resource_type=ResourceType.ASSISTANT, resource=ASSISTANT, user_ids=["u1"]))
    event = session.added[0]
    assert event.event_type == "assistant.grant.added"
    attrs = event.attributes
    assert attrs["assistant_id"] == ASSISTANT_ID
    assert attrs["resource_type"] == "assistant" and attrs["resource_id"] == ASSISTANT_ID
    assert attrs["grant_source"] == "name" and attrs["before"] is None and attrs["after"] == "read"


def test_assistant_grant_rows_carry_grant_source_name(no_personal_integration):
    session = RecordingSession([user_row("u1")], [upserted("u1")])
    svc.grant(session, **grant_args(resource_type=ResourceType.ASSISTANT, resource=ASSISTANT, user_ids=["u1"]))
    params = session.statements[1].compile(dialect=postgresql.dialect()).params
    assert params["grant_source_m0"] == "name" and params["source_link_id_m0"] is None


def test_assistant_non_owner_gets_the_assistant_message():
    with pytest.raises(ExtendedHTTPException) as err:
        svc.grant(
            RecordingSession(),
            **grant_args(resource_type=ResourceType.ASSISTANT, resource=ASSISTANT, owner=User(id="x", username="x")),
        )
    assert err.value.code == 403 and err.value.message == "Only the assistant owner can share or revoke access"


def test_assistant_removal_events_keep_assistant_id():
    session = RecordingSession([deleted("u1")])
    svc.remove_all_grants(ACTOR, ResourceType.ASSISTANT, ASSISTANT_ID, "proj", session)
    attrs = session.added[0].attributes
    assert session.added[0].event_type == "assistant.grant.removed"
    assert attrs["assistant_id"] == ASSISTANT_ID and attrs["before"] == "read" and attrs["after"] is None


def test_hosted_events_get_no_assistant_id():
    session = RecordingSession([deleted("u1")])
    svc.remove_all_grants(ACTOR, RT, RID, "proj", session)
    assert "assistant_id" not in session.added[0].attributes


# ---- preload: one statement over live_grants, fail closed elsewhere ------------------------------------------------


def test_preload_statement_reads_live_grants_only(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", True)
    text = sql(svc.preload_statement(ResourceType.ASSISTANT, "user-1"))
    assert "JOIN users" in text and "is_active" in text and "deleted_at" in text and "user_type" in text
    assert "permissions.resource_type" in text and "permissions.principal_type" in text


def test_preload_statement_narrows_to_valid_uuid_ids(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", True)
    statement = svc.preload_statement(ResourceType.ASSISTANT, "user-1", [ASSISTANT_ID, "not-a-uuid"])
    assert "permissions.resource_id IN" in sql(statement)
    values = statement.compile(dialect=postgresql.dialect()).params.values()
    assert [str(v) for value in values if isinstance(value, list) for v in value] == [ASSISTANT_ID]


def test_preload_keeps_highest_level_per_resource_whatever_the_order():
    a, b = uuid4(), uuid4()
    rows = [(a, svc.Action.READ), (a, svc.Action.DELETE), (a, svc.Action.WRITE), (b, svc.Action.READ)]
    grants = svc.preload(RecordingSession(rows), ResourceType.ASSISTANT, ACTOR)
    assert grants.class_name == "Assistant"
    assert grants.levels == {str(a): svc.Action.DELETE, str(b): svc.Action.READ}


@pytest.mark.asyncio
async def test_apreload_matches_preload_and_names_the_hosted_class():
    a = uuid4()
    grants = await svc.apreload(AsyncRecordingSession([(a, svc.Action.READ)]), RT, ACTOR)
    assert grants.class_name == "HostedApplications" and grants.levels == {str(a): svc.Action.READ}


def test_preload_without_ids_to_check_runs_no_statement():
    session = RecordingSession()
    assert svc.preload(session, ResourceType.ASSISTANT, ACTOR, []).levels == {}
    assert session.statements == []


def test_granted_ids_statement_compares_through_the_type_key(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", True)
    from sqlalchemy import literal

    assistant_sql = sql(svc.granted_ids_statement(ResourceType.ASSISTANT, literal("user-1")))
    assert "CAST(" in assistant_sql and "AS TEXT)" in assistant_sql
    hosted_sql = sql(svc.granted_ids_statement(RT, literal("user-1")))
    assert "CAST(" not in hosted_sql


def test_a_resource_with_a_non_uuid_id_has_no_grants_to_list_or_remove():
    """Assistant ids are text; one that is not a UUID can hold no permission row (resource_id is a uuid)."""
    session = RecordingSession()
    svc.remove_all_grants(ACTOR, ResourceType.ASSISTANT, "assistant-1", "proj", session)
    assert svc.list_shared_with(session, ResourceType.ASSISTANT, "assistant-1") == []
    assert session.statements == [] and session.added == []
