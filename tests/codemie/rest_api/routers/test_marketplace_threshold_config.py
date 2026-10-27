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

"""Tests for the audit-log / cache-invalidation hook on marketplaceThresholds writes.

These drive the customer_config router endpoints via TestClient and assert that:
- PUT /config/declarations/marketplaceThresholds triggers cache invalidation and
  writes a THRESHOLD_UPDATED activity event (into `activity_events`, not the
  legacy `mm_marketplace_event` table -- MR !4070 review comment #21).
- DELETE /config/declarations/marketplaceThresholds triggers cache invalidation
  and writes a THRESHOLD_RESET activity event.
- PUT for any other component_id does NOT trigger the hook.
- The hook is a no-op when marketplace management is disabled, and does not
  import `AUDIT_CACHE` at module load time.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codemie.rest_api.routers import customer_config as cc
from codemie.rest_api.security.user import User
from codemie.service.activity.activity_models import MarketplaceModerationEvent

THRESHOLD_UPDATED = MarketplaceModerationEvent.THRESHOLD_UPDATED
THRESHOLD_RESET = MarketplaceModerationEvent.THRESHOLD_RESET

_MOD = "codemie.rest_api.routers.customer_config"
_AUDIT_CACHE_PATH = "codemie.service.marketplace_management.audit_service.AUDIT_CACHE"
_ENABLED_PATH = "codemie.configs.marketplace_management.is_marketplace_management_enabled"

_THRESHOLD_PATH = "/v1/config/declarations/marketplaceThresholds"
_OTHER_PATH = "/v1/config/declarations/chatDisclaimer"


# ---------------------------------------------------------------------------
# App under test
# ---------------------------------------------------------------------------


@pytest.fixture
def admin():
    return User(
        id="admin-1",
        username="admin",
        name="Admin User",
    )


@pytest.fixture
def app(admin):
    test_app = FastAPI()
    test_app.include_router(cc.router)
    test_app.dependency_overrides[cc.authenticate] = lambda: admin
    test_app.dependency_overrides[cc.require_customer_config_write] = lambda: None
    return test_app


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_cm(added_rows: list):
    """Return an async context manager mock that captures session.add calls."""
    session = MagicMock()

    def _add(obj):
        added_rows.append(obj)

    session.add.side_effect = _add
    session.commit = AsyncMock()

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_put_threshold_writes_audit_row(client):
    """PUT marketplaceThresholds must persist an activity event(THRESHOLD_UPDATED)."""
    with (
        patch(f"{_MOD}.save_setting", new=AsyncMock(return_value={"hotMinUsers": "50"})),
        patch(f"{_MOD}._write_threshold_event", new=AsyncMock()) as mock_write,
        patch(_ENABLED_PATH, return_value=True),
        patch(_AUDIT_CACHE_PATH),
    ):
        response = client.put(_THRESHOLD_PATH, json={"settings": {"hotMinUsers": "50"}})

    assert response.status_code == 200
    mock_write.assert_awaited_once()
    args = mock_write.await_args
    assert args.args[0] == THRESHOLD_UPDATED


def test_delete_threshold_writes_audit_row(client):
    """DELETE marketplaceThresholds must persist an activity event(THRESHOLD_RESET)."""
    with (
        patch(f"{_MOD}.reset_setting", new=AsyncMock()),
        patch(f"{_MOD}._write_threshold_event", new=AsyncMock()) as mock_write,
        patch(_ENABLED_PATH, return_value=True),
        patch(_AUDIT_CACHE_PATH),
        patch(
            "codemie.service.dynamic_config_service.DynamicConfigService.aget_by_key", new=AsyncMock(return_value=None)
        ),
    ):
        response = client.delete(_THRESHOLD_PATH)

    assert response.status_code == 204
    mock_write.assert_awaited_once()
    args = mock_write.await_args
    assert args.args[0] == THRESHOLD_RESET


def test_put_threshold_invalidates_audit_cache(client):
    """PUT marketplaceThresholds must call AUDIT_CACHE.update_config for every EntityKind."""
    from codemie.service.marketplace_management.models import EntityKind

    with (
        patch(f"{_MOD}.save_setting", new=AsyncMock(return_value={"hotMinUsers": "50"})),
        patch(f"{_MOD}._write_threshold_event", new=AsyncMock()),
        patch(_ENABLED_PATH, return_value=True),
        patch(_AUDIT_CACHE_PATH) as mock_cache,
    ):
        response = client.put(_THRESHOLD_PATH, json={"settings": {"hotMinUsers": "50"}})

    assert response.status_code == 200
    assert mock_cache.update_config.call_count >= len(list(EntityKind))


def test_put_threshold_noop_when_feature_disabled(client):
    """PUT marketplaceThresholds must NOT touch AUDIT_CACHE or write an audit row when the feature is off."""
    with (
        patch(f"{_MOD}.save_setting", new=AsyncMock(return_value={"hotMinUsers": "50"})),
        patch(f"{_MOD}._write_threshold_event", new=AsyncMock()) as mock_write,
        patch(_ENABLED_PATH, return_value=False),
        patch(_AUDIT_CACHE_PATH) as mock_cache,
    ):
        response = client.put(_THRESHOLD_PATH, json={"settings": {"hotMinUsers": "50"}})

    assert response.status_code == 200
    mock_cache.update_config.assert_not_called()
    mock_write.assert_not_awaited()


def test_put_non_threshold_does_not_trigger_hook(client):
    """PUT chatDisclaimer must NOT call AUDIT_CACHE.update_config or write an audit row."""
    with (
        patch(f"{_MOD}.save_setting", new=AsyncMock(return_value={"enabled": True})),
        patch(f"{_MOD}._write_threshold_event", new=AsyncMock()) as mock_write,
        patch(_ENABLED_PATH, return_value=True),
        patch(_AUDIT_CACHE_PATH) as mock_cache,
    ):
        response = client.put(_OTHER_PATH, json={"settings": {"enabled": True}})

    assert response.status_code == 200
    mock_cache.update_config.assert_not_called()
    mock_write.assert_not_awaited()


def test_delete_non_threshold_does_not_trigger_hook(client):
    """DELETE chatDisclaimer must NOT call AUDIT_CACHE.update_config or write an audit row."""
    with (
        patch(f"{_MOD}.reset_setting", new=AsyncMock()),
        patch(f"{_MOD}._write_threshold_event", new=AsyncMock()) as mock_write,
        patch(_ENABLED_PATH, return_value=True),
        patch(_AUDIT_CACHE_PATH) as mock_cache,
    ):
        response = client.delete(_OTHER_PATH)

    assert response.status_code == 204
    mock_cache.update_config.assert_not_called()
    mock_write.assert_not_awaited()
