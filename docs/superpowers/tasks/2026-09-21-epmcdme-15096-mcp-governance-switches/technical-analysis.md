# Technical Analysis — EPMCDME-15096: MCP Governance Platform Switches

Reused from the same-session exploration performed conversationally before this pipeline run
(superpowers:brainstorming, project-context step) against the parent epic's own research
(`docs/superpowers/tasks/2026-09-18-epmcdme-15094-mcp-catalogue-governance/technical-analysis.md`),
narrowed to this sub-task's exact scope: registering `mcpCustomServersDisabled` as a runtime-toggleable
customer-config component. No new dispatch was run for this document; per user instruction, Stage 1
research already completed for this scope was written up directly instead of being redone.

## 1. Scope

Make `mcpCustomServersDisabled` — today a YAML-only flag — editable at runtime by admins/maintainers,
readable by everyone, defaulting to off, using the existing customer-config declaration mechanism.
No router, service, resolver, or UI code changes are in scope; those are covered by other epic
sub-tasks (A2 enforcement — already implemented; A3 web UI; A4 SDK parity).

## 2. Current state

- `codemie/config/customer/customer-config.yaml:252-256` already defines the component:
  ```yaml
  - id: "mcpCustomServersDisabled"
    settings:
      enabled: false
      name: "MCP Custom Servers Disabled"
      description: "When enabled, restricts users to catalog-referenced MCP servers only. Custom inline MCP server configuration is not permitted."
  ```
- It is absent from `codemie/src/codemie/service/customer_config_declarations.py`'s `DECLARATIONS` tuple
  (currently `CHAT_DISCLAIMER, RELEASE_NOTES_RECENT_COUNT, WEB_SEARCH, SCHEDULERS`), so it can only be
  changed today via YAML edit + redeploy.
- Enforcement (`MCPAccessControlService`, epic sub-task A2) already reads this flag via
  `customer_config.is_component_enabled("mcpCustomServersDisabled")` and is already implemented/tested.

## 3. Mechanism this task extends

`customer_config_declarations.py`'s own docstring: "Making a component dynamic means appending a
declaration here — no API, schema or frontend change is required." Verified end-to-end:

- **Write path**: `PUT/DELETE /v1/config/declarations/{component_id}`
  (`codemie/src/codemie/rest_api/routers/customer_config.py`) unconditionally requires
  `Depends(authenticate)` + `Depends(require_customer_config_write)` — this is the existing,
  universal admin/maintainer gate; no bespoke permission code is needed.
- **Read path**: `GET /v1/config` has no auth dependency at all, and
  `customer_config_service.resolve_components()` filters to `component.settings.enabled == True`
  before returning — so while off, the component is invisible on the public endpoint; once an admin
  turns it on, it appears there too. This exactly satisfies "everyone can read, default off" with no
  new code.
- **Enforcement path**: `CustomerConfig.is_component_enabled()` →
  `CustomerConfig.resolve_component()` → the shared `component_resolution.resolve()` — the identical
  override-aware resolver `GET /v1/config` uses. Registering the declaration therefore makes
  `MCPAccessControlService`'s own enforcement check runtime-toggleable for free, with zero changes to
  `access_control.py`.
- **Admin settings UI**: `GET /v1/config/declarations` (admin/maintainer-gated) iterates the full
  `DECLARATIONS` tuple generically; the settings page renders any declaration generically. No frontend
  changes needed for this sub-task.

## 4. Declaration shape (precedent: `WEB_SEARCH`, `SCHEDULERS`)

Single-field boolean toggles in the existing registry follow one shape — `component_id`, `label`,
`description`, one `enabled` `FieldDeclaration` of `FieldType.SWITCH`. `mcpCustomServersDisabled` fits
this precedent exactly and needs no new `FieldType`, validation rule, or sanitisation path
(`customer_config_service.validate_and_sanitize` already handles `SWITCH` fields generically; no
markdown/pattern concerns apply).

Constraint carried over from the epic-level risk analysis: `mcpCustomServersDisabled` is intentionally
**not** `features:`-prefixed (unlike `WEB_SEARCH`/`SCHEDULERS`), so it will not receive the automatic
`FEATURE_*` env-var override that `CustomerConfig._apply_feature_env_override` grants to `features:`-
prefixed components. This is consistent with the `CHAT_DISCLAIMER` precedent (also unprefixed, also no
env override) and was confirmed in scope discussion as the desired behavior for this sub-task — a
rename to `features:mcpCustomServersDisabled` was considered and explicitly rejected, since both
`access_control.py` and `codemie-ui/src/constants/mcp.ts` hardcode the current unprefixed string.

## 5. Test surface

`tests/codemie/service/test_customer_config_declarations.py` is the template: parametrized/aggregate
tests (`test_key_is_derived_from_component_id`, `test_every_declaration_has_a_unique_key`) already
cover any new entry automatically. Precedent-specific tests to mirror (see `SCHEDULERS`'s three tests):
registered-in-`DECLARATIONS`, has-enabled-switch, `by_component_id` lookup.

No changes anticipated in the other `test_customer_config_*.py` files (write-guard, validation,
override, warmup, audit, refresh, service) — those exercise the generic mechanism across whatever is in
`DECLARATIONS` and need no sub-task-specific additions.

## 6. Risk Indicators

- **Low risk.** This is an additive, single-entry change to an existing, already-tested generic
  registry. No new abstractions, no schema/migration, no API surface change.
- The only latent risk is scope creep toward the `FEATURE_*`-prefix rename — explicitly out of scope
  per the design decision above; implementers should not "improve" this into a `features:`-prefixed
  id.
- Enforcement (A2) and the read endpoint are already implemented and tested elsewhere; this task must
  not duplicate or modify them.

## 7. Codebase Findings

- File to change: `codemie/src/codemie/service/customer_config_declarations.py` — add one
  `SettingDeclaration` (`component_id="mcpCustomServersDisabled"`), add it to the `DECLARATIONS` tuple.
- File to change: `tests/codemie/service/test_customer_config_declarations.py` — add three tests
  mirroring the `SCHEDULERS` pattern.
- No other production files require changes for this sub-task.
