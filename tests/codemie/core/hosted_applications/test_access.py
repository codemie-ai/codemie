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

from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from codemie.core.ability import Ability, Action, Owned, PreloadedGrants, Role
from codemie.core.hosted_applications import access
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.hosted_application import HostedApplications
from codemie.rest_api.models.permission import Permission, PrincipalType, ResourceType
from codemie.rest_api.models.user_management import UserDB, UserProject
from codemie.rest_api.security.user import User

PROJECT = "proj-a"
OTHER = "proj-b"
ALL_ACTIONS = [Action.READ, Action.WRITE, Action.DELETE]

# persona -> (user fields, project memberships {name: is_project_admin})
PERSONAS = {
    "creator": ({"id": "u-creator"}, {PROJECT: False}),
    "project_admin": ({"id": "u-padmin"}, {PROJECT: True}),
    "platform_admin": ({"id": "u-admin", "is_admin": True}, {}),
    "maintainer": ({"id": "u-maint", "is_maintainer": True}, {}),
    "member": ({"id": "u-member"}, {PROJECT: False}),
    "outsider": ({"id": "u-outsider"}, {OTHER: False}),
    "service_account": ({"id": "u-svc", "user_type": "service_account"}, {OTHER: False}),
    "external": ({"id": "u-ext", "user_type": "external"}, {PROJECT: False}),
    "external_creator": ({"id": "u-extc", "user_type": "external"}, {PROJECT: True}),
}
CREATORS = {"creator": "u-creator", "external_creator": "u-extc"}


def make_user(persona):
    fields, memberships = PERSONAS[persona]
    user = User(
        username=persona,
        project_names=list(memberships),
        admin_project_names=[name for name, admin in memberships.items() if admin],
        **fields,
    )
    # ENV=local forces is_admin on at construction; the persona decides here (maintainers stay admins).
    user.is_admin = bool(fields.get("is_admin") or fields.get("is_maintainer"))
    return user


def make_app(creator_id="u-creator", shared=False, is_global=False, security_state="clear"):
    return HostedApplications(
        id=uuid4(),
        project=PROJECT,
        created_by=CreatedByUser(id=creator_id, username=creator_id, name=creator_id),
        shared=shared,
        is_global=is_global,
        security_state=security_state,
    )


# (id, app kwargs) scenarios; "member" visibility depends on shared / is_global
SCENARIOS = {
    "private": {},
    "shared": {"shared": True},
    "global": {"is_global": True},
    "shared_suspended": {"shared": True, "security_state": "suspended"},
    "global_suspended": {"is_global": True, "security_state": "suspended"},
    "private_suspended": {"security_state": "suspended"},
}


def expected(persona, scenario):
    """Independent statement of the access rules, used for Ability.list and SQL."""
    if persona.startswith("external"):
        return []
    app = SCENARIOS[scenario]
    suspended = app.get("security_state") == "suspended"
    creator = persona == "creator"
    padmin = persona == "project_admin"
    admin = persona in ("platform_admin", "maintainer")
    manager = creator or padmin or admin
    member = persona in ("creator", "project_admin", "member")
    viewer = ((app.get("shared", False) and member) or app.get("is_global", False)) and not suspended
    if manager:
        return list(ALL_ACTIONS)
    return [Action.READ] if viewer else []


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("persona", PERSONAS)
def test_ability_list_matches_access_rules(persona, scenario):
    user = make_user(persona)
    app = make_app(creator_id=CREATORS.get(persona, "u-creator"), **SCENARIOS[scenario])
    assert Ability(user).list(app) == expected(persona, scenario)


def test_model_implements_owned_by_delegation():
    assert issubclass(HostedApplications, Owned)


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("persona", PERSONAS)
def test_predicates_agree_with_ability_roles(persona, scenario):
    user = make_user(persona)
    kwargs = SCENARIOS[scenario]
    creator_id = CREATORS.get(persona, "u-creator")
    app = make_app(creator_id=creator_id, **kwargs)
    fields = {
        "project": app.project,
        "created_by_id": app.created_by.id,
        "shared": app.shared,
        "is_global": app.is_global,
    }
    external = persona.startswith("external")
    assert access.is_owned_by(fields["created_by_id"], user) is (not external and persona == "creator")
    assert access.is_managed_by(fields["project"], user) is (not external and persona == "project_admin")
    member = persona in ("creator", "project_admin", "member")
    suspended = app.security_state == "suspended"
    viewer = not external and not suspended and (app.is_global or (app.shared and member))
    assert access.is_shared_with(app.project, app.shared, app.is_global, app.security_state, user) is viewer
    assert app.is_shared_with(user) is viewer
    assert access.is_suspended(app) is (kwargs.get("security_state") == "suspended")
    # model delegates
    assert app.is_owned_by(user) is access.is_owned_by(app.created_by.id, user)
    assert app.is_managed_by(user) is access.is_managed_by(app.project, user)


@pytest.mark.parametrize("action", ["activate", "publish", "share_on", "move"])
def test_suspend_refuses_raises_for_suspended_app(action):
    with pytest.raises(HostedApplicationError) as raised:
        access.suspend_refuses(make_app(security_state="suspended"), action)
    assert raised.value.code == "APP_SUSPENDED"
    assert raised.value.status == 409


@pytest.mark.parametrize("action", ["activate", "publish", "share_on", "move"])
def test_suspend_refuses_passes_for_clear_app(action):
    assert access.suspend_refuses(make_app(), action) is None


# ---- SQL builders, evaluated on in-memory SQLite against seeded users / user_projects ----

APPS = sa.Table(
    "hosted_applications",
    sa.MetaData(),
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("project", sa.Text),
    sa.Column("created_by", sa.JSON),
    sa.Column("shared", sa.Boolean),
    sa.Column("is_global", sa.Boolean),
    sa.Column("security_state", sa.Text),
)


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    return "JSON"


PERMISSIONS = sa.Table(
    Permission.__table__.name, sa.MetaData(), *[column._copy() for column in Permission.__table__.columns]
)


@pytest.fixture
def engine():
    engine = sa.create_engine("sqlite://")
    UserDB.__table__.create(engine)
    UserProject.__table__.create(engine)
    PERMISSIONS.create(engine)
    APPS.create(engine)
    with engine.begin() as connection:
        for persona, (fields, memberships) in PERSONAS.items():
            connection.execute(
                UserDB.__table__.insert().values(
                    id=fields["id"],
                    username=persona,
                    email=f"{persona}@example.com",
                    user_type=fields.get("user_type", "regular"),
                    is_active=True,
                    is_admin=fields.get("is_admin", False),
                    is_maintainer=fields.get("is_maintainer", False),
                    is_auditor=False,
                    auth_source="local",
                    email_verified=False,
                )
            )
            for name, admin in memberships.items():
                connection.execute(
                    UserProject.__table__.insert().values(
                        id=str(uuid4()),
                        user_id=fields["id"],
                        project_name=name,
                        is_project_admin=admin,
                        is_default=False,
                    )
                )
    return engine


def seed_app(connection, creator_id, **kwargs):
    app_id = uuid4()
    connection.execute(
        APPS.insert().values(
            id=app_id,
            project=PROJECT,
            created_by={"id": creator_id},
            shared=kwargs.get("shared", False),
            is_global=kwargs.get("is_global", False),
            security_state=kwargs.get("security_state", "clear"),
        )
    )
    return app_id


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("persona", PERSONAS)
def test_sql_builders_match_ability(engine, persona, scenario):
    user = make_user(persona)
    creator_id = CREATORS.get(persona, "u-creator")
    with engine.begin() as connection:
        app_id = seed_app(connection, creator_id, **SCENARIOS[scenario])
        user_id = sa.literal(user.id)

        def matches(condition):
            query = sa.select(APPS.c.id).where(APPS.c.id == app_id, condition)
            return connection.execute(query).first() is not None

        granted = expected(persona, scenario)
        assert matches(access.readable(APPS, user_id)) is (Action.READ in granted)
        assert matches(access.manageable(APPS, user_id)) is (Action.WRITE in granted)
        assert matches(access.manageable(APPS, user_id, Action.WRITE)) is (Action.WRITE in granted)
        assert matches(access.manageable(APPS, user_id, Action.DELETE)) is (Action.DELETE in granted)


def test_sql_builders_exclude_inactive_and_deleted_users(engine):
    with engine.begin() as connection:
        app_id = seed_app(connection, "u-creator")
        users = UserDB.__table__
        for change in ({"is_active": False}, {"deleted_at": sa.func.current_timestamp()}):
            connection.execute(users.update().where(users.c.id == "u-creator").values(**change))
            for builder in (access.readable, access.manageable):
                query = sa.select(APPS.c.id).where(APPS.c.id == app_id, builder(APPS, sa.literal("u-creator")))
                assert connection.execute(query).first() is None
            connection.execute(users.update().where(users.c.id == "u-creator").values(is_active=True, deleted_at=None))


def test_module_does_not_import_model():
    source = access.__file__
    with open(source) as handle:
        assert "hosted_application import" not in handle.read()


def _grants(app, level=Action.READ):
    return PreloadedGrants("HostedApplications", {str(app.id): level})


@pytest.mark.parametrize("level", [Action.READ, Action.WRITE, Action.DELETE])
def test_preloaded_read_grant_lists_read_for_non_member(level):
    app = make_app()
    assert Ability(make_user("outsider"), grants=_grants(app, level)).list(app) == [Action.READ]


def test_missing_grants_fails_closed():
    app = make_app()
    assert Ability(make_user("outsider")).list(app) == []
    assert Ability(make_user("outsider"), grants=PreloadedGrants("HostedApplications", {})).list(app) == []


def test_grant_for_other_app_does_not_apply():
    app = make_app()
    other = PreloadedGrants("HostedApplications", {str(uuid4()): Action.READ})
    assert Ability(make_user("outsider"), grants=other).list(app) == []


@pytest.mark.parametrize("persona", ["external", "service_account"])
def test_grant_ineligible_user_gets_nothing(persona):
    app = make_app()
    user = make_user(persona)
    result = Ability(user, grants=_grants(app)).list(app)
    assert result == ([] if persona == "external" else [Action.READ])


def test_grant_does_not_open_suspended_app():
    app = make_app(security_state="suspended")
    assert Ability(make_user("outsider"), grants=_grants(app)).list(app) == []


def test_grants_not_passed_to_other_classes():
    seen = []
    offered = []

    class Probe:
        def is_shared_with(self, user):
            seen.append(user)
            return False

        def is_granted_to(self, user, action, grants):
            offered.append(grants)
            return False

    user = make_user("outsider")
    ability = Ability(user, grants=PreloadedGrants("HostedApplications", {"x": Action.READ}))
    assert ability.shared_with(Probe()) is False
    # The Probe class has no named grants: it is offered its own (none), never the hosted ones.
    assert ability.granted(Probe(), Action.READ) is False
    assert seen == [user] and offered == [None]


# ---- Role.GRANTED: the action-aware grant term (AR-3) ----


def test_granted_is_registered_for_read_only():
    roles = Ability.PERMISSIONS["HostedApplications"]
    assert Role.GRANTED in roles[Action.READ]
    assert Role.GRANTED not in roles[Action.WRITE] and Role.GRANTED not in roles[Action.DELETE]


def test_shared_with_carries_no_grant_term():
    import inspect

    assert "grants" not in inspect.signature(access.is_shared_with).parameters
    assert "grants" not in inspect.signature(HostedApplications.is_shared_with).parameters
    app = make_app()
    assert app.is_shared_with(make_user("outsider")) is False


@pytest.mark.parametrize(
    ("level", "action", "covered"),
    [
        (Action.READ, Action.READ, True),
        (Action.READ, Action.WRITE, False),
        (Action.READ, Action.DELETE, False),
        (Action.WRITE, Action.WRITE, True),
        (Action.WRITE, Action.DELETE, False),
        (Action.DELETE, Action.DELETE, True),
    ],
)
def test_granted_asks_whether_the_preloaded_level_covers_the_action(level, action, covered):
    app = make_app()
    assert Ability(make_user("outsider"), grants=_grants(app, level)).granted(app, action) is covered
    assert app.is_granted_to(make_user("outsider"), action, _grants(app, level)) is covered


def test_granted_never_opens_a_suspended_app_or_an_external_user():
    suspended = make_app(security_state="suspended")
    assert Ability(make_user("outsider"), grants=_grants(suspended)).granted(suspended, Action.READ) is False
    app = make_app()
    assert Ability(make_user("external"), grants=_grants(app)).granted(app, Action.READ) is False


def test_registering_granted_under_write_is_the_only_python_change_edit_access_needs(monkeypatch):
    """With GRANTED under WRITE a WRITE grantee gets WRITE (never DELETE); a READ grantee still only reads."""
    permissions = {
        name: {action: list(roles) for action, roles in by_action.items()}
        for name, by_action in Ability.PERMISSIONS.items()
    }
    permissions["HostedApplications"][Action.WRITE].append(Role.GRANTED)
    monkeypatch.setattr(Ability, "PERMISSIONS", permissions)
    app = make_app()
    assert Ability(make_user("outsider"), grants=_grants(app, Action.WRITE)).list(app) == [Action.READ, Action.WRITE]
    assert Ability(make_user("outsider"), grants=_grants(app, Action.READ)).list(app) == [Action.READ]


def test_manageable_takes_write_or_delete_only():
    with pytest.raises(ValueError):
        access.manageable(APPS, sa.literal("u-outsider"), Action.READ)


def test_a_write_grant_term_joins_manageable_write_but_never_delete(monkeypatch):
    """The SQL half of edit access: adding WRITE to MANAGER_GRANT_ACTIONS puts a grant term in WRITE only."""
    monkeypatch.setattr(access, "MANAGER_GRANT_ACTIONS", frozenset({Action.WRITE}))
    uid = sa.literal("u-outsider")
    assert "permissions" in _sql(access.manageable(APPS, uid, Action.WRITE))
    assert "permissions" not in _sql(access.manageable(APPS, uid, Action.DELETE))


# ---- named view-only grants in the SQL rules ----


def _sql(expression):
    return str(sa.select(APPS.c.id).where(expression).compile(dialect=postgresql.dialect()))


def test_readable_carries_the_grant_term_inside_the_not_suspended_viewer_branch():
    text = _sql(access.readable(APPS, sa.literal("u-outsider")))
    viewer = text[text.index("security_state !=") :]
    assert "permissions" in viewer
    assert "permissions" not in text[: text.index("security_state !=")]


def test_manageable_has_no_grant_term():
    assert frozenset() == access.MANAGER_GRANT_ACTIONS
    for action in (Action.WRITE, Action.DELETE):
        assert "permissions" not in _sql(access.manageable(APPS, sa.literal("u-outsider"), action))


@pytest.fixture
def user_management_on(monkeypatch):
    monkeypatch.setattr(access.config, "ENABLE_USER_MANAGEMENT", True)
    monkeypatch.setattr("codemie.rest_api.models.permission.config.ENABLE_USER_MANAGEMENT", True)


def _grant(connection, app_id, user_id, level=Action.READ):
    connection.execute(
        PERMISSIONS.insert().values(
            id=str(uuid4()),
            resource_type=ResourceType.HOSTED_APPLICATION,
            resource_id=app_id,
            principal_type=PrincipalType.USER,
            principal_id=user_id,
            permission_level=level,
        )
    )


def _matches(connection, app_id, condition):
    return connection.execute(sa.select(APPS.c.id).where(APPS.c.id == app_id, condition)).first() is not None


def test_a_grant_makes_the_app_readable_but_not_manageable(engine, user_management_on):
    with engine.begin() as connection:
        app_id = seed_app(connection, "u-creator")
        uid = sa.literal("u-outsider")
        assert not _matches(connection, app_id, access.readable(APPS, uid))
        _grant(connection, app_id, "u-outsider")
        assert _matches(connection, app_id, access.readable(APPS, uid))
        assert not _matches(connection, app_id, access.manageable(APPS, uid))
        assert not _matches(connection, app_id, access.readable(APPS, sa.literal("u-member")))
        connection.execute(PERMISSIONS.delete())
        assert not _matches(connection, app_id, access.readable(APPS, uid))


def test_a_grant_does_not_open_a_suspended_app_or_an_ineligible_user(engine, user_management_on):
    with engine.begin() as connection:
        suspended = seed_app(connection, "u-creator", security_state="suspended")
        clear = seed_app(connection, "u-creator")
        for app_id in (suspended, clear):
            _grant(connection, app_id, "u-outsider")
            _grant(connection, app_id, "u-ext")
        assert not _matches(connection, suspended, access.readable(APPS, sa.literal("u-outsider")))
        assert _matches(connection, clear, access.readable(APPS, sa.literal("u-outsider")))
        assert not _matches(connection, clear, access.readable(APPS, sa.literal("u-ext")))


def test_grants_are_ignored_while_user_management_is_off(engine):
    with engine.begin() as connection:
        app_id = seed_app(connection, "u-creator")
        _grant(connection, app_id, "u-outsider")
        assert not _matches(connection, app_id, access.readable(APPS, sa.literal("u-outsider")))


@pytest.mark.parametrize(
    ("persona", "creator_id", "expected"),
    [
        ("platform_admin", "u-creator", True),
        ("maintainer", "u-creator", True),
        ("creator", "u-creator", True),
        ("project_admin", "u-creator", False),
        ("member", "u-creator", False),
        ("outsider", "u-creator", False),
        ("external", "u-creator", False),
        ("external_creator", "u-extc", False),
    ],
)
def test_grant_remover_admits_admin_maintainer_or_creator(engine, persona, creator_id, expected):
    uid = sa.literal(PERSONAS[persona][0]["id"])
    with engine.begin() as connection:
        app_id = seed_app(connection, creator_id)
        assert _matches(connection, app_id, access.grant_remover(APPS, uid)) is expected


def test_grant_remover_without_an_app_row_admits_only_admins(engine):
    with engine.connect() as connection:

        def admitted(persona):
            uid = sa.literal(PERSONAS[persona][0]["id"])
            return connection.execute(sa.select(access.grant_remover(None, uid))).scalar()

        assert admitted("platform_admin") and admitted("maintainer")
        assert not admitted("creator") and not admitted("member") and not admitted("external")


def test_grant_remover_excludes_inactive_and_deleted_admins(engine):
    users = UserDB.__table__
    with engine.begin() as connection:
        for change in ({"is_active": False}, {"deleted_at": sa.func.current_timestamp()}):
            connection.execute(users.update().where(users.c.id == "u-admin").values(**change))
            assert not connection.execute(sa.select(access.grant_remover(None, sa.literal("u-admin")))).scalar()
            connection.execute(users.update().where(users.c.id == "u-admin").values(is_active=True, deleted_at=None))


def test_grant_remover_composes_as_a_scalar_column(engine):
    with engine.begin() as connection:
        app_id = seed_app(connection, "u-creator")
        row = connection.execute(
            sa.select(APPS.c.id, access.grant_remover(APPS, sa.literal("u-creator")).label("can")).where(
                APPS.c.id == app_id
            )
        ).one()
        assert row.can


@pytest.mark.parametrize("persona", PERSONAS)
def test_is_grant_remover_agrees_with_the_sql_grant_remover(engine, persona):
    """The Python twin used to revoke a share link (S-17) admits whom ``grant_remover`` admits."""
    user = make_user(persona)
    with engine.begin() as connection:
        app_id = seed_app(connection, "u-creator")
        in_sql = _matches(connection, app_id, access.grant_remover(APPS, sa.literal(user.id)))
    assert access.is_grant_remover("u-creator", user) is in_sql


# ---- one suspension refusal list, applied one way (AR-12) ----


@pytest.mark.parametrize("action", sorted(access.SUSPEND_GATED_ACTIONS))
def test_refuses_is_the_one_predicate_over_the_gated_list(action):
    assert access.refuses(make_app(security_state="suspended"), action) is True
    assert access.refuses(make_app(), action) is False


def test_refuses_rejects_an_action_outside_the_list():
    with pytest.raises(ValueError):
        access.refuses(make_app(security_state="suspended"), "delete")
    with pytest.raises(ValueError):
        access.suspend_refuses(make_app(), "rename")


def test_refused_actions_lists_the_constant_only_while_suspended():
    assert access.refused_actions(make_app()) == []
    assert access.refused_actions(make_app(security_state="suspended")) == sorted(access.SUSPEND_GATED_ACTIONS)


def test_refuses_sql_matches_the_python_predicate(engine):
    with engine.begin() as connection:
        suspended = seed_app(connection, "u-creator", security_state="suspended")
        clear = seed_app(connection, "u-creator")
        assert _matches(connection, suspended, access.refuses_sql(APPS, "share_on"))
        assert not _matches(connection, clear, access.refuses_sql(APPS, "share_on"))
    with pytest.raises(ValueError):
        access.refuses_sql(APPS, "delete")


def test_no_hosted_service_reads_app_security_state_outside_the_access_module():
    """The refusal list is applied through ``refuses``/``suspend_refuses``/``refuses_sql`` only."""
    import pathlib

    root = pathlib.Path(access.__file__).parents[2] / "service" / "hosted_applications"
    offenders = []
    for path in root.glob("*.py"):
        if path.name in ("administration.py", "projections.py"):
            continue  # suspend and reinstate write the state; the projection shows it as ``suspended``
        text = path.read_text()
        if "is_suspended(" in text or "SECURITY_SUSPENDED" in text or 'security_state != "suspended"' in text:
            offenders.append(path.name)
    assert offenders == []


def test_access_module_imports_only_the_shared_tables_and_no_import_hides_in_a_function():
    """AR-16: the rest_api dependency is explicit and pinned to the users, user_projects and permissions tables."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(access.__file__).read_text())
    nested = [
        node.module
        for function in ast.walk(tree)
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(function)
        if isinstance(node, ast.ImportFrom)
    ]
    assert nested == []
    rest_api = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("codemie.rest_api")
    }
    assert rest_api == {
        "codemie.rest_api.models.permission",
        "codemie.rest_api.models.user_management",
        "codemie.rest_api.security.user",  # TYPE_CHECKING only
    }
