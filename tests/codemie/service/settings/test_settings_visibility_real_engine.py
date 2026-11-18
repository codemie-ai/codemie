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

"""Settings list visibility, asserted on rows returned by a real (in-memory SQLite) engine.

Each scope's visibility rule lives in ``SettingsIndexService``. These tests seed one table with rows that each
clause should keep or drop, then read the ids back through the real query, so a clause that is lost or
weakened shows up as a wrong row set rather than as a mocked call.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable
from sqlmodel import Session

from codemie.configs import config
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.settings import Settings, SettingType
from codemie.rest_api.security.user import User
from codemie.service.settings.settings import SettingsService
from codemie.service.settings.settings_index_service import SettingsIndexService
from codemie_tools.base.models import CredentialTypes

ME = "me-id"
ME_NAME = "Me Person"
OTHER = "other-id"
OTHER_NAME = "Other Person"
BASE_DATE = datetime(2026, 1, 1, 12, 0, 0)

USER = SettingType.USER
PROJECT = SettingType.PROJECT


@compiles(JSONB, "sqlite")
def _as_json(_type, compiler, **_kw):
    return "JSON"


@pytest.fixture
def engine(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.execute(CreateTable(Settings.__table__))  # not create_all: ix_settings_project_name is declared twice
    monkeypatch.setattr(Settings, "get_engine", staticmethod(lambda: engine))
    monkeypatch.setattr(config, "ENV", "dev")
    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", True)
    return engine


def make_user(*, is_admin: bool, admin_projects: list[str]) -> User:
    """Build the requester. Call only after ``engine``: ``is_admin`` is resolved when the user is created."""
    return User(
        id=ME,
        username="me@example.com",
        name=ME_NAME,
        is_admin=is_admin,
        project_names=list(admin_projects),
        admin_project_names=list(admin_projects),
    )


def make_row(
    row_id: str,
    *,
    scope: SettingType | None,
    owner: str | None,
    project: str,
    day: int,
    alias: str | None = None,
    credential_type: CredentialTypes = CredentialTypes.JIRA,
    is_global: bool = False,
    credential_values: list[dict] | None = None,
) -> Settings:
    owner_name = {ME: ME_NAME, OTHER: OTHER_NAME}.get(owner)
    return Settings(
        id=row_id,
        user_id=owner,
        created_by=CreatedByUser(id=owner, username=owner, name=owner_name) if owner else None,
        project_name=project,
        alias=alias if alias is not None else row_id,
        credential_type=credential_type,
        credential_values=credential_values or [],
        setting_type=scope,
        is_global=is_global,
        date=BASE_DATE + timedelta(days=day),
    )


def insert(engine, rows: list[Settings]) -> None:
    with Session(engine) as session:
        session.add_all(rows)
        session.commit()


@pytest.fixture
def seeded(engine):
    """ME administers project ``A`` only. Every row has a date of its own; ``u_mine`` is the newest."""
    insert(
        engine,
        [
            make_row("u_mine", scope=USER, owner=ME, project="A", day=8, alias="mine", is_global=True),
            make_row("u_internal", scope=USER, owner=ME, project="A", day=7, alias="__internal__x"),
            make_row("u_wild", scope=USER, owner=ME, project="A", day=6, alias="myinternal-x"),
            make_row("u_other", scope=USER, owner=OTHER, project="A", day=5),
            make_row("p_a", scope=PROJECT, owner=OTHER, project="A", day=4),
            make_row("p_b_mine", scope=PROJECT, owner=ME, project="B", day=3),
            make_row(
                "p_budget",
                scope=PROJECT,
                owner=None,
                project="A",
                day=2,
                alias=f"{SettingsService.PROJECT_BUDGET_ALIAS_PREFIX}A",
                credential_type=CredentialTypes.LITE_LLM,
            ),
            make_row("untyped", scope=None, owner=ME, project="A", day=1),
        ],
    )
    # The ORM skips a None value and lets the column default (USER) fire, so store the NULL explicitly.
    with engine.begin() as conn:
        conn.execute(update(Settings.__table__).where(Settings.__table__.c.id == "untyped").values(setting_type=None))
    return engine


PERSONAS = {
    "project_admin": {"is_admin": False, "admin_projects": ["A"]},
    "platform_admin": {"is_admin": True, "admin_projects": ["A"]},
    "member": {"is_admin": False, "admin_projects": []},
}


def ids(result: dict) -> list[str]:
    return [item.id for item in result["data"]]


@pytest.mark.parametrize(
    "persona, scope, expected",
    [
        ("project_admin", USER, {"u_mine"}),
        ("project_admin", PROJECT, {"p_a"}),
        ("platform_admin", USER, {"u_mine"}),
        ("platform_admin", PROJECT, {"p_a", "p_b_mine", "p_budget"}),
    ],
)
def test_per_scope_visibility(seeded, persona, scope, expected):
    user = make_user(**PERSONAS[persona])

    result = SettingsIndexService.run(user=user, scopes=frozenset({scope}))

    assert set(ids(result)) == expected
    assert result["pagination"]["total"] == len(expected)


def test_untyped_row_is_stored_without_scope(seeded):
    with Session(seeded) as session:
        assert session.get(Settings, "untyped").setting_type is None


def test_member_without_admin_projects_sees_no_project_rows(seeded):
    user = make_user(**PERSONAS["member"])

    result = SettingsIndexService.run(user=user, scopes=frozenset({PROJECT}))

    assert ids(result) == []
    assert result["pagination"]["total"] == 0


def test_equal_dates_order_by_id(engine):
    insert(engine, [make_row(row_id, scope=USER, owner=ME, project="A", day=0) for row_id in ("c", "a", "b")])
    user = make_user(**PERSONAS["project_admin"])

    result = SettingsIndexService.run(user=user, scopes=frozenset({USER}))

    assert ids(result) == ["a", "b", "c"]


def test_unknown_scope_raises(engine):
    user = make_user(**PERSONAS["project_admin"])

    with pytest.raises(ValueError):
        SettingsIndexService._scope_condition(user, None)


# --- Combined list: run() with multiple scopes --------------------------------------------------------------------

ALL = frozenset({USER, PROJECT})


def test_all_scopes_project_admin(seeded):
    user = make_user(**PERSONAS["project_admin"])

    result = SettingsIndexService.run(user=user, scopes=ALL)

    # Absent: u_other (another person's User row), p_b_mine (own Project row outside the administered projects),
    # p_budget (budget row), u_internal and u_wild (internal-alias LIKE) and untyped (no scope).
    assert set(ids(result)) == {"u_mine", "p_a"}
    assert result["pagination"]["total"] == 2
    assert {item.setting_type for item in result["data"]} == {USER, PROJECT}


def test_all_scopes_platform_admin(seeded):
    user = make_user(**PERSONAS["platform_admin"])

    result = SettingsIndexService.run(user=user, scopes=ALL)

    assert set(ids(result)) == {"u_mine", "p_a", "p_b_mine", "p_budget"}
    assert not {"u_other", "u_internal", "u_wild", "untyped"} & set(ids(result))


@pytest.mark.parametrize("persona", list(PERSONAS))
def test_all_scopes_is_the_union_of_each_scope(seeded, persona):
    user = make_user(**PERSONAS[persona])
    per_scope_ids = {
        row_id
        for scope in (USER, PROJECT)
        for row_id in ids(SettingsIndexService.run(user=user, scopes=frozenset({scope})))
    }

    assert set(ids(SettingsIndexService.run(user=user, scopes=ALL))) == per_scope_ids


@pytest.mark.parametrize(
    "filters",
    [
        {"search": "mine"},
        {"project": "A"},
        {"type": CredentialTypes.LITE_LLM.value},
        {"is_global": True},
        {"created_by": OTHER_NAME},
    ],
)
def test_all_scopes_filters_are_union_of_scopes(seeded, filters):
    user = make_user(**PERSONAS["platform_admin"])

    combined = set(ids(SettingsIndexService.run(user=user, scopes=ALL, filters=filters)))
    per_scope = {
        row_id
        for scope in (USER, PROJECT)
        for row_id in ids(SettingsIndexService.run(user=user, scopes=frozenset({scope}), filters=filters))
    }

    assert combined == per_scope
    assert combined  # every case matches at least one visible row


def test_all_scopes_ignores_scope_key_in_filters(seeded):
    user = make_user(**PERSONAS["platform_admin"])

    unfiltered = ids(SettingsIndexService.run(user=user, scopes=ALL))
    with_scope_key = ids(SettingsIndexService.run(user=user, scopes=ALL, filters={"scope": "user"}))

    assert with_scope_key == unfiltered
    assert {"p_a", "p_b_mine", "p_budget"} <= set(with_scope_key)  # Project rows are not narrowed away


def test_all_scopes_search_puts_project_name_matches_first(engine):
    insert(
        engine,
        [
            make_row("u_alpha", scope=USER, owner=ME, project="A", day=9, alias="alpha-key"),
            make_row("p_alpha", scope=PROJECT, owner=OTHER, project="alpha", day=1, alias="proj-key"),
        ],
    )
    user = make_user(**PERSONAS["platform_admin"])

    result = SettingsIndexService.run(user=user, scopes=ALL, filters={"search": "alpha"})

    assert ids(result) == ["p_alpha", "u_alpha"]


def test_all_scopes_equal_dates_page_every_row_once(engine):
    rows = [make_row(f"u{i:02d}", scope=USER, owner=ME, project="A", day=0) for i in range(12)]
    rows += [make_row(f"p{i:02d}", scope=PROJECT, owner=OTHER, project="A", day=0) for i in range(13)]
    expected_ids = sorted(row.id for row in rows)
    insert(engine, rows)
    user = make_user(**PERSONAS["project_admin"])

    pages = [SettingsIndexService.run(user=user, scopes=ALL, page=page, per_page=10) for page in range(3)]
    paged_ids = [row_id for page in pages for row_id in ids(page)]

    assert sorted(paged_ids) == expected_ids
    assert len(paged_ids) == len(set(paged_ids)) == 25
    assert pages[0]["pagination"] == {"page": 0, "per_page": 10, "total": 25, "pages": 3}


def test_all_scopes_masks_like_single_scope(engine):
    sensitive_key = SettingsIndexService.LIST_OF_SENSITIVE_FIELDS[0]
    insert(
        engine,
        [
            make_row(
                "u_secret",
                scope=USER,
                owner=ME,
                project="A",
                day=1,
                credential_values=[{"key": sensitive_key, "value": "s3cr3t"}, {"key": "url", "value": "https://x"}],
            )
        ],
    )
    user = make_user(**PERSONAS["project_admin"])

    def values(result: dict) -> list[tuple[str, str]]:
        return [(cred.key, cred.value) for cred in result["data"][0].credential_values]

    combined = values(SettingsIndexService.run(user=user, scopes=ALL))

    assert combined == values(SettingsIndexService.run(user=user, scopes=frozenset({USER})))
    assert (sensitive_key, SettingsIndexService.MASKED_VALUE) in combined
    assert ("url", "https://x") in combined


def test_empty_scope_set_raises(engine):
    user = make_user(**PERSONAS["project_admin"])

    with pytest.raises(ValueError):
        SettingsIndexService.run(user=user, scopes=frozenset())
