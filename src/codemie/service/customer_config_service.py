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

"""Business layer over customer configuration.

Resolves each component from two sources: a dynamic-config row wins over the YAML
default, field by field for the fields its declaration exposes. Consumers keep calling
``GET /v1/config`` and never talk to dynamic configuration themselves.
"""

from __future__ import annotations

import html
import json
import re
import time
from typing import Any

import bleach

from codemie.clients.postgres import get_async_session
from codemie.configs.config import config
from codemie.configs import component_resolution
from codemie.configs.component_resolution import Overrides
from codemie.configs.customer_config import CONFIG_IDS, Component, ComponentSetting, customer_config
from codemie.configs.logger import logger
from codemie.rest_api.models.dynamic_config import ConfigValueType
from codemie.rest_api.security.user import User
from codemie.service.activity.activity_models import (
    ActivityDomain,
    ActivityEntityType,
    ActivityEventCreate,
    CustomerConfigEvent,
)
from codemie.service.activity.activity_repository import activity_event_repository
from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.customer_config_declarations import (
    DECLARATIONS,
    KEY_PREFIX,
    FieldDeclaration,
    FieldType,
    Markup,
    SettingDeclaration,
    by_component_id,
    by_key,
)
from codemie.service.dynamic_config_service import DynamicConfigService


class OverrideCache:
    """Process-local cache of customer-config overrides with a bounded staleness window.

    The pod that performs a write invalidates its own copy immediately; every other pod
    picks the change up within the TTL. When the database is unreachable the last known
    good snapshot is served, and if there is none the caller falls back to YAML.
    """

    def __init__(self, ttl_seconds: int):
        self.ttl_seconds = ttl_seconds
        self._loaded = False
        self._expires_at: float = 0.0

    def invalidate(self) -> None:
        """Drop the snapshot entirely, leaving readers on YAML until the next load.

        The write path uses ``refresh_overrides`` instead: synchronous readers have no
        reload path of their own, so discarding their only source would revert every
        override to YAML for up to a TTL on the very pod that served the write.
        """
        self._loaded = False
        self._expires_at = 0.0
        component_resolution.clear_snapshot()

    def expire_now(self) -> None:
        """Mark the snapshot stale while keeping it as the degradation fallback."""
        self._expires_at = 0.0

    async def get(self) -> Overrides:
        if self._loaded and time.monotonic() < self._expires_at:
            return component_resolution.current_snapshot()

        try:
            overrides = await _load_overrides()
        except Exception as error:
            if self._loaded:
                logger.warning(f"Failed to load customer config overrides, serving last snapshot. {error=}")
                return component_resolution.current_snapshot()
            logger.warning(f"Failed to load customer config overrides, falling back to YAML. {error=}")
            return {}

        component_resolution.publish_snapshot(overrides)
        self._loaded = True
        self._expires_at = time.monotonic() + self.ttl_seconds
        return overrides


override_cache = OverrideCache(ttl_seconds=config.CUSTOMER_CONFIG_CACHE_TTL_SECONDS)


async def refresh_overrides() -> None:
    """Reload the override snapshot, keeping the last good one if the source is unreachable.

    ``expire_now`` marks the snapshot stale without dropping it, so a failed reload degrades
    to the last known good values rather than to YAML.
    """
    override_cache.expire_now()
    await override_cache.get()


async def _load_overrides() -> Overrides:
    rows = await DynamicConfigService.alist_by_key_prefix(KEY_PREFIX)

    overrides: Overrides = {}
    for row in rows:
        declaration = by_key(row.key)
        if declaration is None:
            logger.warning(f"Ignoring override for an undeclared customer config key: {row.key=}")
            continue

        parsed = _parse_override(row.key, row.value, declaration)
        if parsed is not None:
            overrides[declaration.component_id] = parsed

    return overrides


def _parse_override(key: str, raw_value: str, declaration: SettingDeclaration) -> dict | None:
    try:
        value = json.loads(raw_value)
    except (TypeError, ValueError) as error:
        logger.warning(f"Ignoring unparseable customer config override. {key=}, {error=}")
        return None

    if not isinstance(value, dict):
        logger.warning(f"Ignoring customer config override that is not an object. {key=}")
        return None

    return {name: value[name] for name in declaration.field_names if name in value}


async def resolve_components() -> list[Component]:
    """Return the enabled components with overrides applied before the enabled filter."""
    await override_cache.get()

    resolved = component_resolution.resolve_all(
        customer_config.components,
        customer_config.get_runtime_components(),
        set(CONFIG_IDS.values()),
    )
    return [component for component in resolved if component.settings.enabled]


_INVALID_VALUE_MESSAGE = "Invalid configuration value"

_DANGEROUS_LINK_SCHEMES = re.compile(r"\]\(\s*(?:javascript|data|vbscript)\s*:", re.IGNORECASE)

# bleach strips tags but keeps their text, which would leave script bodies behind as plain text
_SCRIPTING_BLOCKS = re.compile(r"<\s*(script|style|iframe)\b[^>]*>.*?<\s*/\s*\1\s*>", re.IGNORECASE | re.DOTALL)

_EXPECTED_TYPES: dict[FieldType, type | tuple[type, ...]] = {
    FieldType.SWITCH: bool,
    FieldType.INPUT: str,
    FieldType.TEXTAREA: str,
}


def validate_and_sanitize(declaration: SettingDeclaration, payload: dict) -> dict:
    """Check a submitted settings payload against its declaration and clean markup.

    Only declared field names are accepted, which is what keeps undeclared YAML fields
    out of the database. Nothing is returned unless every field passes.
    """
    _reject_undeclared_fields(declaration, payload)

    return {field.name: _validate_field(field, payload) for field in declaration.fields}


def _reject_undeclared_fields(declaration: SettingDeclaration, payload: dict) -> None:
    undeclared = sorted(set(payload) - declaration.field_names)
    if undeclared:
        raise ExtendedHTTPException(
            code=400,
            message="Unknown configuration fields",
            details=f"Fields not declared for '{declaration.component_id}': {', '.join(undeclared)}",
        )


def _validate_field(field: FieldDeclaration, payload: dict) -> Any:
    if field.name not in payload:
        raise ExtendedHTTPException(
            code=400,
            message="Missing configuration field",
            details=f"Field '{field.name}' is required by the declaration",
        )

    value = payload[field.name]
    expected = _EXPECTED_TYPES[field.type]

    # bool is a subclass of int, so an explicit check keeps True out of text fields
    if not isinstance(value, expected) or (expected is str and isinstance(value, bool)):
        raise ExtendedHTTPException(
            code=400,
            message=_INVALID_VALUE_MESSAGE,
            details=f"Field '{field.name}' must be of type {expected.__name__}",
        )

    if isinstance(value, str):
        return _validate_text(field, value)

    return value


def _validate_text(field: FieldDeclaration, value: str) -> str:
    if field.required and not value.strip():
        raise ExtendedHTTPException(
            code=400,
            message="Missing configuration value",
            details=f"Field '{field.name}' cannot be empty",
        )

    if field.max_length is not None and len(value) > field.max_length:
        raise ExtendedHTTPException(
            code=400,
            message="Configuration value too long",
            details=f"Field '{field.name}' exceeds its limit of {field.max_length} characters",
        )

    # an empty optional value has nothing to match; requiredness is the check above
    if field.pattern is not None and value and not re.match(field.pattern, value):
        raise ExtendedHTTPException(
            code=400,
            message=_INVALID_VALUE_MESSAGE,
            details=field.pattern_message or f"Field '{field.name}' does not match {field.pattern}",
        )

    if field.markup is Markup.MARKDOWN:
        return _sanitize_markdown(field, value)

    return value


def _sanitize_markdown(field: FieldDeclaration, value: str) -> str:
    # Character references are decoded first: a browser decodes them inside an href, so
    # checking the raw spelling alone would let `java&#115;cript:` through unnoticed.
    canonical = html.unescape(value)

    if _DANGEROUS_LINK_SCHEMES.search(canonical):
        raise ExtendedHTTPException(
            code=400,
            message="Unsafe link in configuration value",
            details=f"Field '{field.name}' contains a link with a disallowed scheme",
        )

    without_scripting = _SCRIPTING_BLOCKS.sub("", canonical)
    stripped = bleach.clean(without_scripting, tags=[], attributes={}, strip=True)

    # bleach escapes bare &, < and >; the stored value is Markdown source, not HTML,
    # and escaping it would surface as `AT&amp;T` in the admin form and the tooltip
    return html.unescape(stripped)


async def save_setting(component_id: str, payload: dict, actor: User) -> dict:
    """Validate, sanitise and store an override for one declared component."""
    declaration = _require_declaration(component_id)
    settings = validate_and_sanitize(declaration, payload)

    previous = await _read_stored_settings(declaration)

    await DynamicConfigService.aset(
        key=declaration.key,
        value=json.dumps(settings),
        value_type=ConfigValueType.STRING,
        description=declaration.label,
        updated_by=actor.id,
    )
    await refresh_overrides()

    await _audit(CustomerConfigEvent.SETTING_UPDATED, declaration, actor, previous, settings)
    logger.info(f"Customer config setting updated: {component_id=}, actor={actor.id}")
    return settings


async def reset_setting(component_id: str, actor: User) -> None:
    """Drop the override for one declared component so its value comes from YAML again.

    The YAML value is never copied into the database, so a reset setting keeps following
    later deployments. Resetting a setting that has no override is a no-op.
    """
    declaration = _require_declaration(component_id)

    previous = await _read_stored_settings(declaration)
    deleted = await DynamicConfigService.adelete(declaration.key)

    if not deleted:
        logger.debug(f"Customer config setting had no override to reset: {component_id=}")
        return

    await refresh_overrides()

    await _audit(CustomerConfigEvent.SETTING_RESET, declaration, actor, previous, None)
    logger.info(f"Customer config setting reset: {component_id=}, actor={actor.id}")


def _require_declaration(component_id: str) -> SettingDeclaration:
    declaration = by_component_id(component_id)
    if declaration is None:
        raise ExtendedHTTPException(
            code=404,
            message="Setting not found",
            details=f"Component '{component_id}' is not declared as dynamic configuration",
        )
    return declaration


async def _read_stored_settings(declaration: SettingDeclaration) -> dict | None:
    row = await DynamicConfigService.aget_by_key(declaration.key)
    if row is None:
        return None
    return _parse_override(declaration.key, row.value, declaration)


async def _audit(
    event_type: str,
    declaration: SettingDeclaration,
    actor: User,
    old_value: dict | None,
    new_value: dict | None,
) -> None:
    event = ActivityEventCreate(
        domain=ActivityDomain.CUSTOMER_CONFIG,
        event_type=event_type,
        entity_type=ActivityEntityType.CUSTOMER_CONFIG_SETTING,
        entity_id=declaration.component_id,
        actor_id=actor.id,
        attributes={"key": declaration.key, "old_value": old_value, "new_value": new_value},
    )

    # The config write is already committed; an audit failure must not turn it into a 500
    try:
        async with get_async_session() as session:
            await activity_event_repository.async_insert(event, session)
            await session.commit()
    except Exception as error:
        logger.error(f"Failed to record customer config audit event. {event_type=}, {declaration.key=}, {error=}")


async def list_settings() -> list[dict]:
    """Declared settings with their resolved value and override marker, for the admin UI.

    Only declared fields are exposed: configuration that has not been migrated never
    reaches the frontend.
    """
    # Read through, not through the cache: the admin must see their own write immediately
    # even when the refresh lands on a pod that did not serve it
    overrides = await _load_overrides()
    yaml_settings = {component.id: component.settings for component in customer_config.components}

    settings = []
    for declaration in DECLARATIONS:
        defaults = yaml_settings.get(declaration.component_id)
        override = overrides.get(declaration.component_id)
        resolved = _resolved_value(declaration, defaults, override)

        settings.append(
            {
                "component_id": declaration.component_id,
                "label": declaration.label,
                "description": declaration.description,
                "overridden": override is not None,
                "value": resolved,
                "fields": [field.model_dump() for field in declaration.fields],
            }
        )

    return settings


def _resolved_value(
    declaration: SettingDeclaration,
    defaults: ComponentSetting | None,
    override: dict | None,
) -> dict:
    default_values = defaults.model_dump(exclude_none=True) if defaults is not None else {}
    merged = declaration.empty_value() | default_values | (override or {})
    return {name: merged[name] for name in declaration.field_names}
