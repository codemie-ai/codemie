# Deselectable Datasource Default Tools (Backend) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an assistant author persist a per-assistant opt-out list of the three default
datasource tools (Get Repo Tree, Search Code, Search Knowledge Base) and have every path that
assembles, lists, versions, or invokes assistant tools honor it.

**Architecture:** One new field, `deselected_default_tools: list[str]`, added once to
`AssistantBase` (so `Assistant`, `VirtualAssistant`, `VirtualIdeAssistant` all inherit it) and
once to `AssistantRequest` and `AssistantConfiguration`. Everywhere `context`/`toolkits` are
already read, mapped, versioned, or restored, the same field is added alongside them, following
the existing per-field pattern (no new abstraction). Runtime gating is a membership check against
this list, independent of and additional to the existing `toolkits` gating.

**Tech Stack:** Python, SQLModel/Pydantic, Alembic, FastAPI, pytest.

**Spec:** `docs/superpowers/tasks/2026-10-19-deselect-default-datasource-tools/spec.md`

Commit per task using the repository's existing convention; no commit steps are spelled out
below.

## Global Constraints

- Valid `deselected_default_tools` entries are exactly `"get_repository_file_tree"`,
  `"search_code_repo"`, `"search_kb"` — the three canonical standard tool names. The `_v2`
  variants are never valid entries.
- An absent or empty list must behave exactly like today for every existing assistant (no data
  migration, no backfill).
- Selecting a `toolkits` V2 variant alongside its standard name in `deselected_default_tools` is
  accepted; V2 still wins at runtime (D1) — no variant-conflict validation is added.
- This field does not touch `exclude_extra_context_tools` (workflow YAML setting), per-datasource
  selection, provider/Git/MCP tools, or smart tool selection's "no toolkits selected" check.
- No SDK or UI changes (separate stories) — only the stored shape and backend behavior.

## Review Focus

- A save request with an unknown string in `deselected_default_tools` (e.g. a typo or a `_v2`
  name) must be rejected with nothing persisted, not silently dropped or silently accepted —
  covered in Task 3.
- An assistant with multiple code or KB datasources must have the deselected tool missing for
  *all* of them, not just the first matched context — covered in Task 4 (`add_context_tools` is
  called once per tool kind already; Task 5 covers the direct-invoke path's own first-context
  bug).
- Direct tool invoke for an assistant whose only datasource is of the *other* type (e.g. only a
  KB datasource, tool_name is a code tool) must still resolve/fail the same way it does today —
  the new gate must not change behavior for non-deselected, inapplicable tool names — covered in
  Task 5.
- A partial update request that omits `deselected_default_tools` entirely (not explicitly set)
  must preserve the assistant's existing deselected list, not reset it to `[]` — covered in
  Task 3 and Task 6, following the existing `fields_set` / `versioned()` partial-update pattern.
- Rollback to an older version must restore that version's `deselected_default_tools` onto the
  live assistant record, not just the new version row — covered in Task 6
  (`update_assistant_config_fields`).

---

## Task 1: Persist the column

**Files:**
- Create: `src/external/alembic/versions/<new_revision>_add_deselected_default_tools.py`

**Interfaces:**
- Produces: a nullable, default-`[]` JSONB-compatible column `deselected_default_tools` on
  tables `assistants` and `assistant_configurations`.

- [ ] **Step 1: Determine the current alembic head**

Run: `cd src/external/alembic && alembic heads` (or inspect the most recent file under
`versions/` for its `down_revision`/`revision` chain) to get the `down_revision` value for the
new migration.

- [ ] **Step 2: Write the migration**

Follow the exact shape of `src/external/alembic/versions/14c6b7e8f9a0_add_image_generation_model_to_assistants.py`:
`op.add_column('assistants', sa.Column('deselected_default_tools', sa.JSON(), nullable=True))`
and the same for `'assistant_configurations'`; `downgrade()` drops both columns.

- [ ] **Step 3: Run the migration locally**

Run: `alembic upgrade head` (per `.ai-run/guides/development/setup-guide.md` for the exact
invocation in this repo). Expected: migration applies with no error; `downgrade()` then
`upgrade()` round-trips cleanly.

Test-first: no — a schema migration has no unit test; its own apply/rollback run is the
verification (Step 3).

---

## Task 2: Add the field to the models

**Files:**
- Modify: `src/codemie/rest_api/models/assistant.py:656` (`AssistantBase`, alongside `toolkits`)
- Modify: `src/codemie/rest_api/models/assistant.py:330` (`AssistantRequest`, alongside `toolkits`)
- Modify: `src/codemie/rest_api/models/assistant.py:1277` (`AssistantConfiguration`, alongside
  `toolkits`)
- Test: `tests/codemie/rest_api/models/test_assistant_model.py`

**Interfaces:**
- Produces: `Assistant.deselected_default_tools: list[str]`,
  `AssistantRequest.deselected_default_tools: list[str]`,
  `AssistantConfiguration.deselected_default_tools: list[str]`, `VirtualAssistant` and
  `VirtualIdeAssistant` inherit the field from `AssistantBase` with no further change.

- [ ] **Step 1: Write the failing test**

```python
def test_deselected_default_tools_defaults_to_empty_list():
    assistant = Assistant(name="a", description="d", system_prompt="s")
    assert assistant.deselected_default_tools == []

def test_deselected_default_tools_round_trips_through_model_dump():
    assistant = Assistant(
        name="a", description="d", system_prompt="s",
        deselected_default_tools=["search_kb"],
    )
    rehydrated = Assistant(**assistant.model_dump())
    assert rehydrated.deselected_default_tools == ["search_kb"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/rest_api/models/test_assistant_model.py -k deselected_default_tools -v`
Expected: FAIL with `deselected_default_tools` not a recognized field.

- [ ] **Step 3: Add the field to the three models**

In `AssistantBase` (line 656), add
`deselected_default_tools: list[str] = SQLField(default_factory=list, sa_column=Column(JSONB))`
next to `toolkits`. In `AssistantRequest` (line 330), add
`deselected_default_tools: list[str] = Field(default_factory=list)`. In `AssistantConfiguration`
(line 1277), add the same `SQLField`/`JSONB` declaration as `AssistantBase`'s.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/codemie/rest_api/models/test_assistant_model.py -k deselected_default_tools -v`
Expected: PASS.

Test-first: yes — the two tests above (`test_deselected_default_tools_defaults_to_empty_list`,
`test_deselected_default_tools_round_trips_through_model_dump`) are written before the field
exists and fail until Step 3.

---

## Task 3: Save-time validation

**Files:**
- Modify: `src/codemie/rest_api/models/assistant.py:992` (new sibling of
  `_validate_mcp_server_names`) and `:928` (`validate_fields`, add the call)
- Test: `tests/codemie/rest_api/models/test_assistant_model.py`

**Interfaces:**
- Consumes: `self.deselected_default_tools` (Task 2).
- Produces: `Assistant._validate_deselected_default_tools(self) -> Optional[str]`, wired into
  `Assistant.validate_fields()` so both `save()` and `update()` (via
  `rest_api/models/base.py:195` / `:508` and `:211` / `:522`) reject before any commit.

- [ ] **Step 1: Write the failing test**

```python
def test_validate_rejects_unknown_deselected_tool():
    assistant = Assistant(
        name="a", description="d", system_prompt="s",
        deselected_default_tools=["get_repository_file_tree_v2"],
    )
    error = assistant.validate_fields()
    assert "get_repository_file_tree_v2" in error

def test_validate_accepts_known_deselected_tools():
    assistant = Assistant(
        name="a", description="d", system_prompt="s",
        deselected_default_tools=["get_repository_file_tree", "search_kb"],
    )
    assert assistant._validate_deselected_default_tools() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/rest_api/models/test_assistant_model.py -k deselected -v`
Expected: FAIL (`validate_fields()` does not flag the unknown name; method does not exist for
the second test).

- [ ] **Step 3: Implement `_validate_deselected_default_tools(self) -> Optional[str]`**

Same shape as `_validate_mcp_server_names` (line 992): define module-level
`VALID_DESELECTABLE_DEFAULT_TOOLS = {"get_repository_file_tree", "search_code_repo", "search_kb"}`
near the top of the file; the method returns a message naming every entry in
`self.deselected_default_tools` not in that set, else `None`. Add
`deselected_tools_error = self._validate_deselected_default_tools()` /
`if deselected_tools_error: return deselected_tools_error` into `validate_fields()` (line 928),
after the existing `mcp_names_error` check.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/codemie/rest_api/models/test_assistant_model.py -k deselected -v`
Expected: PASS.

Test-first: yes — `test_validate_rejects_unknown_deselected_tool` and
`test_validate_accepts_known_deselected_tools` are written before the validator exists.

---

## Task 4: Gate default tool assembly (chat-time)

**Files:**
- Modify: `src/codemie/service/tools/toolkit_service.py:1463-1481` (`_add_kb_tools`)
- Modify: `src/codemie/service/tools/toolkit_service.py:1550-1616` (`_add_code_tools`)
- Test: `tests/codemie/service/tools/test_toolkit_service.py`

**Interfaces:**
- Consumes: `assistant.deselected_default_tools` (Task 2),
  `VALID_DESELECTABLE_DEFAULT_TOOLS` names (`get_repository_file_tree`, `search_code_repo`,
  `search_kb`).
- Produces: no change to either method's signature; both still append to the `tools` list
  passed in.

- [ ] **Step 1: Write the failing tests**

```python
def test_add_kb_tools_skips_search_kb_when_deselected():
    # assistant.deselected_default_tools = ["search_kb"]; kb_index resolves normally
    # -> tools stays empty, no SearchKBTool appended

def test_add_code_tools_skips_repo_tree_when_deselected():
    # assistant.deselected_default_tools = ["get_repository_file_tree"]
    # -> neither standard nor V2 repo tree tool is appended, even if toolkits selects the V2 variant

def test_add_code_tools_skips_search_code_when_deselected():
    # assistant.deselected_default_tools = ["search_code_repo"]
    # -> neither standard nor V2 search tool is appended; other default tools unaffected

def test_add_code_tools_keeps_defaults_when_nothing_deselected():
    # assistant.deselected_default_tools == [] (legacy/default)
    # -> behavior matches current test expectations exactly (AC4)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/codemie/service/tools/test_toolkit_service.py -k deselect -v`
Expected: FAIL — tool still appended regardless of `deselected_default_tools`.

- [ ] **Step 3: Gate `_add_kb_tools`**

Before `tools.extend(KBToolkit.get_tools(...))` (line 1481), add
`if SEARCH_KB_TOOL_NAME in assistant.deselected_default_tools: return` (or wrap the extend in
the negated condition). Use the literal `"search_kb"` or import `SEARCH_KB_TOOL.name` from
`codemie.agents.tools.kb.search_kb` — prefer the existing `SEARCH_KB_TOOL` import if already
available in this file, otherwise the literal matching Task 3's canonical set.

- [ ] **Step 4: Gate `_add_code_tools`**

At lines 1598-1616, change the two `if repo_tree_with_filtering or not exclude_extra_context_tools:`
/ `if code_search_with_filtering or not exclude_extra_context_tools:` conditions to also require
`REPO_TREE_TOOL.name not in assistant.deselected_default_tools` /
`CODE_SEARCH_TOOL.name not in assistant.deselected_default_tools` respectively — the deselect
check applies regardless of which variant (`toolkits`-selected V2 or default standard) would
otherwise be added, per spec ("independent of, and additional to, the existing
`toolkits`/`exclude_extra_context_tools` gating").

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/codemie/service/tools/test_toolkit_service.py -k deselect -v`
Expected: PASS. Then run the full existing file to confirm no regression:
`pytest tests/codemie/service/tools/test_toolkit_service.py -v`.

Test-first: yes — the four tests above are written before the gating conditions exist and fail
at Step 2.

---

## Task 5: Gate direct tool invoke

**Files:**
- Modify: `src/codemie/service/tools/tool_execution_service.py:302-325` (`_get_context_tools`)
- Test: `tests/codemie/service/tools/test_tool_execution_invoke.py`

**Interfaces:**
- Consumes: `assistant.deselected_default_tools`, `assistant.context` (now iterated in full, not
  `next(iter(...))`), `TOOL_NOT_FOUND_ERROR` from `codemie.service.tools.tool_service`.
- Produces: no signature change to `_get_context_tools`; raises `ValueError` before dispatching
  to `_get_kb_tools`/`_get_code_tools` when the tool is deselected.

- [ ] **Step 1: Write the failing tests**

```python
def test_invoke_rejects_deselected_search_kb_across_datasources():
    # assistant.context = [Context(CODE, "repo1"), Context(KNOWLEDGE_BASE, "kb1")]
    # assistant.deselected_default_tools = ["search_kb"]
    # tool_name = "search_kb" -> raises ValueError naming the tool, even though the first
    # context entry is CODE, not KNOWLEDGE_BASE

def test_invoke_rejects_deselected_repo_tree_v2_variant():
    # deselected_default_tools = ["get_repository_file_tree"], tool_name = "get_repository_file_tree_v2"
    # -> rejected: the standard name in the deselect list covers both variants (AC2)

def test_invoke_allows_non_deselected_tool_unchanged():
    # deselected_default_tools = [], tool_name = "search_code_repo" -> resolves exactly as today
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/codemie/service/tools/test_tool_execution_invoke.py -k deselect -v`
Expected: FAIL — tool is still built/resolved.

- [ ] **Step 3: Implement the gate**

At the top of `_get_context_tools` (line 302), after the `ToolSet.VCS` branch and before
`if not assistant.context: return` (line 308), add a check: map `tool_name` to its canonical
deselectable name (`get_repository_file_tree` for both `get_repository_file_tree` and
`get_repository_file_tree_v2`; `search_code_repo` for both `search_code_repo` and
`search_code_repo_v2`; `search_kb` for `search_kb`). If the canonical name is in
`assistant.deselected_default_tools`, `raise ValueError(TOOL_NOT_FOUND_ERROR.format(tool_name=tool_name, available_tools=[]))`.
This check does not depend on which `Context` entry `next(iter(...))` would have picked — it is
keyed on `assistant.deselected_default_tools` alone, so it already covers every datasource (D6)
without iterating `assistant.context`. Add the `TOOL_NOT_FOUND_ERROR` import from
`codemie.service.tools.tool_service`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/codemie/service/tools/test_tool_execution_invoke.py -k deselect -v`
Expected: PASS. Then run
`pytest tests/codemie/service/tools/test_tool_execution_invoke.py tests/codemie/service/tools/test_tool_execution_service.py -v`
to confirm no regression on the existing first-context-only behavior for non-deselected tools.

Test-first: yes — the three tests above are written before the gate exists and fail at Step 2.

---

## Task 6: Carry the field through versioning, restore, and migration

**Files:**
- Modify: `src/codemie/service/assistant/assistant_version_compare_service.py:61-82` (`candidate`
  dict in `has_configuration_changes`)
- Modify: `src/codemie/service/assistant/assistant_version_service.py:54-76`
  (`create_initial_version`), `:112-134` (`create_new_version`'s `versioned()` fields),
  `:276-300` (`rollback_to_version`'s `new_config`), `:326-343`
  (`update_assistant_config_fields`)
- Modify: `src/codemie/service/assistant/assistant_version_migration_service.py:68-88`
  (`_create_single_version_from_current`), `:101-124` (`_create_version_from_history`),
  `:131-151` (`_create_version_from_current`)
- Test: `tests/codemie/service/assistant/` (extend the existing version/compare/migration test
  files for this package; create one if none currently cover these methods — the analysis found
  no located tests here)

**Interfaces:**
- Consumes: `Assistant.deselected_default_tools`, `AssistantConfiguration.deselected_default_tools`,
  `AssistantRequest.deselected_default_tools` (Task 2).
- Produces: no signature changes; every site that already copies `context`/`toolkits` between
  `Assistant`, `AssistantConfiguration`, and `AssistantRequest` now also copies
  `deselected_default_tools`.

- [ ] **Step 1: Write the failing tests**

```python
def test_has_configuration_changes_true_when_only_deselected_tools_change():
    # current_config.deselected_default_tools == []; request sets deselected_default_tools=["search_kb"]
    # and request.model_fields_set includes 'deselected_default_tools' -> returns True (AC7)

def test_create_new_version_carries_deselected_tools():
    # request.deselected_default_tools == ["search_kb"] -> resulting AssistantConfiguration has it

def test_rollback_restores_deselected_tools_onto_assistant():
    # target_config.deselected_default_tools == ["get_repository_file_tree"]
    # -> after rollback_to_version, assistant.deselected_default_tools == ["get_repository_file_tree"]

def test_migration_carries_deselected_tools_from_assistant():
    # assistant.deselected_default_tools == ["search_code_repo"], no system_prompt_history
    # -> created AssistantConfiguration.deselected_default_tools == ["search_code_repo"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
`pytest tests/codemie/service/assistant/ -k deselected -v`
Expected: FAIL — each site drops the field (absent from `candidate`/constructor calls).

- [ ] **Step 3: Add the field to `AssistantVersionCompareService`**

In the `candidate` dict (around line 70), add
`'deselected_default_tools': request.deselected_default_tools,` next to `'toolkits'`. No change
to `_prepare_for_comparison` — it already includes every field via `config.model_dump()` minus
the excluded metadata set, so it picks up the new column automatically.

- [ ] **Step 4: Add the field everywhere `AssistantConfiguration(...)` is constructed**

In `assistant_version_service.py`: `create_initial_version` — add
`deselected_default_tools=request.deselected_default_tools,`. `create_new_version` — add
`deselected_default_tools=versioned('deselected_default_tools'),` using the existing
`versioned()` helper so an omitted field preserves the current value. `rollback_to_version`'s
`new_config` — add `deselected_default_tools=target_config.deselected_default_tools,`.
`update_assistant_config_fields` — add
`assistant.deselected_default_tools = config.deselected_default_tools`.

- [ ] **Step 5: Add the field everywhere in `assistant_version_migration_service.py`**

In all three `_create_*` methods, add
`deselected_default_tools=assistant.deselected_default_tools,` alongside the existing
`context=assistant.context, toolkits=assistant.toolkits,` lines.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/codemie/service/assistant/ -k deselected -v`
Expected: PASS. Then run the package's existing version/compare/migration test files in full to
confirm no regression.

Test-first: yes — the four tests above are written before the field is threaded through and fail
at Step 2.

---

## Task 7: Move default tools into the UI listing

**Files:**
- Modify: `src/codemie/agents/tools/code/code_toolkit.py:44-67` (`CodeToolkitUI`,
  `get_tools_api_info`)
- Modify: `src/codemie/service/tools/tools_info_service.py:82-83` (KB listing gate)
- Test: `tests/codemie/service/tools/test_tools_info_script_callable.py` (or the file currently
  covering `get_tools_info`/`_merge_code_toolkit`)

**Interfaces:**
- Consumes: `REPO_TREE_TOOL`, `CODE_SEARCH_TOOL` (`tools_vars.py`), `SEARCH_KB_TOOL`
  (`search_kb.py`).
- Produces: no signature change; `CodeToolkitUI.tools` now includes the two standard tools;
  `get_tools_api_info` no longer needs to append them separately; KB listing appends
  `KBToolkit.get_tools_ui_info()` for both `show_for_ui=True` and `False` when Elasticsearch is
  configured.

- [ ] **Step 1: Write the failing tests**

```python
def test_code_toolkit_ui_lists_standard_repo_tree_and_search():
    ui = CodeToolkit.get_tools_ui_info()
    names = {t["name"] for t in ui["tools"]}
    assert "get_repository_file_tree" in names
    assert "search_code_repo" in names

def test_tools_info_lists_search_kb_for_ui_true_and_false():
    # with ElasticSearchClient configured (mocked), get_tools_info(show_for_ui=True) and
    # get_tools_info(show_for_ui=False) both include a toolkit entry with SEARCH_KB_TOOL's name
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/codemie/service/tools/ -k "ui_lists or search_kb_for_ui" -v`
Expected: FAIL — `get_repository_file_tree`/`search_code_repo` absent from the UI listing;
`search_kb` absent when `show_for_ui=True`.

- [ ] **Step 3: Move the two tools into `CodeToolkitUI`**

In `code_toolkit.py:46-52`, add `Tool.from_metadata(REPO_TREE_TOOL)` and
`Tool.from_metadata(CODE_SEARCH_TOOL)` to the `tools` list. Simplify `get_tools_api_info`
(line 60-67) since the UI list now already contains both — it can return `CodeToolkitUI().model_dump()`
directly (same as `get_tools_ui_info`), or keep the method as a thin alias; do not duplicate the
two tools a second time.

- [ ] **Step 4: Always list the KB toolkit**

In `tools_info_service.py:82-83`, change
`if ElasticSearchClient.is_configured() and not show_for_ui:` to
`if ElasticSearchClient.is_configured():` so `KBToolkit.get_tools_ui_info()` is appended for both
`show_for_ui` values.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/codemie/service/tools/ -k "ui_lists or search_kb_for_ui" -v`
Expected: PASS. Then run the package's existing `tools_info_service`/`code_toolkit`/
`toolkit_lookup_service` test files to confirm nothing that asserted the old API-only/UI-only
split regresses.

Test-first: yes — the two tests above are written before the listing move and fail at Step 2.

---

## Task 8: Remove "call repo tree first" language

**Files:**
- Modify: `src/codemie/agents/tools/code/tools_vars.py:17-40` (`CODE_SEARCH_TOOL.description`,
  `.react_description`)
- Test: `tests/codemie/agents/tools/code/test_code_tools_health.py` (or a new small test next to
  it)

**Interfaces:**
- Consumes: none new.
- Produces: no signature change — only the string content of `CODE_SEARCH_TOOL.description` and
  `.react_description` changes; `REPO_TREE_TOOL_V2`'s own "must be first" clause (line 138) is
  untouched (spec: it only fires when the V2 variant is itself selected).

- [ ] **Step 1: Write the failing test**

```python
def test_code_search_tool_description_does_not_reference_repo_tree():
    assert "repository tree" not in CODE_SEARCH_TOOL.description.lower()
    assert "repository tree" not in CODE_SEARCH_TOOL.react_description.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/agents/tools/code/ -k repo_tree -v`
Expected: FAIL — both strings currently say "from repository tree".

- [ ] **Step 3: Edit the description text**

In `CODE_SEARCH_TOOL.description` (line 26) and `.react_description` (line 38), reword "is a
list of relevant file paths from repository tree which might be relevant to user input" to drop
the "from repository tree" clause (e.g. "is a list of relevant file paths which might be relevant
to user input"), preserving the rest of the parameter documentation unchanged. Grep
`src/codemie/agents/tools/code/` and the KB tool description once more for any other
"repository tree"/"call ... first" phrasing the Stage-1 analysis flagged as unread, and apply the
same edit if found (AC5's open risk).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/codemie/agents/tools/code/ -k repo_tree -v`
Expected: PASS.

Test-first: yes — the description-content test above is written before the edit and fails at
Step 2.

---

## Task 9: Workflow-referenced assistants keep the deselect list

**Files:**
- Test: `tests/codemie/service/assistant/test_virtual_assistant_service.py` (or the closest
  existing workflow-execution test file that loads a real `Assistant` and runs it through
  `AssistantService.add_context_tools`)

**Interfaces:**
- Consumes: `Assistant.deselected_default_tools` (Task 2), `ToolkitService.add_context_tools`
  (Task 4 gating).
- Produces: no production code change — `VirtualAssistant`/`VirtualIdeAssistant` already inherit
  the field from `AssistantBase` (Task 2), and a workflow step that references an existing,
  persisted assistant loads that `Assistant` record directly and runs it through the same
  `add_context_tools` path Task 4 already gates. This task is the regression test that pins that
  down, since the Stage-1 analysis found no existing coverage here.

- [ ] **Step 1: Write the failing test (fails only if the pass-through regresses)**

```python
def test_workflow_referenced_assistant_keeps_deselected_tools():
    # Build/save a real Assistant with a CODE context and
    # deselected_default_tools=["get_repository_file_tree"]; load it the way a workflow step
    # that references an existing assistant_id would (Assistant.get_by_id), then call
    # ToolkitService.add_context_tools(assistant=loaded, ...) and assert no repo-tree tool
    # (standard or V2) is in the result.
```

- [ ] **Step 2: Run test to verify it currently passes once Task 4 and Task 2 are done**

Run: `pytest tests/codemie/service/assistant/test_virtual_assistant_service.py -k deselect -v`
Expected: PASS already, since Tasks 2 and 4 cover the mechanism — this step confirms it rather
than driving new production code. If it fails, that indicates a workflow-specific assistant
construction path that bypasses the loaded `Assistant` record's field; in that case, inspect
`VirtualAssistantService.create_from_virtual_asst_config` and add
`deselected_default_tools=...` to the `VirtualAssistantService.create(...)` call the same way
`toolkits`/`context` are passed, then re-run.

Test-first: yes — the test is written first; whether it requires a code change depends on
Step 2's outcome, which is itself the verification this task exists to produce.

---

## Negative-constraint pass

- **"The `_v2` variants are never valid entries"** (spec, Data shape) — honored by Task 3's
  `VALID_DESELECTABLE_DEFAULT_TOOLS` set, which contains only the three standard names; no task
  adds a V2 name to that set.
- **"No new validation" for standard+V2 co-selection (D1)** — Task 4 and Task 5 both gate only on
  `deselected_default_tools` membership, never on whether `toolkits` also selects the V2 variant;
  no task adds a conflict check between the two.
- **"No data migration of existing assistants' tool selection" / legacy assistants unaffected
  (D2, AC4)** — Task 1's migration adds the column nullable/defaulted, no backfill; Task 2's
  field defaults to `[]`; Task 4's `test_add_code_tools_keeps_defaults_when_nothing_deselected`
  pins that an empty list changes nothing.
- **"Smart tool selection ... is not affected"** — no task touches
  `toolkit_service.py`'s `get_tools()` line ~528 smart-selection condition; confirmed out of
  scope for every task above.
- **"Per-datasource tool selection" is explicitly out of scope** — Task 4's gate checks
  `assistant.deselected_default_tools` once per tool kind inside `_add_kb_tools`/`_add_code_tools`,
  not per `Context` entry; no task introduces a per-datasource override.
- **"The workflow YAML `exclude_extra_context_tools` setting ... stays as-is"** — Task 4 keeps
  the existing `exclude_extra_context_tools` condition intact and only adds the deselect check
  alongside it with `and`/independent logic, never replacing or inverting the existing flag.
- negative-constraints: all of the above are from the spec's Non-goals and Decisions sections;
  none were left unchecked.
