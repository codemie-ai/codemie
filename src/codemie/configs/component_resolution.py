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

"""The single component resolution shared by the backend and ``GET /v1/config``.

Precedence is per field: a runtime-computed component wins outright, then the override for
each field it carries, then YAML. The snapshot is read synchronously with no I/O; only the
asynchronous side writes it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

Overrides = dict[str, dict]

_snapshot: Overrides = {}


class ComponentSetting(BaseModel):
    enabled: bool = Field()
    availableForExternal: bool = Field(default=True)
    name: str | None = Field(default=None)
    url: str | None = Field(default=None)
    created_by: str | None = Field(default=None)
    icon_url: str | None = Field(default=None)

    model_config = ConfigDict(extra="allow")


class Component(BaseModel):
    id: str = Field()
    settings: ComponentSetting

    @staticmethod
    @field_validator("id")
    def validate_id(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            raise ValueError("Component ID must be a non-empty string")
        return v


def publish_snapshot(overrides: Overrides) -> None:
    """Replace the override snapshot. Called only from the asynchronous side."""
    global _snapshot
    _snapshot = overrides


def current_snapshot() -> Overrides:
    return _snapshot


def clear_snapshot() -> None:
    publish_snapshot({})


def apply_override(component: Component, override: dict | None) -> Component:
    """Lay the override's fields over the YAML settings, leaving the rest alone.

    Fields the override does not carry stay on their YAML value, so they keep following
    deployments instead of freezing at the moment of the first save.
    """
    if not override:
        return component

    settings = component.settings.model_dump(exclude_none=True) | override
    return Component(id=component.id, settings=ComponentSetting(**settings))


def _from_override_alone(component_id: str, override: dict) -> Component:
    """Build a component the YAML omits but an override declares."""
    return Component(id=component_id, settings=ComponentSetting(**{"enabled": False, **override}))


def resolve(
    component_id: str,
    yaml_components: list[Component],
    runtime_components: list[Component],
    runtime_ids: set[str],
) -> Component | None:
    """Resolve one component: runtime-computed, then the override, then YAML."""
    if component_id in runtime_ids:
        return next((c for c in runtime_components if c.id == component_id), None)

    override = _snapshot.get(component_id)
    yaml_component = next((c for c in yaml_components if c.id == component_id), None)

    if yaml_component is not None:
        return apply_override(yaml_component, override)

    if override is None:
        return None

    return _from_override_alone(component_id, override)


def resolve_all(
    yaml_components: list[Component],
    runtime_components: list[Component],
    runtime_ids: set[str],
) -> list[Component]:
    """Every known component, resolved. Runtime-computed ones are appended unchanged."""
    yaml_ids = {c.id for c in yaml_components}

    resolved = [
        apply_override(component, _snapshot.get(component.id))
        for component in yaml_components
        if component.id not in runtime_ids
    ]
    resolved += [
        _from_override_alone(component_id, override)
        for component_id, override in _snapshot.items()
        if component_id not in yaml_ids and component_id not in runtime_ids
    ]
    return resolved + list(runtime_components)
