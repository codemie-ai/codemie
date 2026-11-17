# Spec: Deselectable Datasource Default Tools (Backend)

**Status**: Ready for planning
**Parent story**: `./story.md`
**Technical analysis**: `./technical-analysis.md`

## Intent

Three default datasource tools — Get Repo Tree, Search Code, Search Knowledge Base — are
currently force-added to every assistant with a matching datasource, regardless of the
assistant's explicit tool selection. An author cannot turn them off. This spec adds one
persisted, per-assistant opt-out list so an author can deselect any of the three, and makes
every path that assembles, lists, versions or invokes assistant tools honour it.

## Data shape

New field on `Assistant`, assistant-version configuration, and the SDK/export payload shape
(`rest_api/models/assistant.py`, `assistant_version_migration_service.py`):

```python
deselected_default_tools: list[str] = []
```

Valid values are exactly the three canonical standard tool names already defined in
`tools_vars.py` / `search_kb.py`:
`"get_repository_file_tree"`, `"search_code_repo"`, `"search_kb"`.
The "with filtering" (`_v2`) variants are never valid entries — deselecting the standard name
covers both variants (AC2); the V2 name itself is a `toolkits` selection, not a deselect.

## Behavior

- **Validation (save-time, in the assistant save/update path)**: any entry not in the
  three-name set above is rejected before anything is persisted, with a message naming the
  offending value(s) (AC10). Selecting a `toolkits` V2 variant alongside its standard name in
  `deselected_default_tools` is accepted and unchanged at runtime — V2 still wins (D1, AC9).
- **Tool assembly** (`toolkit_service.py::add_context_tools` / `_add_code_tools` /
  `_add_kb_tools`): each of the three default tools is added only if its canonical name is
  absent from `assistant.deselected_default_tools`. This check is independent of, and
  additional to, the existing `toolkits`/`exclude_extra_context_tools` gating — it is a further
  "may this tool exist at all for this assistant" gate, checked once per tool kind, not
  per-datasource (AC2, AC3).
- **Direct invoke** (`tool_execution_service.py`): before building the tool, reject with the
  existing "tool not available" error if the requested canonical name is in the assistant's
  `deselected_default_tools`. Must check across all of the assistant's datasources, not just
  the first (D6, AC8) — the existing single-datasource lookup in `_get_context_tools` needs to
  stop short-circuiting on `next(iter(...))` for this check.
- **Listing** (`tools_info_service.py::get_tools_info`): Get Repo Tree and standard Search Code
  move from API-only (`get_tools_api_info`, "hidden from UI") into the UI listing
  (`CodeToolkitUI` / `get_tools_ui_info`) under Codebase Tools; Search Knowledge Base is listed
  under Knowledge Base tools for both `show_for_ui=True` and `False` (AC1). Tool-search index
  and assistant-generator validation pick these up unchanged, since both consume
  `get_tools_info` (D5) — no separate change needed there beyond this listing move.
- **Descriptions**: remove "call repo tree first" language from the standard Search Code
  (`CODE_SEARCH_TOOL`) description/react_description and from any other default-tool
  description found to reference it, so that once Get Repo Tree is deselected nothing still
  instructs the model to call it (AC5). The V2 repo tree description's own "must be first"
  clause is unaffected — it only fires when Get Repo Tree's V2 variant is itself selected.
- **Versioning, restore, export/import, workflow-referenced assistants**: `deselected_default_tools`
  is carried wherever `context`/`toolkits` are already carried — `AssistantVersionCompareService`
  (changing only this list counts as a configuration change, AC7), version create/restore,
  `assistant_version_migration_service.py`, export/import, and
  `virtual_assistant_service.py` (D7, AC6). No new migration framework; follow the existing
  field-by-field pattern at each of those sites.
- **Legacy assistants**: an absent or empty list is equivalent to "nothing deselected" — behavior
  matches today exactly (D2, AC4). Smart tool selection's "no toolkits selected" check
  (`get_tools` l.528) is untouched; it does not consult this new field.

## Persistence

One Alembic migration adds the column to the assistants table and to the assistant-version /
assistant-configuration table(s), both nullable or defaulted to `[]`, non-breaking for existing
rows.

## Acceptance criteria

Verbatim from `./story.md` — all ten checkboxes under "Acceptance Criteria" there are binding
and are the Stage 6 acceptance lens for this change.

## Non-goals

- Python/Node SDK changes and UI changes — separate stories.
- Changing which tools exist, or removing the repository tree tool from the platform.
- New code navigation tools.
- Per-datasource tool selection — this feature is per assistant only.
- The workflow YAML `exclude_extra_context_tools` setting — stays as-is, untouched by this field.
- Provider datasource tools, Git tools, and MCP tools — not covered by this deselect list.
- Platform-wide or project-wide tool-disable policies.
- A standard/V2 variant-conflict validation rule — intentionally not added (D1).

## Open risks

- The analysis did not trace every tool-description and system-prompt string that references
  "repository tree" — the description cleanup (AC5) may need to extend past `CODE_SEARCH_TOOL`
  once the implementer greps further.
- Version compare/restore, export/import and `virtual_assistant_service` paths have no located
  existing tests; new coverage there is net-new, not an extension of an existing suite.
