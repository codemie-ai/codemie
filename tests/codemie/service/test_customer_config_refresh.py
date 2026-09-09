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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs import component_resolution
from codemie.service import customer_config_service
from codemie.service.customer_config_service import override_cache, refresh_overrides


@pytest.fixture(autouse=True)
def reset_state():
    override_cache.invalidate()
    yield
    override_cache.invalidate()


@pytest.mark.asyncio
async def test_refresh_overrides_publishes_the_snapshot():
    with patch.object(
        customer_config_service,
        "_load_overrides",
        AsyncMock(return_value={"features:webSearch": {"enabled": False}}),
    ):
        await refresh_overrides()

    assert component_resolution.current_snapshot() == {"features:webSearch": {"enabled": False}}


@pytest.mark.asyncio
async def test_refresh_overrides_reloads_a_stale_snapshot():
    with patch.object(
        customer_config_service,
        "_load_overrides",
        AsyncMock(return_value={"features:webSearch": {"enabled": True}}),
    ):
        await refresh_overrides()

    second = AsyncMock(return_value={"features:webSearch": {"enabled": False}})
    with patch.object(customer_config_service, "_load_overrides", second):
        await refresh_overrides()

    second.assert_awaited_once()
    assert component_resolution.current_snapshot() == {"features:webSearch": {"enabled": False}}


@pytest.mark.asyncio
async def test_refresh_overrides_keeps_the_last_good_snapshot_when_the_source_fails():
    with patch.object(
        customer_config_service,
        "_load_overrides",
        AsyncMock(return_value={"features:webSearch": {"enabled": False}}),
    ):
        await refresh_overrides()

    with patch.object(customer_config_service, "_load_overrides", AsyncMock(side_effect=RuntimeError("db down"))):
        await refresh_overrides()

    assert component_resolution.current_snapshot() == {"features:webSearch": {"enabled": False}}


@pytest.mark.asyncio
async def test_a_save_leaves_synchronous_readers_on_the_stored_value_not_on_yaml():
    """After an admin save the pod must not fall back to YAML until the next refresh tick.

    The snapshot is the only source synchronous readers have, so dropping it on write would
    re-enable every overridden feature for up to a full TTL on the very pod that served it.
    """
    actor = MagicMock()
    actor.id = "admin-1"
    stored = {"features:webSearch": {"enabled": False}}

    with (
        patch.object(customer_config_service.DynamicConfigService, "aget_by_key", AsyncMock(return_value=None)),
        patch.object(customer_config_service.DynamicConfigService, "aset", AsyncMock()),
        patch.object(customer_config_service, "_load_overrides", AsyncMock(return_value=stored)),
        patch.object(customer_config_service, "_audit", AsyncMock()),
    ):
        await customer_config_service.save_setting("features:webSearch", {"enabled": False}, actor)

    assert (
        component_resolution.current_snapshot() == stored
    ), "a save must leave the writing pod holding the stored overrides, not an empty snapshot"


@pytest.mark.asyncio
async def test_reset_returns_synchronous_readers_to_yaml():
    """Deleting the row must put the component back under YAML control for the backend too."""
    actor = MagicMock()
    actor.id = "admin-1"

    with patch.object(
        customer_config_service,
        "_load_overrides",
        AsyncMock(return_value={"features:webSearch": {"enabled": False}}),
    ):
        await refresh_overrides()

    assert component_resolution.current_snapshot() == {"features:webSearch": {"enabled": False}}

    with (
        patch.object(customer_config_service.DynamicConfigService, "adelete", AsyncMock(return_value=True)),
        patch.object(customer_config_service, "_read_stored_settings", AsyncMock(return_value={"enabled": False})),
        patch.object(customer_config_service, "_load_overrides", AsyncMock(return_value={})),
        patch.object(customer_config_service, "_audit", AsyncMock()),
    ):
        await customer_config_service.reset_setting("features:webSearch", actor)

    assert component_resolution.current_snapshot() == {}
