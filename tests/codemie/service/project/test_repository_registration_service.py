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

"""Remote normalization and registration store (EPMCDME-16440)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session

from codemie.core.exceptions import ExtendedHTTPException
from tests.codemie.service.project import registration_db
from codemie.rest_api.models.repository_project_registration import RepositoryProjectRegistration
from codemie.service.project.repository_registration_service import (
    RepositoryRegistrationRepository,
    normalize_remote,
)

CANON = "github.com/Org/Repo"


@pytest.mark.parametrize(
    "remote",
    [
        "git@github.com:Org/Repo.git",
        "git@GitHub.com:Org/Repo",
        "ssh://git@github.com/Org/Repo.git",
        "ssh://git@github.com:22/Org/Repo.git",
        "https://github.com/Org/Repo",
        "https://github.com/Org/Repo.git/",
        "https://user:s3cret@GITHUB.COM:443/Org/Repo.git",
        "http://github.com:80/Org/Repo",
        "  https://github.com/Org/Repo  ",
    ],
)
def test_equivalent_spellings_collapse(remote):
    assert normalize_remote(remote) == CANON


@pytest.mark.parametrize(
    "remote",
    [
        "https://github.com/org/repo",  # path case is identity
        "https://github.com/Org/Repo2",
        "https://github.com:8443/Org/Repo",  # non-default port is identity
        "ssh://git@github.com:2222/Org/Repo",
        "https://gitlab.com/Org/Repo",
    ],
)
def test_distinct_remotes_stay_distinct(remote):
    assert normalize_remote(remote) != CANON


def test_non_default_port_kept():
    assert normalize_remote("ssh://git@Host.example:2222/a/b.git") == "host.example:2222/a/b"
    assert normalize_remote("https://host.example:8443/a/b") == "host.example:8443/a/b"


@pytest.mark.parametrize(
    "remote", ["", "   ", "nonsense", "https://", "https://host", "git@host:", "https://h:bad/a/b"]
)
def test_unparseable_raises_without_echoing_input(remote):
    with pytest.raises(ValueError) as exc:
        normalize_remote(remote)
    assert remote.strip() == "" or remote not in str(exc.value)


def test_credentials_not_in_error():
    with pytest.raises(ValueError) as exc:
        normalize_remote("https://user:s3cret@host")
    assert "s3cret" not in str(exc.value)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    saved = registration_db.create(engine)
    try:
        with Session(engine) as s:
            for name in ("proj", "other", "a", "b"):
                registration_db.add_project(s, name)
            yield s
    finally:
        registration_db.restore(saved)


def test_register_and_find(session):
    reg = RepositoryRegistrationRepository.register(session, "proj", "u1", remote="git@github.com:Org/Repo.git")
    assert reg.normalized_remote == CANON
    found = RepositoryRegistrationRepository.find_matches(session, "https://github.com/Org/Repo")
    assert [m.project_name for m in found] == ["proj"]


def test_find_zero_and_unparseable(session):
    assert RepositoryRegistrationRepository.find_matches(session, "https://github.com/x/y") == []
    assert RepositoryRegistrationRepository.find_matches(session, "garbage") == []


def test_register_idempotent_same_project(session):
    RepositoryRegistrationRepository.register(session, "proj", "u1", remote="https://github.com/Org/Repo")
    RepositoryRegistrationRepository.register(session, "proj", "u2", remote="git@github.com:Org/Repo.git")
    assert len(RepositoryRegistrationRepository.list_for_project(session, "proj")) == 1


def test_register_other_project_conflicts(session):
    RepositoryRegistrationRepository.register(session, "proj", "u1", remote="https://github.com/Org/Repo")
    with pytest.raises(ExtendedHTTPException) as exc:
        RepositoryRegistrationRepository.register(session, "other", "u1", remote="git@github.com:Org/Repo")
    assert exc.value.code == 409


def test_register_unparseable_is_400(session):
    with pytest.raises(ExtendedHTTPException) as exc:
        RepositoryRegistrationRepository.register(session, "proj", "u1", remote="nonsense")
    assert exc.value.code == 400


def test_find_returns_list_for_pre_normalized_row(session):
    # normalized_remote is the PK, so "many" cannot occur here; the list contract lets the resolver detect it
    # on a store without the constraint.
    session.add(RepositoryProjectRegistration(normalized_remote=CANON, project_name="a", created_by="u"))
    session.commit()
    found = RepositoryRegistrationRepository.find_matches(session, "https://github.com/Org/Repo")
    assert len(found) == 1


def test_unregister(session):
    RepositoryRegistrationRepository.register(session, "proj", "u1", remote="https://github.com/Org/Repo")
    assert RepositoryRegistrationRepository.unregister(session, "other", remote="https://github.com/Org/Repo") is None
    assert RepositoryRegistrationRepository.unregister(session, "proj", remote="git@github.com:Org/Repo.git") == CANON
    assert RepositoryRegistrationRepository.list_for_project(session, "proj") == []
