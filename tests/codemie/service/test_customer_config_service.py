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

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs.customer_config import Component, ComponentSetting
from codemie.service import customer_config_service
from codemie.service.customer_config_service import (
    OverrideCache,
    list_settings,
    resolve_components,
)


def _row(key: str, value: dict) -> MagicMock:
    row = MagicMock()
    row.key = key
    row.value = json.dumps(value)
    return row


@pytest.fixture(autouse=True)
def reset_cache():
    customer_config_service.override_cache.invalidate()
    yield
    customer_config_service.override_cache.invalidate()


@pytest.fixture
def yaml_components():
    return [
        Component(id="chatDisclaimer", settings=ComponentSetting(enabled=False, text="")),
        Component(
            id="features:webSearch",
            settings=ComponentSetting(enabled=True, name="Web search", description="From YAML"),
        ),
    ]


@pytest.fixture
def patched_yaml(yaml_components):
    with patch.object(customer_config_service, "customer_config") as mock_config:
        mock_config.components = yaml_components
        mock_config.get_runtime_components.return_value = []
        yield mock_config


def _patch_rows(rows):
    return patch.object(
        customer_config_service.DynamicConfigService,
        "alist_by_key_prefix",
        AsyncMock(return_value=rows),
    )


def _find(components, component_id):
    return next((c for c in components if c.id == component_id), None)


@pytest.mark.asyncio
async def test_empty_database_leaves_yaml_behaviour_untouched(patched_yaml):
    with _patch_rows([]):
        components = await resolve_components()

    assert _find(components, "chatDisclaimer") is None
    assert _find(components, "features:webSearch").settings.name == "Web search"


@pytest.mark.asyncio
async def test_override_merges_before_enabled_filter(patched_yaml):
    rows = [_row("CUSTOMER_CONFIG__CHAT_DISCLAIMER", {"enabled": True, "text": "Be careful"})]

    with _patch_rows(rows):
        components = await resolve_components()

    disclaimer = _find(components, "chatDisclaimer")
    assert disclaimer is not None
    assert disclaimer.settings.text == "Be careful"


@pytest.mark.asyncio
async def test_override_can_disable_a_component_enabled_in_yaml(patched_yaml, yaml_components):
    yaml_components[0].settings.enabled = True
    rows = [_row("CUSTOMER_CONFIG__CHAT_DISCLAIMER", {"enabled": False, "text": "hidden"})]

    with _patch_rows(rows):
        components = await resolve_components()

    assert _find(components, "chatDisclaimer") is None


@pytest.mark.asyncio
async def test_undeclared_yaml_fields_keep_following_deployments(patched_yaml, yaml_components):
    """An override must not freeze fields the declaration does not expose."""
    rows = [_row("CUSTOMER_CONFIG__CHAT_DISCLAIMER", {"enabled": True, "text": "Be careful"})]
    yaml_components[0].settings.name = "Renamed by a later deployment"

    with _patch_rows(rows):
        components = await resolve_components()

    assert _find(components, "chatDisclaimer").settings.name == "Renamed by a later deployment"


@pytest.mark.asyncio
async def test_override_for_an_undeclared_component_is_ignored(patched_yaml):
    rows = [_row("CUSTOMER_CONFIG__FEATURES__DYNAMIC_CODE_INTERPRETER", {"enabled": True})]

    with _patch_rows(rows):
        components = await resolve_components()

    assert _find(components, "features:dynamicCodeInterpreter") is None
    assert _find(components, "features:webSearch") is not None


@pytest.mark.asyncio
async def test_unparseable_override_falls_back_to_yaml(patched_yaml):
    row = MagicMock()
    row.key = "CUSTOMER_CONFIG__CHAT_DISCLAIMER"
    row.value = "{not json"

    with _patch_rows([row]):
        components = await resolve_components()

    assert _find(components, "chatDisclaimer") is None


@pytest.mark.asyncio
async def test_database_failure_degrades_to_last_known_good(patched_yaml):
    rows = [_row("CUSTOMER_CONFIG__CHAT_DISCLAIMER", {"enabled": True, "text": "cached"})]

    with _patch_rows(rows):
        await resolve_components()

    customer_config_service.override_cache.expire_now()
    failing = patch.object(
        customer_config_service.DynamicConfigService,
        "alist_by_key_prefix",
        AsyncMock(side_effect=RuntimeError("database is down")),
    )

    with failing:
        components = await resolve_components()

    assert _find(components, "chatDisclaimer").settings.text == "cached"


@pytest.mark.asyncio
async def test_database_failure_without_a_snapshot_degrades_to_yaml(patched_yaml):
    failing = patch.object(
        customer_config_service.DynamicConfigService,
        "alist_by_key_prefix",
        AsyncMock(side_effect=RuntimeError("database is down")),
    )

    with failing:
        components = await resolve_components()

    assert _find(components, "chatDisclaimer") is None
    assert _find(components, "features:webSearch") is not None


@pytest.mark.asyncio
async def test_cache_serves_repeated_reads_without_hitting_the_database(patched_yaml):
    rows = [_row("CUSTOMER_CONFIG__CHAT_DISCLAIMER", {"enabled": True, "text": "cached"})]
    loader = AsyncMock(return_value=rows)

    with patch.object(customer_config_service.DynamicConfigService, "alist_by_key_prefix", loader):
        await resolve_components()
        await resolve_components()

    loader.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalidation_makes_the_writing_pod_read_fresh(patched_yaml):
    rows = [_row("CUSTOMER_CONFIG__CHAT_DISCLAIMER", {"enabled": True, "text": "first"})]
    loader = AsyncMock(return_value=rows)

    with patch.object(customer_config_service.DynamicConfigService, "alist_by_key_prefix", loader):
        await resolve_components()
        customer_config_service.override_cache.invalidate()
        await resolve_components()

    assert loader.await_count == 2


@pytest.mark.asyncio
async def test_cache_reloads_after_ttl_expiry(patched_yaml):
    rows = [_row("CUSTOMER_CONFIG__CHAT_DISCLAIMER", {"enabled": True, "text": "first"})]
    loader = AsyncMock(return_value=rows)

    with patch.object(customer_config_service.DynamicConfigService, "alist_by_key_prefix", loader):
        await resolve_components()
        customer_config_service.override_cache.expire_now()
        await resolve_components()

    assert loader.await_count == 2


@pytest.mark.asyncio
async def test_runtime_components_are_appended_unchanged(patched_yaml):
    patched_yaml.get_runtime_components.return_value = [
        Component(id="features:enterpriseEdition", settings=ComponentSetting(enabled=True)),
        Component(id="features:userManagement", settings=ComponentSetting(enabled=False)),
    ]

    with _patch_rows([]):
        components = await resolve_components()

    assert _find(components, "features:enterpriseEdition") is not None
    assert _find(components, "features:userManagement") is None


def test_cache_ttl_comes_from_configuration():
    cache = OverrideCache(ttl_seconds=5)

    assert cache.ttl_seconds == 5


def test_chat_disclaimer_default_is_declared_disabled_and_empty():
    """The deployment default must exist in YAML, so a reset has something to fall back to."""
    from codemie.configs.customer_config import customer_config as real_config

    component = next((c for c in real_config.components if c.id == "chatDisclaimer"), None)

    assert component is not None
    assert component.settings.enabled is False
    assert getattr(component.settings, "text", None) == ""


# --- Regressions found in code review (CR-003, CR-004) ---


@pytest.mark.asyncio
async def test_a_declared_component_missing_from_yaml_still_receives_its_override(patched_yaml, yaml_components):
    """A customer's YAML is its own file and may predate a declaration."""
    yaml_components.pop(0)
    rows = [_row("CUSTOMER_CONFIG__CHAT_DISCLAIMER", {"enabled": True, "text": "Be careful"})]

    with _patch_rows(rows):
        components = await resolve_components()

    disclaimer = _find(components, "chatDisclaimer")
    assert disclaimer is not None
    assert disclaimer.settings.text == "Be careful"


@pytest.mark.asyncio
async def test_a_declared_component_missing_from_yaml_is_absent_without_an_override(patched_yaml, yaml_components):
    yaml_components.pop(0)

    with _patch_rows([]):
        components = await resolve_components()

    assert _find(components, "chatDisclaimer") is None


@pytest.mark.asyncio
async def test_admin_listing_never_reports_a_null_the_form_cannot_post_back(patched_yaml, yaml_components):
    yaml_components.pop(0)

    with _patch_rows([]):
        settings = await list_settings()

    value = next(s for s in settings if s["component_id"] == "chatDisclaimer")["value"]
    assert value == {"enabled": False, "text": ""}


@pytest.mark.asyncio
async def test_admin_listing_reads_through_so_the_admin_sees_their_own_write(patched_yaml):
    """The refresh after a save may land on a pod whose cache predates the write."""
    stale = AsyncMock(return_value=[])
    with patch.object(customer_config_service.DynamicConfigService, "alist_by_key_prefix", stale):
        await resolve_components()

    fresh = [_row("CUSTOMER_CONFIG__CHAT_DISCLAIMER", {"enabled": True, "text": "just saved"})]
    with patch.object(
        customer_config_service.DynamicConfigService, "alist_by_key_prefix", AsyncMock(return_value=fresh)
    ):
        settings = await list_settings()

    disclaimer = next(s for s in settings if s["component_id"] == "chatDisclaimer")
    assert disclaimer["overridden"] is True
    assert disclaimer["value"]["text"] == "just saved"


def test_release_notes_recent_count_default_is_declared_enabled_with_a_value():
    """The reference deployment config carries a tunable default value; enabled: true is
    structural (ComponentSetting.enabled has no default), not a feature toggle here."""
    from codemie.configs.customer_config import customer_config as real_config

    component = next((c for c in real_config.components if c.id == "releaseNotesRecentCount"), None)

    assert component is not None
    assert component.settings.enabled is True
    assert getattr(component.settings, "recentReleaseCount", None) == "10"


@pytest.fixture
def yaml_with_release_notes(yaml_components):
    extended = [
        *yaml_components,
        Component(
            id="releaseNotesRecentCount",
            settings=ComponentSetting(enabled=True, recentReleaseCount="10"),
        ),
    ]
    with patch.object(customer_config_service, "customer_config") as mock_config:
        mock_config.components = extended
        mock_config.get_runtime_components.return_value = []
        yield mock_config


@pytest.mark.asyncio
async def test_a_switchless_declaration_does_not_disable_its_yaml_component(yaml_with_release_notes):
    """releaseNotesRecentCount declares no `enabled` field, so saving it must not turn it off."""
    rows = [_row("CUSTOMER_CONFIG__RELEASE_NOTES_RECENT_COUNT", {"recentReleaseCount": "25"})]

    with _patch_rows(rows):
        components = await resolve_components()

    component = _find(components, "releaseNotesRecentCount")
    assert component is not None, "the component must not vanish from /v1/config after a save"
    assert component.settings.enabled is True
    assert component.settings.recentReleaseCount == "25"


@pytest.fixture
def yaml_with_disclaimer_text():
    components = [
        Component(id="chatDisclaimer", settings=ComponentSetting(enabled=True, text="From YAML")),
    ]
    with patch.object(customer_config_service, "customer_config") as mock_config:
        mock_config.components = components
        mock_config.get_runtime_components.return_value = []
        yield mock_config


@pytest.mark.asyncio
async def test_a_row_missing_a_declared_field_keeps_the_yaml_value(yaml_with_disclaimer_text):
    """A row written before a declaration gained a field must not blank that field."""
    rows = [_row("CUSTOMER_CONFIG__CHAT_DISCLAIMER", {"enabled": True})]

    with _patch_rows(rows):
        components = await resolve_components()

    disclaimer = _find(components, "chatDisclaimer")
    assert disclaimer is not None
    assert disclaimer.settings.text == "From YAML"
