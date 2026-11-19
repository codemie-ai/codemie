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

"""Tests for IDP_MANAGED_ENABLED guard on user management router mutation endpoints.

Verifies that:
- require_no_idp_lock raises HTTP 403 when IDP_MANAGED_ENABLED=True
- require_no_idp_lock does not raise when IDP_MANAGED_ENABLED=False
- Handlers that change identity data (create/deactivate user, project and knowledge-base access)
  have require_no_idp_lock wired as a dependency
- Handlers for platform-managed data (roles, user type, budgets) and GET endpoints do not
"""

import inspect
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.idp_lock import require_no_idp_lock
from codemie.rest_api.routers.user_management_router import (
    add_knowledge_base_access,
    add_project_access,
    admin_change_password,
    bulk_set_user_budgets,
    create_user,
    deactivate_user,
    list_users,
    remove_knowledge_base_access,
    remove_project_access,
    reset_user_budget_spending,
    set_user_budgets,
    update_project_access,
    update_user,
)

_IDP_LOCK_MSG = "User management is controlled by the identity provider."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dependency_functions(handler):
    """Return the set of dependency callables wired into a FastAPI handler."""
    sig = inspect.signature(handler)
    return {p.default.dependency for p in sig.parameters.values() if hasattr(p.default, "dependency")}


def _assert_idp_lock_raises_403():
    """Shared assertion: require_no_idp_lock raises HTTP 403 when IDP_MANAGED_ENABLED=True."""
    mock_config = MagicMock()
    mock_config.IDP_MANAGED_ENABLED = True
    with patch("codemie.rest_api.security.idp_lock.config", mock_config):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            require_no_idp_lock()
    assert exc_info.value.code == 403
    assert exc_info.value.message == _IDP_LOCK_MSG


@pytest.mark.parametrize(
    "handler",
    [
        pytest.param(admin_change_password, id="admin_change_password"),
        pytest.param(update_project_access, id="update_project_access"),
    ],
)
def test_other_identity_owned_mutations_are_wired_to_the_idp_lock(handler):
    assert require_no_idp_lock in _dependency_functions(handler)


# ---------------------------------------------------------------------------
# Tests 1-7: each mutation handler is wired to require_no_idp_lock
# and the guard raises 403 when IDP_MANAGED_ENABLED=True
# ---------------------------------------------------------------------------


def test_create_user_returns_403_when_idp_managed_enabled():
    """create_user has the IDP guard wired; guard raises 403 when IDP managed."""
    assert require_no_idp_lock in _dependency_functions(create_user)
    _assert_idp_lock_raises_403()


def test_deactivate_user_returns_403_when_idp_managed_enabled():
    """deactivate_user has the IDP guard wired; guard raises 403 when IDP managed."""
    assert require_no_idp_lock in _dependency_functions(deactivate_user)
    _assert_idp_lock_raises_403()


def test_add_project_access_returns_403_when_idp_managed_enabled():
    """add_project_access has the IDP guard wired; guard raises 403 when IDP managed."""
    assert require_no_idp_lock in _dependency_functions(add_project_access)
    _assert_idp_lock_raises_403()


def test_remove_project_access_returns_403_when_idp_managed_enabled():
    """remove_project_access has the IDP guard wired; guard raises 403 when IDP managed."""
    assert require_no_idp_lock in _dependency_functions(remove_project_access)
    _assert_idp_lock_raises_403()


def test_add_knowledge_base_access_returns_403_when_idp_managed_enabled():
    """add_knowledge_base_access has the IDP guard wired; guard raises 403 when IDP managed."""
    assert require_no_idp_lock in _dependency_functions(add_knowledge_base_access)
    _assert_idp_lock_raises_403()


def test_remove_knowledge_base_access_returns_403_when_idp_managed_enabled():
    """remove_knowledge_base_access has the IDP guard wired; guard raises 403 when IDP managed."""
    assert require_no_idp_lock in _dependency_functions(remove_knowledge_base_access)
    _assert_idp_lock_raises_403()


# ---------------------------------------------------------------------------
# Test 8: guard is a no-op when IDP_MANAGED_ENABLED=False
# ---------------------------------------------------------------------------


def test_guard_not_triggered_when_idp_managed_disabled():
    """require_no_idp_lock must not raise when IDP_MANAGED_ENABLED=False (default)."""
    mock_config = MagicMock()
    mock_config.IDP_MANAGED_ENABLED = False
    with patch("codemie.rest_api.security.idp_lock.config", mock_config):
        # Must not raise
        require_no_idp_lock()


# ---------------------------------------------------------------------------
# Platform-managed mutations stay available in IDP managed mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handler",
    [
        pytest.param(update_user, id="update_user"),
        pytest.param(bulk_set_user_budgets, id="bulk_set_user_budgets"),
        pytest.param(reset_user_budget_spending, id="reset_user_budget_spending"),
        pytest.param(set_user_budgets, id="set_user_budgets"),
    ],
)
def test_platform_managed_handler_has_no_idp_guard(handler):
    """Platform roles, user type and budgets are not owned by the IdP, so maintainers must keep access."""
    assert require_no_idp_lock not in _dependency_functions(handler)


# ---------------------------------------------------------------------------
# Test 13: GET endpoint is never guarded
# ---------------------------------------------------------------------------


def test_get_endpoint_not_guarded_when_idp_managed_enabled():
    """list_users (GET) must not have require_no_idp_lock as a dependency.

    When ENABLE_USER_MANAGEMENT=False it raises ExtendedHTTPException (400),
    not HTTPException(403), proving the IDP guard is absent.
    """
    # Wiring: GET handler must NOT have the IDP lock
    assert require_no_idp_lock not in _dependency_functions(list_users)

    # Behaviour: calling list_users when ENABLE_USER_MANAGEMENT=False raises a 400
    # ExtendedHTTPException — never an HTTPException(403) from the IDP guard
    mock_config = MagicMock()
    mock_config.IDP_MANAGED_ENABLED = True
    mock_config.ENABLE_USER_MANAGEMENT = False
    with patch("codemie.rest_api.security.idp_lock.config", mock_config):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            list_users(page=0, per_page=20, search=None, filters=None, user=MagicMock())
        assert exc_info.value.code == 400
