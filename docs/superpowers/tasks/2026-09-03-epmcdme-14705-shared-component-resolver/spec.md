# EPMCDME-14705 — One component resolver shared by the backend and `/v1/config`

Design for the shared resolver, its lifecycle, and the `features:webSearch` pilot.

Ticket: <https://jiraeu.epam.com/browse/EPMCDME-14705>
Grounding: `technical-analysis.md`, `complexity-assessment.json` (L, 22/36) in this directory.

---

## Problem

Dynamic customer configuration overrides only what the frontend is served. Two read paths exist over
the same YAML-backed singleton, and only one of them knows about overrides:

```
YAML ──> customer_config (singleton, parsed once at import)
           │
           ├──> is_component_enabled / is_feature_enabled / get_feature_setting
           │        └──> 21 backend call sites            ← the override is invisible here
           │
           └──> resolve_components() ──> GET /v1/config ──> frontend
                        ↑
                  override rows from dynamic_config
```

An admin flipping a switch changes the UI while the backend keeps running on YAML. That split brain
blocks 13 operationally valuable components from becoming dynamic.

The resolver grew at the API layer because EPMCDME-13983 had exactly one consumer. This design moves
it below both consumers.

## Approach

Both operations reduce to the same one: **resolve a component by id**. The frontend path is that
resolution applied to every component, then filtered by `enabled`. So the design is a single
resolution function, not a shared rule with two implementations:

```
configs/component_resolution.py          ← new; owns the snapshot AND the precedence rule
        ▲                    ▲
        │                    │
configs/customer_config.py   service/customer_config_service.py
  (reads, synchronously)       (populates it; resolves /v1/config through it)
```

`configs/` imports nothing from `service/`, and the service imports from configs, so a module below
both breaks no rule. `Component` and `ComponentSetting` already live in `configs/customer_config.py`,
which is what makes the merge belong there naturally.

### What each side does

```python
# configs/component_resolution.py — the only resolution
def resolve(component_id, yaml_components, runtime_components, runtime_ids) -> Component | None
def resolve_all(yaml_components, runtime_components, runtime_ids) -> list[Component]

# configs/customer_config.py — synchronous consumers, signatures unchanged; the thin
# resolve_component/resolve_all methods just supply this instance's components
def is_component_enabled(self, cid) -> bool
def get_feature_setting(self, key, name, default)

# service/customer_config_service.py — the HTTP path
async def resolve_components() -> list[Component]:
    await override_cache.get()                       # the only difference: ensure freshness
    resolved = component_resolution.resolve_all(
        customer_config.components,
        customer_config.get_runtime_components(),
        set(CONFIG_IDS.values()),
    )
    return [c for c in resolved if c.settings.enabled]
```

The resolution takes its inputs as arguments rather than importing `customer_config`, which is what
keeps the module at the bottom with no cycle. The service passes the **public**
`get_runtime_components()`, exactly as the previous implementation did.

The async wrapper stops being a second resolver and becomes one line meaning "make sure the snapshot
is fresh". Everything below it is the shared synchronous code.

### What collapses rather than grows

- `apply_override` moves into the resolver and serves both paths instead of one.
- `_declared_components` dissolves. Its job — resolve a component the YAML omits — becomes a plain
  rule in the resolver: iterate the YAML components, plus any id present in the snapshot but not in
  YAML. `_switchless_defaults` stays in the service, because its neutral value per field
  (`declaration.empty_value()`) needs the declaration; the service folds those defaults into the
  snapshot entry it hands over, so the resolver still never sees a declaration.
- `resolve_components()` shrinks from ~10 lines to two.
- `OverrideCache` stops holding `_overrides` itself and becomes a facade over the resolver's
  snapshot — one store instead of two. It keeps its name and module: existing tests patch
  `customer_config_service.override_cache` by name.
- `is_component_enabled` loses its own runtime-component branch (`customer_config.py:254-259`); that
  check lives in the shared resolver, which is where `resolve_components()` was already doing the
  equivalent filtering by other means.

### What stays in the service

Knowledge of declarations. Parsing a stored row and filtering it to declared field names
(`_parse_override`) needs `DECLARATIONS`, which the resolver in `configs/` must not import. The
service therefore hands the resolver a ready `{component_id: {field: value}}` map. This also means
the declarations registry does **not** relocate, and `rest_api/models/customer_config.py:23` needs no
change.

## Resolution semantics

Precedence is **per field**, not per component:

1. Runtime-computed component (`CONFIG_IDS`) — never overridable, wins outright.
2. The override, for each field it actually carries.
3. YAML, for everything else.

A stored row always carries the full declared set — `validate_and_sanitize` rejects a payload missing
any declared field — so per-field resolution matters in two cases, both real:

- Fields the declaration does not expose (`name`, `description`, anything absent from the admin
  form). They never reach the database and always come from YAML, so they keep following
  deployments.
- Declared fields added after a row was written. `_parse_override` already guards this with
  `if name in value`; the new field resolves from YAML until the next save freezes it.

A component declared but absent from YAML still resolves (CR-004 of the EPMCDME-13983 design): the
service folds the declaration's neutral per-field values into the snapshot entry, and the resolver
builds the component from that entry when YAML has no such id.

**The missing-key default does not change.** A key present in neither the override nor YAML resolves
to `False`. `is_feature_enabled` prefixes `features:` and delegates to `is_component_enabled`, whose
fallback is `False`, and `tests/codemie/configs/test_customer_config.py:186` asserts exactly that.
The docstring at `customer_config.py:271` claims `True` and is simply wrong — correct that line. Do
not change the default: it would flip behaviour at 18 call sites, four of them `subWorkflow` guards.

## Snapshot lifecycle

The snapshot is module state in the resolver, read synchronously with **zero I/O**. This is forced
and also correct: `DynamicConfigService` has no synchronous `list_by_key_prefix`, and the repository's
existing sync→async bridge is documented as deadlock-prone (`project_member_runtime_sync.py:390`) and
prone to "Future attached to a different loop". The synchronous path never touches the database; it
reads what was put there.

Only the asynchronous side writes, in three ways:

| When | What happens |
|---|---|
| Process start-up | awaited warm-up in the lifespan, **before** `_initialize_optional_features()` (`main.py:786`) |
| Every TTL seconds | background loop: `refresh_overrides()`, then sleep |
| Admin save / reset | `override_cache.invalidate()` — unchanged |

`refresh_overrides()` is `expire_now()` followed by `await override_cache.get()`. `expire_now()`
(`customer_config_service.py:78`) exists today with zero callers and a docstring describing exactly
these semantics. The distinction from `invalidate()` matters: `invalidate()` drops the snapshot, so
an unreachable database leaves nothing behind; `expire_now()` keeps the last known good one.

Warm-up ordering is deliberate. `_initialize_optional_features` reads
`is_feature_enabled("subWorkflow")` at `main.py:394`; the pilot does not depend on this, but placing
the warm-up earlier means migrating `subWorkflow` later will not have to move it.

**Degradation is inherited, not rewritten.** `_load_overrides` already implements the ladder —
database error → last known good snapshot → empty map → YAML — and never raises to the caller. So an
unreachable database at start-up means "running on YAML", never "pod failed to start". The startup
probe budget (~600s, sized for Alembic) leaves ample room for one `SELECT`.

The loop reads its interval from `override_cache.ttl_seconds` rather than a literal 60: the value is
already taken from config at import (`customer_config_service.py:100`) and should not be duplicated
in a third place.

Two known traps are closed by this shape:

- `codemie_tools/base/models.py:22` binds `customer_config.get_tool_default` as a bound method at
  import time. This design mutates state behind the singleton rather than replacing it, so the
  binding stays valid.
- `override_cache` keeps its name and module, so tests patching it by name keep working.

## Pilot: `features:webSearch`

One declaration with a single `enabled` switch field. Chosen because it has exactly one backend call
site (`toolkit_service.py:260`, inside `_augment_toolkits_with_feature_flags`), it is not read during
start-up, and its effect is visible in a chat. The frontend surface already exists —
`useFeatureFlag('features:webSearch')` in `DynamicToolsSettings.tsx:36` and
`chatInterfaceBasics.ts:177` — so AC11 needs no UI work.

## Testing

- **The resolver directly**: precedence, per-field resolution, a key absent everywhere (`False`), a
  stored row missing a declared field, a component declared but absent from YAML.
- **The call site separately.** `.ai-run/guides/testing/testing-patterns.md` ("Seam Tests for Policy
  Helpers") flags exactly this: "deleting a call-site guard leaves tests green". The pilot brings a
  test for `toolkit_service.py:260` in both branches — today `enable_web_search` is only ever `None`
  across ~1800 lines of toolkit tests.
- **The periodic loop** is the one place needing a new pattern: `grep "while True" tests` over 1058
  files returns nothing. Split it — `_refresh_once()` is an ordinary coroutine tested by direct call;
  the loop's scheduling is asserted by patching `main.asyncio.create_task` and inspecting the
  arguments, the way `_schedule_startup_recovery` is already tested. The infinite loop never runs in
  a test.
- **Warm-up** as a standalone helper, matching the house norm (`_initialize_optional_features` and
  friends are tested as plain functions, not through the fifteen-deep `patch` pyramid in
  `test_startup_integration.py`), plus a "warm-up fails, start-up continues" test modelled on
  `test_no_enterprise.py:58`.
- **An autouse reset fixture for the resolver snapshot.** It is module state exactly like
  `override_cache`, which already has one (`test_customer_config_service.py:36`); without it an
  override set in one test leaks into the next.

Two notes on existing tests:

- `test_customer_config.py:186` stays untouched. The technical analysis predicted its failure on the
  assumption that the unknown-key default would flip to `True`; since the default does not change,
  the test keeps guarding real behaviour.
- `test_customer_config_declarations.py:71` (`features:webSearch` asserted undeclared) is rewritten
  deliberately — it stops being true by definition of this task.
- The 309 lines of `test_customer_config_service.py` must pass **unchanged**. That is the evidence
  that resolution moved without changing. Needing to adjust them is a signal, not routine work.

## Out of scope

- Per-request consistency. A long request may read a flag twice across a refresh and see different
  values — the same thing a rolling redeploy already does. A request-scoped `ContextVar` was
  considered and rejected: workflow execution has no request boundary, and the context would have to
  be threaded into background threads by hand.
- Cross-pod invalidation (Redis pub/sub, `LISTEN/NOTIFY`). Buys at most the TTL on a rare, deliberate
  admin action, in exchange for a new infrastructure dependency and failure mode.
- Changing the missing-key default.
- Migrating the other 12 backend-read components.
- Making `_initialize_optional_features` itself dynamic. Warm-up is ordered before it so a future
  migration is possible, but `subWorkflow` still cannot be flipped at runtime without solving
  re-initialisation.
- Consolidating YAML and the database into one source of truth.
- Admin UI changes: the form renders from declarations and already supports switch fields.

## Incidental — attempted and reverted

`CUSTOMER_CONFIG_CACHE_TTL_SECONDS` exists on `Config` (`configs/config.py:793`) but is absent from
`.env.example`, so documenting it there looked like a free improvement. It is not:
`tests/codemie/configs/test_env_example.py` pins that file to an exact key list, with the stated
intent that it must not "drift into templating every optional Config field". The addition was made,
the test caught it, and the change was reverted rather than the invariant widened.

The knob needs no surfacing anyway: `deploy-templates/templates/deployment.yaml:68` renders
environment through `codemie.mergeEnvLists` over `.Values.extraEnv` / `.Values.customEnv`, which is
how every operator-set variable reaches a pod.
