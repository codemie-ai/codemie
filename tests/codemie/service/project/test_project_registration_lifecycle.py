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

"""Repository registrations: an unambiguous public key (IR-003) and the project lifecycle (IR-004), EPMCDME-16440."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, select

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.rest_api.models.repository_project_registration import RepositoryProjectRegistration
from codemie.service.project.project_service import ProjectService
from codemie.service.project.repository_registration_service import RepositoryRegistrationRepository as Regs
from codemie.service.project.repository_registration_service import canonical_identity
from tests.codemie.service.project import registration_db

P = "codemie.service.project.project_service"
REMOTE = "https://host.example/Org/Repo"
KEY = "host.example/Org/Repo"
A_RAW, A_KEY = "https://host.example:8443/Org/Repo", "host.example:8443/Org/Repo"  # a port
B_RAW, B_KEY = "git@host.example:8443/Org/Repo", "host.example/8443/Org/Repo"  # scp: a path starting 8443/


@pytest.fixture
def session():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    saved = registration_db.create(engine)
    try:
        with Session(engine) as s:
            for name in ("proj", "other", "old", "new-project"):
                registration_db.add_project(s, name)
            yield s
    finally:
        registration_db.restore(saved)


def _owners(session) -> dict[str, str]:
    session.expire_all()
    return {r.normalized_remote: r.project_name for r in session.exec(select(RepositoryProjectRegistration)).all()}


# ── IR-003: the key is exact, the raw remote is normalized, and the two never share a field ──────────────


def test_a_port_and_an_scp_path_register_side_by_side_in_one_project(session):
    a = Regs.register(session, "proj", "u", remote=A_RAW)
    b = Regs.register(session, "proj", "u", remote=B_RAW)
    assert (a.normalized_remote, b.normalized_remote) == (A_KEY, B_KEY)
    assert _owners(session) == {A_KEY: "proj", B_KEY: "proj"}


@pytest.mark.parametrize("target, other", [(A_KEY, B_KEY), (B_KEY, A_KEY)])
def test_delete_by_key_removes_exactly_the_named_registration(session, target, other):
    Regs.register(session, "proj", "u", remote=A_RAW)
    Regs.register(session, "proj", "u", remote=B_RAW)
    assert Regs.unregister(session, "proj", key=target) == target
    assert _owners(session) == {other: "proj"}


@pytest.mark.parametrize("raw, key, other", [(A_RAW, A_KEY, B_KEY), (B_RAW, B_KEY, A_KEY)])
def test_delete_by_raw_remote_removes_exactly_what_it_normalizes_to(session, raw, key, other):
    Regs.register(session, "proj", "u", remote=A_RAW)
    Regs.register(session, "proj", "u", remote=B_RAW)
    assert Regs.unregister(session, "proj", remote=raw) == key
    assert _owners(session) == {other: "proj"}


@pytest.mark.parametrize("raw, key", [(A_RAW, A_KEY), (B_RAW, B_KEY)])
def test_put_by_key_or_raw_touches_only_its_own_row(session, raw, key):
    Regs.register(session, "proj", "u", remote=A_RAW)
    Regs.register(session, "proj", "u", remote=B_RAW)
    assert Regs.register(session, "proj", "u", key=key).normalized_remote == key  # idempotent, no new row
    assert Regs.register(session, "proj", "u", remote=raw).normalized_remote == key
    assert _owners(session) == {A_KEY: "proj", B_KEY: "proj"}


def test_put_conflicts_only_with_the_row_it_names_across_projects(session):
    Regs.register(session, "proj", "u", remote=A_RAW)
    Regs.register(session, "other", "u", remote=B_RAW)
    with pytest.raises(ExtendedHTTPException) as exc:
        Regs.register(session, "other", "u", key=A_KEY)  # A belongs to proj
    assert exc.value.code == 409
    assert Regs.register(session, "other", "u", key=B_KEY).project_name == "other"  # its own row: idempotent
    assert Regs.unregister(session, "other", key=A_KEY) is None  # not other's row
    assert _owners(session) == {A_KEY: "proj", B_KEY: "other"}


@pytest.mark.parametrize(
    "raw, key",
    [
        ("https://host.example/Org/Repo.git", KEY),
        (A_RAW + ".git", A_KEY),
        ("ssh://git@host.example:2222/Org/Repo", "host.example:2222/Org/Repo"),
        ("https://[2001:db8::1]/Org/Repo.git", "[2001:db8::1]/Org/Repo"),
        ("https://[2001:db8::1]:8443/Org/Repo", "[2001:db8::1]:8443/Org/Repo"),
        ("git@github.com:Org/Repo.git", "github.com/Org/Repo"),
    ],
)
def test_the_returned_key_round_trips_to_delete(session, raw, key):
    assert Regs.register(session, "proj", "u", remote=raw).normalized_remote == key
    listed = [r.normalized_remote for r in Regs.list_for_project(session, "proj")]
    assert listed == [key]
    assert Regs.unregister(session, "proj", key=listed[0]) == key
    assert Regs.list_for_project(session, "proj") == []


def test_exactly_one_of_remote_and_key_is_required(session):
    for kwargs in ({}, {"remote": REMOTE, "key": KEY}):
        with pytest.raises(ExtendedHTTPException) as exc:
            Regs.register(session, "proj", "u", **kwargs)
        assert exc.value.code == 400
        with pytest.raises(ValueError):
            Regs.unregister(session, "proj", **kwargs)


@pytest.mark.parametrize(
    "text", ["https://host.example/a/b", "git@host.example:a/b", "host.example/a/b.git", "u:p@h/a/b"]
)
def test_a_key_must_be_a_stored_identity_not_a_remote(session, text):
    assert canonical_identity(text) is None
    with pytest.raises(ExtendedHTTPException) as exc:
        Regs.register(session, "proj", "u", key=text)
    assert exc.value.code == 400


# ── IR-004: registration follows a live project ────────────────────────────────────────────────────────


def test_a_registration_for_a_gone_deleted_or_personal_project_writes_nothing(session):
    registration_db.add_project(session, "soft", deleted=True)
    registration_db.add_project(session, "me", project_type="personal")
    for name, code in (("ghost", 404), ("soft", 404), ("me", 400)):
        with pytest.raises(ExtendedHTTPException) as exc:
            Regs.register(session, name, "u", remote=REMOTE)
        assert exc.value.code == code
    assert _owners(session) == {}


class _NoRefresh:
    """The real session, except that the Application stubs of these tests are not persistent rows to refresh."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def refresh(self, _obj):
        pass


def _rename(session, fail: bool = False):
    @contextmanager
    def fake_get_session():
        try:
            yield _NoRefresh(session)
        except Exception:
            session.rollback()
            raise

    admin = MagicMock(is_admin=True, is_maintainer=False, id="admin", is_admin_or_maintainer=True)
    current = session.exec(select(Application).where(Application.name == "old")).one()
    with (
        patch(f"{P}.get_session", fake_get_session),
        patch(f"{P}.application_repository") as apps,
        patch(f"{P}.user_project_repository") as members,
        patch(f"{P}.cost_center_service"),
        patch(f"{P}.activity_event_repository") as events,
    ):
        apps.get_by_name.return_value = current
        apps.get_by_name_case_insensitive.return_value = None
        members.get_by_project_name.return_value = []
        members.is_admin.return_value = True
        if fail:
            events.insert.side_effect = RuntimeError("boom")

        def rename(s, project, **kw):  # what application_repository.update_project does to the row
            project.name = project.id = kw["name"]
            s.add(project)
            return project

        apps.update_project.side_effect = rename
        ProjectService.update_project(admin, "old", name="renamed")


def _delete(session, fail: bool = False):
    def delete_by_name(s, name):  # what application_repository.delete_by_name does
        s.delete(s.exec(select(Application).where(Application.name == name)).one())

    with (
        patch(f"{P}.application_repository") as apps,
        patch(f"{P}.user_project_repository") as members,
        patch(f"{P}.budget_repository"),
        patch(f"{P}.project_budget_group_repository"),
        patch(f"{P}.activity_event_repository") as events,
    ):
        members.get_by_project_name.return_value = []
        apps.get_project_entity_counts_bulk.return_value = {}
        apps.delete_by_name.side_effect = delete_by_name
        if fail:
            events.insert.side_effect = RuntimeError("boom")
        ProjectService.delete_project(
            session, "old", Application.ProjectType.SHARED, "admin", "DELETE /v1/projects/old"
        )


def test_rename_moves_the_registrations_and_the_old_name_is_not_registrable(session):
    Regs.register(session, "old", "u", remote=REMOTE)
    _rename(session)
    session.commit()
    assert _owners(session) == {KEY: "renamed"}
    assert Regs.list_for_project(session, "old") == []
    with pytest.raises(ExtendedHTTPException) as exc:  # a stale authorization for the old name
        Regs.register(session, "old", "u", remote="https://host.example/Org/Other")
    assert exc.value.code == 404 and _owners(session) == {KEY: "renamed"}


def test_a_failed_rename_leaves_the_registrations_untouched(session):
    Regs.register(session, "old", "u", remote=REMOTE)
    with pytest.raises(RuntimeError):
        _rename(session, fail=True)
    session.rollback()
    assert _owners(session) == {KEY: "old"}


def test_delete_drops_the_registrations_and_a_stale_put_cannot_orphan_one(session):
    Regs.register(session, "old", "u", remote=REMOTE)
    Regs.register(session, "other", "u", remote="https://host.example/Org/Other")
    _delete(session)
    session.commit()
    assert _owners(session) == {"host.example/Org/Other": "other"}
    with pytest.raises(ExtendedHTTPException) as exc:  # authorized before the delete, inserting after it
        Regs.register(session, "old", "u", remote=REMOTE)
    assert exc.value.code == 404
    assert Regs.register(session, "new-project", "u", remote=REMOTE).project_name == "new-project"


def test_a_failed_delete_keeps_project_and_registrations(session):
    Regs.register(session, "old", "u", remote=REMOTE)
    with pytest.raises(RuntimeError):
        _delete(session, fail=True)
    session.rollback()
    assert _owners(session) == {KEY: "old"}
    assert session.exec(select(Application).where(Application.name == "old")).first() is not None
