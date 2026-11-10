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

"""Assistant grants follow the shared live grant rule: only eligible internal users' grants count (SQLite)."""

from datetime import datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from codemie.configs import config
from codemie.core.ability import Action, Ability
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.permission import Permission, PrincipalType, ResourceType
from codemie.rest_api.models.user_management import UserDB
from codemie.rest_api.security.user import User
from codemie.service.permission import named_grant_service

ASSISTANT_ID = "00000000-0000-0000-0000-0000000000a1"


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    return "JSON"


PERMISSIONS = sa.Table(
    Permission.__table__.name,
    sa.MetaData(),
    *[sa.Column(c.name, c.type, primary_key=c.primary_key, nullable=c.nullable) for c in Permission.__table__.columns],
)


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", True)
    engine = sa.create_engine("sqlite://")
    UserDB.__table__.create(engine)
    PERMISSIONS.create(engine)
    with engine.begin() as connection:
        for uid, active, deleted, user_type in (
            ("live", True, None, "regular"),
            ("deactivated", False, None, "regular"),
            ("deleted", True, datetime(2026, 1, 1), "regular"),
            ("outsider", True, None, config.EXTERNAL_USER_TYPE),
        ):
            connection.execute(
                UserDB.__table__.insert().values(
                    id=uid,
                    username=uid,
                    email=f"{uid}@example.com",
                    user_type=user_type,
                    is_active=active,
                    deleted_at=deleted,
                    is_admin=False,
                    is_maintainer=False,
                    is_auditor=False,
                    auth_source="local",
                    email_verified=False,
                )
            )
            connection.execute(
                PERMISSIONS.insert().values(
                    id=str(uuid4()),
                    date=datetime.now(),
                    update_date=datetime.now(),
                    resource_type=ResourceType.ASSISTANT,
                    resource_id=UUID(ASSISTANT_ID),
                    principal_type=PrincipalType.USER,
                    principal_id=uid,
                    permission_level=Action.READ,
                    grant_source=None,
                )
            )
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.mark.parametrize(
    "uid, counts", [("live", True), ("deactivated", False), ("deleted", False), ("outsider", False)]
)
def test_only_an_eligible_internal_grantee_keeps_an_assistant_grant(session, uid, counts):
    grants = named_grant_service.preload(session, ResourceType.ASSISTANT, User(id=uid, username=uid), [ASSISTANT_ID])
    assert (ASSISTANT_ID in grants.levels) is counts


def test_legacy_rows_without_grant_source_still_count(session):
    grants = named_grant_service.preload(session, ResourceType.ASSISTANT, User(id="live", username="live"))
    assert grants.levels == {ASSISTANT_ID: Action.READ}


def test_user_management_off_counts_no_assistant_grant(session, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", False)
    assert named_grant_service.preload(session, ResourceType.ASSISTANT, User(id="live", username="live")).levels == {}


@pytest.mark.parametrize("uid, count", [("live", 1), ("deactivated", 0)])
def test_the_list_filter_select_follows_the_same_rule(session, uid, count):
    statement = named_grant_service.granted_ids_statement(ResourceType.ASSISTANT, sa.literal(uid))
    assert len(session.execute(statement).all()) == count


def test_a_deactivated_grantee_loses_read_through_ability(session):
    from codemie.rest_api.models.assistant import Assistant

    assistant = Assistant(
        id=ASSISTANT_ID,
        name="a",
        description="d",
        system_prompt="s",
        project="proj",
        shared=False,
        is_global=False,
        creator="owner",
    )
    for uid, readable in (("live", True), ("deactivated", False)):
        user = User(id=uid, username=uid, project_names=[])
        user.is_admin = False
        grants = named_grant_service.preload(session, ResourceType.ASSISTANT, user, [ASSISTANT_ID])
        assert Ability(user, grants=grants).can(Action.READ, assistant) is readable


# ---- lazy per-class grant loading inside Ability (finding 2) --------------------------------------------------------

HOSTED_ID = "00000000-0000-0000-0000-0000000000b2"


def _assistant(assistant_id=ASSISTANT_ID):
    from codemie.rest_api.models.assistant import Assistant

    return Assistant(
        id=assistant_id,
        name="a",
        description="d",
        system_prompt="s",
        project="proj",
        shared=False,
        is_global=False,
        creator="owner",
    )


def _hosted_app(app_id=HOSTED_ID):
    from codemie.rest_api.models.hosted_application import HostedApplications

    return HostedApplications(
        id=UUID(app_id),
        project="proj",
        shared=False,
        is_global=False,
        security_state="clear",
        created_by=CreatedByUser(id="owner", username="owner", name="Owner"),
    )


def _grantee(uid="live"):
    user = User(id=uid, username=uid, project_names=[])
    user.is_admin = False
    return user


@pytest.fixture
def platform_engine(session, monkeypatch):
    """The sync engine ``Ability`` loads grants through is the SQLite one, with every statement counted."""
    engine = session.get_bind()
    statements = []
    sa.event.listen(engine, "before_cursor_execute", lambda *args: statements.append(args[2]))
    monkeypatch.setattr("codemie.clients.postgres.PostgresClient.get_engine", lambda *a, **k: engine)
    with engine.begin() as connection:
        connection.execute(
            PERMISSIONS.insert().values(
                id=str(uuid4()),
                date=datetime.now(),
                update_date=datetime.now(),
                resource_type=ResourceType.HOSTED_APPLICATION,
                resource_id=UUID(HOSTED_ID),
                principal_type=PrincipalType.USER,
                principal_id="live",
                permission_level=Action.READ,
                grant_source="name",
            )
        )
    statements.clear()
    return statements


def test_a_grantee_reads_an_assistant_through_plain_ability(platform_engine):
    """No preload at the call site: the named grant still counts (the assistant read check of any new screen)."""
    assert Ability(_grantee()).can(Action.READ, _assistant()) is True
    assert Ability(_grantee("deactivated")).can(Action.READ, _assistant()) is False


def test_grants_load_once_per_class_for_one_ability(platform_engine):
    ability = Ability(_grantee())
    others = [_assistant(str(uuid4())) for _ in range(5)]
    for assistant in [_assistant(), *others]:
        ability.can(Action.READ, assistant)
    ability.list(_assistant())
    assert len(platform_engine) == 1


def test_a_mixed_list_of_assistants_and_hosted_apps_takes_one_query_per_class(platform_engine):
    ability = Ability(_grantee())
    items = [_assistant(), _hosted_app(), _assistant(str(uuid4())), _hosted_app(str(uuid4()))]
    readable = [ability.can(Action.READ, item) for item in items]
    assert readable == [True, True, False, False]
    assert len(platform_engine) == 2


def test_explicit_grants_count_as_loaded_for_their_class_only(platform_engine):
    from codemie.core.ability import PreloadedGrants

    ability = Ability(_grantee(), grants=PreloadedGrants("HostedApplications", {}))
    assert ability.can(Action.READ, _hosted_app()) is False  # the passed (empty) grants are the hosted answer
    assert platform_engine == []
    assert ability.can(Action.READ, _assistant()) is True  # assistants still load lazily
    assert len(platform_engine) == 1


def test_owners_and_managers_never_load_grants(platform_engine):
    owner = User(id="owner", username="owner", project_names=[])
    assert Ability(owner).can(Action.READ, _assistant()) is True
    assert platform_engine == []
