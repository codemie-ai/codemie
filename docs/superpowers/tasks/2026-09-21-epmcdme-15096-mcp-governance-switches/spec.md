# Spec — EPMCDME-15096: MCP Governance Platform Switches

This spec reflects the design already brainstormed and approved by the user conversationally
(superpowers:brainstorming), prior to invoking this pipeline. Per the user's instruction, it is written
directly here rather than by re-dispatching brainstorming.

## Summary

Make `mcpCustomServersDisabled` — today a YAML-only flag — admin-toggleable at runtime, by registering
it in the existing customer-config `SettingDeclaration` registry
(`codemie/src/codemie/service/customer_config_declarations.py`). This is the sole change required: the
registry's own docstring states adding a declaration needs "no API, schema or frontend change."

## Goals

- Admins/maintainers can flip `mcpCustomServersDisabled` on/off without a YAML edit + redeploy.
- Any authenticated-or-not caller of `GET /v1/config` can read the current state once it's on.
- Default state remains off (custom MCP servers allowed) — unchanged from today's YAML default.
- Zero behavior change to existing enforcement (`MCPAccessControlService`) or existing endpoints
  beyond making the flag's *source of truth* dynamic.

## Non-goals

- No changes to `codemie/src/codemie/rest_api/routers/customer_config.py`, the write/read auth gates,
  or `component_resolution.py` — all already generically support any declared component.
- No changes to `access_control.py` / `MCPAccessControlService` (epic sub-task A2, already implemented
  and tested elsewhere).
- No UI changes in codemie-ui (epic sub-task A3) or SDK changes in codemie-sdk (epic sub-task A4) —
  separate tickets.
- No `FEATURE_*`-style env-var override for this component. Explicitly considered and rejected: it
  would require renaming the component id to `features:mcpCustomServersDisabled`, which rips into two
  places that hardcode the current string (`access_control.py`,
  `codemie-ui/src/constants/mcp.ts`) — out of scope for this ticket.

## Design

Add to `customer_config_declarations.py`:

```python
MCP_CUSTOM_SERVERS_DISABLED = SettingDeclaration(
    component_id="mcpCustomServersDisabled",
    label="MCP Custom Servers Disabled",
    description="When enabled, restricts users to catalog-referenced MCP servers only. Custom inline MCP server configuration is not permitted.",
    fields=[
        FieldDeclaration(
            name="enabled",
            type=FieldType.SWITCH,
            label="Restrict to catalog MCP servers",
        ),
    ],
)
```

- `component_id` is byte-for-byte identical to the existing YAML id and to the string hardcoded in
  `access_control.py` / `mcp.ts` — no rename.
- `label` / top-level `description` are lifted verbatim from
  `codemie/config/customer/customer-config.yaml:255-256`.
- The field's own `label` ("Restrict to catalog MCP servers") deliberately does not say "Enabled" —
  the component is named "...Disabled", so a switch literally labeled "Enabled" next to it would read
  as a double negative. This wording states what turning the switch on actually does.
- Add `MCP_CUSTOM_SERVERS_DISABLED` to the `DECLARATIONS` tuple.

### Why this is sufficient (acceptance criteria)

1. **Admin-only write** — `PUT/DELETE /v1/config/declarations/mcpCustomServersDisabled` already
   requires `Depends(authenticate)` + `Depends(require_customer_config_write)` unconditionally for
   every declared component; registering this one is what makes those routes accept its id (previously
   they 404 via `_require_declaration`).
2. **Everyone can read** — `GET /v1/config` has no auth dependency and
   `customer_config_service.resolve_components()` returns only components resolved as `enabled=True`;
   this flag is invisible there while off, present there (still unauthenticated) once an admin turns it
   on. No change needed to reach this behavior.
3. **Default off** — the YAML default (`enabled: false`) is untouched; the DB override table has no
   row for this key until an admin writes one, so `resolve()` falls through to the YAML value.
4. **Enforcement inherits the toggle for free** — `MCPAccessControlService`'s check reads
   `customer_config.is_component_enabled("mcpCustomServersDisabled")` →
   `CustomerConfig.resolve_component()` → the shared `component_resolution.resolve()`, the identical
   resolver behind `GET /v1/config`. Once the declaration exists, an admin's runtime toggle is
   immediately authoritative for enforcement — no separate wiring.
5. **Admin settings page** — `GET /v1/config/declarations` iterates the full `DECLARATIONS` tuple and
   the settings UI renders any declaration generically; this component appears there automatically.

## Testing

Add to `tests/codemie/service/test_customer_config_declarations.py`, mirroring the existing
`SCHEDULERS` tests:

- `test_mcp_custom_servers_disabled_declaration_registered` — `component_id` present in `DECLARATIONS`.
- `test_mcp_custom_servers_disabled_has_enabled_switch` — `component_id`, `label`, single `enabled`
  field of `FieldType.SWITCH`.
- `test_by_component_id_finds_mcp_custom_servers_disabled` — lookup round-trips to the same object.

The existing parametrized/aggregate tests (`test_key_is_derived_from_component_id`,
`test_every_declaration_has_a_unique_key`) already cover the new entry once added, with no changes.
No changes anticipated to any other `test_customer_config_*.py` file — they exercise the generic
mechanism against whatever is in `DECLARATIONS`.

## Error handling / edge cases

None beyond existing generic behavior. A single boolean `SWITCH` field has no pattern, length, or
markdown/sanitisation concerns; `customer_config_service.validate_and_sanitize` already handles it
without new code paths.

## Risks

Low. Additive, single-entry change to an already-implemented, already-tested generic registry
(precedent: `WEB_SEARCH`, `SCHEDULERS`). Rollback is deleting the tuple entry. See
`technical-analysis.md` §6 for the full risk write-up (scope-creep risk toward the rejected
`features:`-prefix rename is the only one called out).
