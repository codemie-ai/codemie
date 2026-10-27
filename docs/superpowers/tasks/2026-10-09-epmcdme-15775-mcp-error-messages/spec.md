# EPMCDME-15775 (Bug): Hide mcp_config_id from the error shown when saving an assistant or workflow with a custom MCP server while "MCP Custom Servers Disabled" is enabled

Related: EPMCDME-15094 (MCP Catalogue Governance, shipped). Fix version Prod 2.58.0.

## Problem
With the platform setting `mcpCustomServersDisabled` on, saving an assistant or workflow that has a custom MCP server (e.g. named `rogue`, no catalogue reference) returns:
"Custom MCP servers are not allowed in restricted mode. Server 'rogue' must reference a catalog entry via mcp_config_id."
The internal field name `mcp_config_id` is exposed. The error should still identify the server by name, but not expose this implementation detail.

## Acceptance criteria
1. When saving an assistant or workflow with a custom MCP server while the setting is enabled, the error does not expose `mcp_config_id`.
2. The error continues to identify the offending MCP server by its name.
3. Regression tests cover this validation error for both assistant and workflow saves.

## Additional intent of this change (beyond the ticket; reviewer may judge scope)
- All restricted-mode refusals are user-actionable text with no internal field names (`mcp_config_id`, `use_custom_config`, `'config'`).
- "not found" / "inactive" / "not public" catalogue refusals are merged into ONE message; the exact reason and the catalogue id are logged only (so a signed-in user cannot probe which catalogue entries exist). The duplicate-entry message lists no ids.
- Version-restore refusals say "Can't restore this version: ..." and advise choosing another version (a stored version cannot be edited).
- Server names in messages: quotes, markup, control chars removed; whitespace collapsed; 60 chars; "unnamed" when empty; max 5 names then "and N more".
- Server names and catalogue ids are logged with repr() so they cannot forge log lines.
- Same HTTP status (400) and same rules as before: text-only change; refusal logic unchanged.
