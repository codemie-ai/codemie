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

"""The hosted grant descriptor reads the sharing rule: creator, project admin, platform admin, maintainer."""

from types import SimpleNamespace

from codemie.rest_api.security.user import User
from codemie.service.hosted_applications.grant_descriptor import DESCRIPTOR, MANAGERS_ONLY

PROJECT = "proj-a"


def _user(**fields) -> User:
    user = User(id=fields.pop("id"), username="u", **fields)
    user.is_admin = bool(fields.get("is_maintainer"))  # ENV=local forces is_admin on at construction
    return user


def _app(creator_id: str | None = "u-creator"):
    created_by = SimpleNamespace(id=creator_id) if creator_id else None
    return SimpleNamespace(created_by=created_by, project=PROJECT)


def test_platform_project_admin_is_a_manager():
    admin = _user(id="u-admin", admin_project_names=[PROJECT])
    assert DESCRIPTOR.is_owner(_app(), admin) is True


def test_platform_admin_and_maintainer_are_managers():
    platform_admin = _user(id="u-pa")
    platform_admin.is_admin = True  # set explicitly: only ENV=local makes every user an admin
    assert DESCRIPTOR.is_owner(_app(), platform_admin)
    assert DESCRIPTOR.is_owner(_app(), _user(id="u-m", is_maintainer=True))


def test_creator_is_a_manager():
    assert DESCRIPTOR.is_owner(_app(), _user(id="u-creator"))


def test_outsider_and_admin_of_another_project_are_not_managers():
    assert not DESCRIPTOR.is_owner(_app(), _user(id="u-out"))
    other = _user(id="u-other", admin_project_names=["proj-b"])
    assert not DESCRIPTOR.is_owner(_app(), other)
    assert not DESCRIPTOR.is_owner(_app(creator_id=None), _user(id="u-out"))


def test_not_owner_message_names_the_managers():
    assert DESCRIPTOR.not_owner_message == MANAGERS_ONLY
