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

"""Unit tests for the per-viewer CLI analytics project-setting policy (EPMCDME-16025)."""

from unittest.mock import patch

import pytest

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User
from codemie.service.project import cli_analytics_policy as policy


def make_user(**kwargs) -> User:
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(id="u-1", email="u@example.com", **kwargs)


# (label, user kwargs, can view "proj", can write)
ROLES = [
    ("admin", {"is_admin": True}, True, True),
    ("maintainer", {"is_maintainer": True}, True, True),
    ("project_admin_of_proj", {"admin_project_names": ["proj"], "project_names": ["proj"]}, True, False),
    ("project_admin_of_other", {"admin_project_names": ["other"], "project_names": ["other"]}, False, False),
    ("auditor", {"is_auditor": True}, False, False),
    ("member", {"project_names": ["proj"]}, False, False),
    ("outsider", {}, False, False),
]


@pytest.mark.parametrize(("label", "kwargs", "can_view", "can_write"), ROLES, ids=[r[0] for r in ROLES])
def test_role_matrix(label, kwargs, can_view, can_write):
    user = make_user(**kwargs)

    assert policy.can_view(user, "proj") is can_view
    assert policy.visible_value(user, "proj", True) is (True if can_view else None)
    assert policy.visible_value(user, "proj", False) is (False if can_view else None)
    if can_write:
        policy.require_write(user, {"cli_analytics_enabled"})
    else:
        with pytest.raises(ExtendedHTTPException) as exc:
            policy.require_write(user, {"cli_analytics_enabled"})
        assert exc.value.code == 403


def test_require_write_ignores_requests_without_the_field():
    policy.require_write(make_user(admin_project_names=["proj"]), {"description", "name"})
    policy.require_write(make_user(), set())
