# EPMCDME-14705 — Shared Component Resolver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One resolution function, shared by the synchronous backend flag path and the `GET /v1/config` path, so dynamic-configuration overrides change backend behaviour and not only what the frontend is served.

**Architecture:** A new module `configs/component_resolution.py` owns the override snapshot, the component models, and the single precedence rule (runtime-computed → override → YAML, per field). `customer_config` supplies its YAML and runtime components to that rule and re-exports the models, so existing imports keep working. `customer_config_service` populates the snapshot and resolves `/v1/config` through the same rule, reducing `resolve_components()` to two lines. The lifespan warms the snapshot before any request is served and refreshes it periodically.

**Tech Stack:** Python 3, FastAPI, pydantic v2, poetry, pytest + pytest-asyncio (strict mode).

**Spec:** `docs/superpowers/tasks/2026-09-03-epmcdme-14705-shared-component-resolver/spec.md`

## Global Constraints

- `src/codemie/configs/` imports nothing from `src/codemie/service/`, `repository/` or `rest_api/`. The new module obeys this.
- Synchronous resolution performs **zero I/O**. There is no sync `list_by_key_prefix`, and the repo's sync→async bridge is documented as deadlock-prone.
- All ~21 existing flag call sites stay byte-identical. 19 are synchronous, so no signature may become `async`.
- `from codemie.configs.customer_config import Component, ComponentSetting, CONFIG_IDS` must keep working — five modules rely on it, three of them tests.
- `customer_config_service.override_cache` keeps its name and module: tests patch it by name.
- The missing-key default stays `False`. Only the wrong docstring line at `configs/customer_config.py:271` changes.
- `tests/codemie/service/test_customer_config_service.py` (309 lines) must pass **unchanged**.
- Style: `X | None` never `Optional`, `list[str]` / `dict[str, str]`, `from __future__ import annotations` in new modules, Apache licence header on every new file (`make license-check`), f-string logging with `{var=}`.
- Tests mirror `src/` under `tests/codemie/...`; pytest-asyncio is **strict**, so every async test needs an explicit `@pytest.mark.asyncio`.

---

### Task 1: The resolver module

**Files:**
- Create: `src/codemie/configs/component_resolution.py`
- Test: `tests/codemie/configs/test_component_resolution.py`

**Interfaces:**
- Consumes: nothing (bottom of the stack).
- Produces:
  - `Overrides = dict[str, dict]`
  - `class ComponentSetting(BaseModel)` — moved verbatim from `customer_config.py:33-41`
  - `class Component(BaseModel)` — moved verbatim from `customer_config.py:63-72`
  - `publish_snapshot(overrides: Overrides) -> None`
  - `current_snapshot() -> Overrides`
  - `clear_snapshot() -> None`
  - `apply_override(component: Component, override: dict | None) -> Component`
  - `resolve(component_id: str, yaml_components: list[Component], runtime_components: list[Component], runtime_ids: set[str]) -> Component | None`
  - `resolve_all(yaml_components: list[Component], runtime_components: list[Component], runtime_ids: set[str]) -> list[Component]`

**Test-first: yes** — `test_override_field_wins_over_yaml` fails with `ModuleNotFoundError: codemie.configs.component_resolution`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/codemie/configs/test_component_resolution.py
import pytest

from codemie.configs.component_resolution import (
    Component,
    ComponentSetting,
    clear_snapshot,
    publish_snapshot,
    resolve,
    resolve_all,
)

RUNTIME_IDS = {"features:enterpriseEdition"}


@pytest.fixture(autouse=True)
def reset_snapshot():
    clear_snapshot()
    yield
    clear_snapshot()


def _yaml():
    return [
        Component(id="features:webSearch", settings=ComponentSetting(enabled=True, name="Web Search")),
        Component(id="features:codeInterpreter", settings=ComponentSetting(enabled=False)),
    ]


def _runtime():
    return [Component(id="features:enterpriseEdition", settings=ComponentSetting(enabled=True))]


def test_yaml_value_is_used_when_no_override():
    component = resolve("features:webSearch", _yaml(), _runtime(), RUNTIME_IDS)
    assert component is not None
    assert component.settings.enabled is True


def test_override_field_wins_over_yaml():
    publish_snapshot({"features:webSearch": {"enabled": False}})
    component = resolve("features:webSearch", _yaml(), _runtime(), RUNTIME_IDS)
    assert component.settings.enabled is False


def test_fields_absent_from_the_override_keep_coming_from_yaml():
    publish_snapshot({"features:webSearch": {"enabled": False}})
    component = resolve("features:webSearch", _yaml(), _runtime(), RUNTIME_IDS)
    assert component.settings.name == "Web Search"


def test_runtime_component_ignores_the_override():
    publish_snapshot({"features:enterpriseEdition": {"enabled": False}})
    component = resolve("features:enterpriseEdition", _yaml(), _runtime(), RUNTIME_IDS)
    assert component.settings.enabled is True


def test_unknown_component_resolves_to_none():
    assert resolve("features:nothingHere", _yaml(), _runtime(), RUNTIME_IDS) is None


def test_component_absent_from_yaml_resolves_from_the_snapshot_alone():
    publish_snapshot({"chatDisclaimer": {"enabled": True, "text": "hi"}})
    component = resolve("chatDisclaimer", _yaml(), _runtime(), RUNTIME_IDS)
    assert component is not None
    assert component.settings.enabled is True
    assert component.settings.text == "hi"


def test_resolve_all_covers_yaml_runtime_and_snapshot_only_components():
    publish_snapshot({"chatDisclaimer": {"enabled": True}})
    ids = {c.id for c in resolve_all(_yaml(), _runtime(), RUNTIME_IDS)}
    assert ids == {
        "features:webSearch",
        "features:codeInterpreter",
        "features:enterpriseEdition",
        "chatDisclaimer",
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/codemie/configs/test_component_resolution.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'codemie.configs.component_resolution'`

- [ ] **Step 3: Write the module**

```python
# src/codemie/configs/component_resolution.py
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

    # Declared but absent from this customer's YAML: the override is all we have.
    return Component(id=component_id, settings=ComponentSetting(**{"enabled": False, **override}))


def resolve_all(
    yaml_components: list[Component],
    runtime_components: list[Component],
    runtime_ids: set[str],
) -> list[Component]:
    """Every known component, resolved. Runtime-computed ones are appended unchanged."""
    yaml_ids = {c.id for c in yaml_components}
    snapshot_only = [cid for cid in _snapshot if cid not in yaml_ids and cid not in runtime_ids]

    resolved = [
        apply_override(component, _snapshot.get(component.id))
        for component in yaml_components
        if component.id not in runtime_ids
    ]
    resolved += [
        Component(id=cid, settings=ComponentSetting(**{"enabled": False, **_snapshot[cid]}))
        for cid in snapshot_only
    ]
    return resolved + list(runtime_components)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/codemie/configs/test_component_resolution.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/codemie/configs/component_resolution.py tests/codemie/configs/test_component_resolution.py
git commit -m "EPMCDME-14705: Add shared component resolution module"
```

---

### Task 2: `customer_config` reads through the resolver

**Files:**
- Modify: `src/codemie/configs/customer_config.py:33-72` (models move out, re-exported), `:249-295` (read through the resolver), `:271` (docstring fix)
- Test: `tests/codemie/configs/test_customer_config_override.py` (new file; the existing `test_customer_config.py` stays untouched)

**Interfaces:**
- Consumes: `component_resolution.resolve`, `resolve_all`, `Component`, `ComponentSetting` from Task 1.
- Produces:
  - `CustomerConfig.resolve_component(self, component_id: str) -> Component | None`
  - `CustomerConfig.resolve_all(self) -> list[Component]`
  - `is_component_enabled`, `is_feature_enabled`, `get_feature_setting` — signatures unchanged.

**Test-first: yes** — `test_is_component_enabled_follows_the_override` fails because the override snapshot is ignored by the current implementation.

- [ ] **Step 1: Write the failing tests**

```python
# tests/codemie/configs/test_customer_config_override.py
import pytest

from codemie.configs.component_resolution import clear_snapshot, publish_snapshot
from codemie.configs.customer_config import Component, ComponentSetting, CustomerConfig


@pytest.fixture(autouse=True)
def reset_snapshot():
    clear_snapshot()
    yield
    clear_snapshot()


@pytest.fixture
def cfg():
    config = CustomerConfig.model_construct(
        components=[
            Component(id="features:webSearch", settings=ComponentSetting(enabled=True, name="Web Search")),
        ],
        preconfigured_assistants=[],
        tool_defaults={},
    )
    return config


def test_is_component_enabled_follows_the_override(cfg):
    publish_snapshot({"features:webSearch": {"enabled": False}})
    assert cfg.is_component_enabled("features:webSearch") is False


def test_is_feature_enabled_follows_the_override(cfg):
    publish_snapshot({"features:webSearch": {"enabled": False}})
    assert cfg.is_feature_enabled("webSearch") is False


def test_yaml_still_wins_when_no_override(cfg):
    assert cfg.is_feature_enabled("webSearch") is True


def test_unknown_key_still_defaults_to_false(cfg):
    assert cfg.is_feature_enabled("nothingHere") is False
    assert cfg.is_component_enabled("nothingHere") is False


def test_get_feature_setting_follows_the_override(cfg):
    publish_snapshot({"features:webSearch": {"enabled": True, "name": "Overridden"}})
    assert cfg.get_feature_setting("webSearch", "name") == "Overridden"


def test_get_feature_setting_falls_back_to_yaml_for_fields_the_override_omits(cfg):
    publish_snapshot({"features:webSearch": {"enabled": True}})
    assert cfg.get_feature_setting("webSearch", "name") == "Web Search"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/codemie/configs/test_customer_config_override.py -v`
Expected: `test_is_component_enabled_follows_the_override` FAILS — `assert True is False`

- [ ] **Step 3: Rewire `customer_config.py`**

Replace the `ComponentSetting` and `Component` class definitions (`:33-41` and `:63-72`) with a re-export, keeping every existing importer working:

```python
from codemie.configs.component_resolution import (  # re-exported: five modules import these from here
    Component,
    ComponentSetting,
    resolve as _resolve_component,
    resolve_all as _resolve_all_components,
)
```

Replace `is_component_enabled` and `get_feature_setting` (`:249-295`) with:

```python
    def resolve_component(self, component_id: str) -> Component | None:
        """One component, resolved through the shared rule."""
        return _resolve_component(
            component_id,
            self.components,
            self._get_runtime_config(),
            set(CONFIG_IDS.values()),
        )

    def resolve_all(self) -> list[Component]:
        """Every known component, resolved through the shared rule."""
        return _resolve_all_components(
            self.components,
            self._get_runtime_config(),
            set(CONFIG_IDS.values()),
        )

    def is_component_enabled(self, component_id: str) -> bool:
        """
        Check if a component is enabled (includes runtime-computed config and overrides).
        If the component is not in the configuration, it defaults to False (disabled).
        """
        component = self.resolve_component(component_id)
        return component is not None and component.settings.enabled

    def is_feature_enabled(self, feature_key: str) -> bool:
        """
        Check if a feature is enabled.
        If the feature is not in the configuration, it defaults to False (disabled).

        Args:
            feature_key: The feature key (e.g., 'webSearch', 'dynamicCodeInterpreter')

        Returns:
            True if feature is enabled, False otherwise
        """
        return self.is_component_enabled(f"features:{feature_key}")

    def get_feature_setting(self, feature_key: str, setting_name: str, default=None):
        """Read an extra setting from a ``features:<feature_key>`` component's settings.

        ``ComponentSetting`` allows extra fields, so features can carry structured config
        (e.g. an interactive-elements ``catalog``). Returns ``default`` when absent.
        """
        component = self.resolve_component(f"features:{feature_key}")
        if component is None:
            return default
        return getattr(component.settings, setting_name, default)
```

The docstring correction is in the block above: `is_feature_enabled` now documents `False`, matching both the implementation and `tests/codemie/configs/test_customer_config.py:186`.

- [ ] **Step 4: Run the new and the existing config tests**

Run: `poetry run pytest tests/codemie/configs/ -v`
Expected: the 6 new tests pass; `test_customer_config.py` passes unchanged, including `:186`.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/configs/customer_config.py tests/codemie/configs/test_customer_config_override.py
git commit -m "EPMCDME-14705: Resolve backend feature flags through the shared resolver"
```

---

### Task 3: The service populates the snapshot and resolves through it

**Files:**
- Modify: `src/codemie/service/customer_config_service.py:61-100` (`OverrideCache` becomes a facade), `:134-176` (`apply_override` / `_declared_components` removed), `:147` (`resolve_components` collapses)
- Test: `tests/codemie/service/test_customer_config_service.py` — **must pass unchanged**

**Interfaces:**
- Consumes: `publish_snapshot`, `current_snapshot` from Task 1; `customer_config.resolve_all` from Task 2.
- Produces: `OverrideCache.get()` / `invalidate()` / `expire_now()` with unchanged signatures; `apply_override` re-exported from the resolver so importers of `customer_config_service.apply_override` keep working. `Overrides` is imported from the resolver instead of being redefined at `customer_config_service.py:59`.

**Test-first: no** — this is a refactor whose contract is the 309 existing lines of `test_customer_config_service.py`. Those tests are the failing-then-passing evidence: they must stay green throughout, and any need to edit them means behaviour moved.

- [ ] **Step 1: Record the baseline**

Run: `poetry run pytest tests/codemie/service/test_customer_config_service.py -v`
Expected: all pass. Note the count — it must be identical at Step 4.

- [ ] **Step 2: Make `OverrideCache` a facade over the resolver snapshot**

```python
class OverrideCache:
    """Bounded-staleness view of the customer-config overrides.

    The snapshot itself lives in ``configs.component_resolution`` so the synchronous
    resolution path and this one read the same state. This class owns only freshness.
    """

    def __init__(self, ttl_seconds: int):
        self.ttl_seconds = ttl_seconds
        self._loaded = False
        self._expires_at: float = 0.0

    def invalidate(self) -> None:
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
```

- [ ] **Step 3: Collapse `resolve_components` and delegate `apply_override`**

```python
# Re-exported so the router and existing tests keep importing it from here; the rule
# itself now lives in the resolver.
from codemie.configs.component_resolution import apply_override  # noqa: F401


async def resolve_components() -> list[Component]:
    """Return the enabled components with overrides applied before the enabled filter."""
    await override_cache.get()
    return [c for c in customer_config.resolve_all() if c.settings.enabled]
```

Delete `_declared_components` and keep `_switchless_defaults`, now used when building the snapshot entry so a declared component missing from YAML still carries a neutral value per declared field:

```python
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
            overrides[declaration.component_id] = _switchless_defaults(declaration) | parsed

    return overrides
```

- [ ] **Step 4: Run the whole customer-config test surface**

Run: `poetry run pytest tests/codemie/service/test_customer_config_service.py tests/codemie/service/test_customer_config_audit.py tests/codemie/rest_api/routers/test_customer_config_router.py -v`
Expected: identical pass count to Step 1, with **no edits to the test files**.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/customer_config_service.py
git commit -m "EPMCDME-14705: Resolve /v1/config through the shared resolver"
```

---

### Task 4: Warm-up and periodic refresh in the lifespan

**Files:**
- Modify: `src/codemie/service/customer_config_service.py` (add `refresh_overrides`), `src/codemie/rest_api/main.py:786` (warm-up before `_initialize_optional_features()`), `:793-816` (schedule the loop)
- Test: `tests/codemie/service/test_customer_config_refresh.py`, `tests/codemie/rest_api/test_customer_config_warmup.py`

**Interfaces:**
- Consumes: `override_cache` from Task 3.
- Produces:
  - `async def refresh_overrides() -> None` in `customer_config_service` — `expire_now()` then `await override_cache.get()`
  - `async def _warm_customer_config() -> None` in `main` — never raises
  - `def _schedule_customer_config_refresh(tasks: list[asyncio.Task]) -> None` in `main`

**Test-first: yes** — `test_refresh_overrides_reloads_a_stale_snapshot` fails with `ImportError: cannot import name 'refresh_overrides'`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/codemie/service/test_customer_config_refresh.py
from unittest.mock import AsyncMock, patch

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
        customer_config_service, "_load_overrides", AsyncMock(return_value={"features:webSearch": {"enabled": False}})
    ):
        await refresh_overrides()

    assert component_resolution.current_snapshot() == {"features:webSearch": {"enabled": False}}


@pytest.mark.asyncio
async def test_refresh_overrides_reloads_a_stale_snapshot():
    first = AsyncMock(return_value={"features:webSearch": {"enabled": True}})
    with patch.object(customer_config_service, "_load_overrides", first):
        await refresh_overrides()

    second = AsyncMock(return_value={"features:webSearch": {"enabled": False}})
    with patch.object(customer_config_service, "_load_overrides", second):
        await refresh_overrides()

    second.assert_awaited_once()
    assert component_resolution.current_snapshot() == {"features:webSearch": {"enabled": False}}


@pytest.mark.asyncio
async def test_refresh_overrides_keeps_the_last_good_snapshot_when_the_source_fails():
    with patch.object(
        customer_config_service, "_load_overrides", AsyncMock(return_value={"features:webSearch": {"enabled": False}})
    ):
        await refresh_overrides()

    with patch.object(customer_config_service, "_load_overrides", AsyncMock(side_effect=RuntimeError("db down"))):
        await refresh_overrides()

    assert component_resolution.current_snapshot() == {"features:webSearch": {"enabled": False}}
```

```python
# tests/codemie/rest_api/test_customer_config_warmup.py
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.rest_api import main


@pytest.mark.asyncio
async def test_warm_up_loads_the_snapshot():
    with patch.object(main, "refresh_overrides", AsyncMock()) as refresh:
        await main._warm_customer_config()

    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_warm_up_failure_does_not_crash_startup():
    with patch.object(main, "refresh_overrides", AsyncMock(side_effect=RuntimeError("db down"))):
        await main._warm_customer_config()  # must not raise


def test_refresh_loop_is_scheduled_as_a_background_task():
    tasks: list[asyncio.Task] = []
    created = MagicMock(return_value="task-handle")

    with patch.object(main.asyncio, "create_task", created):
        main._schedule_customer_config_refresh(tasks)

    assert created.call_count == 1
    assert created.call_args.kwargs["name"] == "customer_config_refresh"
    assert tasks == ["task-handle"]
    created.call_args.args[0].close()  # the coroutine is never awaited in a test
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/test_customer_config_refresh.py tests/codemie/rest_api/test_customer_config_warmup.py -v`
Expected: `ImportError: cannot import name 'refresh_overrides' from 'codemie.service.customer_config_service'`

- [ ] **Step 3: Add the refresher and wire the lifespan**

In `customer_config_service.py`:

```python
async def refresh_overrides() -> None:
    """Reload the override snapshot, keeping the last good one if the source is unreachable."""
    override_cache.expire_now()
    await override_cache.get()
```

In `rest_api/main.py`, next to the other private lifespan helpers:

```python
async def _warm_customer_config() -> None:
    """Load the override snapshot before the app serves traffic.

    Ordered before ``_initialize_optional_features``, which reads a feature flag itself.
    A failure here leaves the service on YAML; it never blocks start-up.
    """
    try:
        await refresh_overrides()
    except Exception as error:
        logger.warning(f"Customer config warm-up failed, serving YAML until the next refresh. {error=}")


def _schedule_customer_config_refresh(tasks: list[asyncio.Task]) -> None:
    """Keep the override snapshot fresh for synchronous readers that never touch /v1/config."""

    async def _loop() -> None:
        while True:
            await refresh_overrides()
            await asyncio.sleep(override_cache.ttl_seconds)

    tasks.append(asyncio.create_task(_loop(), name="customer_config_refresh"))
```

Import it **by name**, next to the other service imports — the tests patch `main.refresh_overrides`, which only works if the name is bound in this module:

```python
from codemie.service.customer_config_service import override_cache, refresh_overrides
```

Then call the warm-up immediately before `_initialize_optional_features()` (currently `main.py:786`):

```python
    # Load dynamic configuration before anything reads a feature flag
    await _warm_customer_config()

    # Initialize optional features
    _initialize_optional_features()
```

and schedule the loop with the other background tasks, after `tasks = []` (currently `main.py:794`):

```python
    _schedule_customer_config_refresh(tasks)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/test_customer_config_refresh.py tests/codemie/rest_api/test_customer_config_warmup.py tests/codemie/rest_api/test_startup_integration.py -v`
Expected: the new tests pass and the existing startup integration tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/customer_config_service.py src/codemie/rest_api/main.py tests/codemie/service/test_customer_config_refresh.py tests/codemie/rest_api/test_customer_config_warmup.py
git commit -m "EPMCDME-14705: Warm and refresh the override snapshot in the lifespan"
```

---

### Task 5: Pilot — declare `features:webSearch`

**Files:**
- Modify: `src/codemie/service/customer_config_declarations.py:108-146` (add the declaration, extend `DECLARATIONS`)
- Modify: `tests/codemie/service/test_customer_config_declarations.py:71` (rewrite the negative test)
- Test: `tests/codemie/service/tools/test_toolkit_web_search_flag.py` (new seam test)

**Interfaces:**
- Consumes: `SettingDeclaration`, `FieldDeclaration`, `FieldType` — already in the module.
- Produces: `WEB_SEARCH: SettingDeclaration` with `component_id="features:webSearch"`; `DECLARATIONS` gains it.

**Test-first: yes** — `test_web_search_is_disabled_by_the_override` fails because the flag branch resolves from YAML only.

- [ ] **Step 1: Write the failing seam test**

The guides require a call-site test, not only a unit test: "deleting a call-site guard leaves tests green" is an explicit red flag in `.ai-run/guides/testing/testing-patterns.md`. So this test calls `_augment_toolkits_with_feature_flags` itself and asserts on the toolkit it does or does not add. `enable_web_search` is `None` in every existing toolkit test, so both branches are new coverage. The mock shape follows `tests/codemie/service/tools/test_toolkit_service.py:1753-1768`.

```python
# tests/codemie/service/tools/test_toolkit_web_search_flag.py
from unittest.mock import Mock

import pytest

from codemie.configs.component_resolution import clear_snapshot, publish_snapshot
from codemie.rest_api.models.assistant import AssistantChatRequest
from codemie.rest_api.security.user import User
from codemie.service.tools.toolkit_service import ToolkitService
from codemie_tools.base.models import ToolSet


@pytest.fixture(autouse=True)
def reset_snapshot():
    clear_snapshot()
    yield
    clear_snapshot()


@pytest.fixture
def request_with_web_search():
    request = Mock(spec=AssistantChatRequest)
    request.enable_web_search = True
    request.enable_code_interpreter = None
    request.enable_image_generation = None
    request.image_generation_model = None
    request.tools_config = []
    return request


@pytest.fixture
def user():
    mock_user = Mock(spec=User)
    mock_user.id = "test-user-id"
    mock_user.is_admin = False
    return mock_user


@pytest.fixture
def assistant():
    mock_assistant = Mock()
    mock_assistant.name = "test-assistant"
    return mock_assistant


def _augment(request, assistant, user):
    return ToolkitService._augment_toolkits_with_feature_flags(
        [], request, assistant, user, "gpt-4", "test-uuid"
    )


def test_research_toolkit_is_added_when_the_flag_is_on(request_with_web_search, assistant, user):
    toolkits = _augment(request_with_web_search, assistant, user)
    assert ToolSet.RESEARCH in {tk.toolkit for tk in toolkits}


def test_the_override_removes_the_research_toolkit(request_with_web_search, assistant, user):
    publish_snapshot({"features:webSearch": {"enabled": False}})
    toolkits = _augment(request_with_web_search, assistant, user)
    assert ToolSet.RESEARCH not in {tk.toolkit for tk in toolkits}


def test_declaration_exists_for_the_pilot_flag():
    from codemie.service.customer_config_declarations import by_component_id

    declaration = by_component_id("features:webSearch")
    assert declaration is not None
    assert {field.name for field in declaration.fields} == {"enabled"}
```

Note for the implementer: `test_research_toolkit_is_added_when_the_flag_is_on` depends on `features:webSearch` being enabled in `config/customer/customer-config.yaml:125`, which it is. If the import paths for `AssistantChatRequest`, `User` or `ToolSet` differ, copy them from the header of `tests/codemie/service/tools/test_toolkit_service.py` rather than guessing.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/tools/test_toolkit_web_search_flag.py -v`
Expected: `test_declaration_exists_for_the_pilot_flag` FAILS — `assert None is not None`

- [ ] **Step 3: Add the declaration**

```python
WEB_SEARCH = SettingDeclaration(
    component_id="features:webSearch",
    label="Web search",
    description="Google Search, Tavily Search and Web Scraper tools in chat.",
    fields=[
        FieldDeclaration(
            name="enabled",
            type=FieldType.SWITCH,
            label="Enable web search",
        ),
    ],
)

DECLARATIONS: tuple[SettingDeclaration, ...] = (CHAT_DISCLAIMER, RELEASE_NOTES_RECENT_COUNT, WEB_SEARCH)
```

- [ ] **Step 4: Rewrite the negative declaration test**

`tests/codemie/service/test_customer_config_declarations.py:71` asserts `by_component_id("features:webSearch") is None`, which stops being true by definition of this task. Replace the component id with one that is genuinely undeclared:

```python
    def test_undeclared_component_is_not_resolvable(self):
        self.assertIsNone(by_component_id("features:dynamicCodeInterpreter"))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/tools/test_toolkit_web_search_flag.py tests/codemie/service/test_customer_config_declarations.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/customer_config_declarations.py tests/codemie/service/tools/test_toolkit_web_search_flag.py tests/codemie/service/test_customer_config_declarations.py
git commit -m "EPMCDME-14705: Declare features:webSearch as dynamically configurable"
```

---

### Task 6: ~~Document the TTL knob for local development~~ — REVERTED

Attempted, then reverted. `.env.example` is pinned to an exact key list by
`tests/codemie/configs/test_env_example.py`, whose comment states the intent: the file must not
"drift into templating every optional Config field". Adding `CUSTOMER_CONFIG_CACHE_TTL_SECONDS`
failed that test, and the repository's invariant wins over the convenience.

No replacement is needed: the variable has a default in `Config` and reaches a deployed pod through
`.Values.extraEnv`, the standard path rendered at `deploy-templates/templates/deployment.yaml:68`.

---

### Task 7: Full quality gates

**Files:** none — verification only.

**Test-first: no** — this task runs the repository's gates; it writes no code.

- [ ] **Step 1: Lint**

Run: `make ruff`
Expected: clean

- [ ] **Step 2: Licence headers**

Run: `make license-check`
Expected: clean — the new module carries the Apache header

- [ ] **Step 3: Full test suite**

Run: `make test`
Expected: green, with no edits to `tests/codemie/service/test_customer_config_service.py` and `tests/codemie/configs/test_customer_config.py`

- [ ] **Step 4: Commit anything the gates fixed**

```bash
git add -A
git commit -m "EPMCDME-14705: Apply quality gate fixes"
```
