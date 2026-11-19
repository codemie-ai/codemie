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

"""Tests for the IDP_MANAGED_ENABLED guard on project create/delete and membership endpoints.

When projects and their members are managed in the identity provider, every endpoint that
creates or deletes a project, or changes who belongs to it, must carry ``require_no_idp_lock``.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest

from codemie.rest_api.routers.projects import crud, management, membership
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.idp_lock import require_no_idp_lock

_IDP_LOCK_MSG = "User management is controlled by the identity provider."

LOCKED_HANDLERS = [
    pytest.param(crud.create_project, id="create_project"),
    pytest.param(management.delete_project, id="delete_project"),
    pytest.param(management.update_project_ownership, id="update_project_ownership"),
    pytest.param(membership.assign_user_to_project, id="assign_user_to_project"),
    pytest.param(membership.update_user_project_assignment, id="update_user_project_assignment"),
    pytest.param(membership.remove_user_from_project, id="remove_user_from_project"),
    pytest.param(membership.bulk_assign_users_to_project, id="bulk_assign_users_to_project"),
    pytest.param(membership.validate_users_from_csv, id="validate_users_from_csv"),
    pytest.param(membership.import_users_from_csv, id="import_users_from_csv"),
    pytest.param(membership.bulk_remove_users_from_project, id="bulk_remove_users_from_project"),
]


def _dependency_functions(handler):
    """Return the set of dependency callables wired into a FastAPI handler."""
    sig = inspect.signature(handler)
    return {p.default.dependency for p in sig.parameters.values() if hasattr(p.default, "dependency")}


@pytest.mark.parametrize("handler", LOCKED_HANDLERS)
def test_handler_has_idp_guard_wired(handler):
    assert require_no_idp_lock in _dependency_functions(handler)


def test_idp_guard_raises_403_when_idp_managed():
    mock_config = MagicMock()
    mock_config.IDP_MANAGED_ENABLED = True
    with patch("codemie.rest_api.security.idp_lock.config", mock_config):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            require_no_idp_lock()

    assert exc_info.value.code == 403
    assert exc_info.value.message == _IDP_LOCK_MSG


def test_idp_guard_not_triggered_when_disabled():
    mock_config = MagicMock()
    mock_config.IDP_MANAGED_ENABLED = False
    with patch("codemie.rest_api.security.idp_lock.config", mock_config):
        require_no_idp_lock()  # must not raise
