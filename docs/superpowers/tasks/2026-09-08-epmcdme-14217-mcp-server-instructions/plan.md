# EPMCDME-14217 part 2 — Apply MCP server instructions to the assistant prompt

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Take the `instructions` an MCP server returns on `tools/list` (delivered by the paired `codemie-mcp-connect-service` MR) and place it in the assistant's system prompt before any MCP tool is called, for both Assistants and Skills.

**Architecture:** The bridge now returns `instructions` alongside `tools`. `MCPListToolsResponse` gains the field, and a new typed client method returns both; `list_tools` keeps its current signature so its existing callers are untouched. The value rides on `MCPToolkit`, so a cached toolkit keeps it. `get_mcp_server_tools` cannot change its return type — it has five callers outside the assistant path — so the collected instructions travel to the prompt through a contextvar side channel, exactly the pattern `mcp/auth_warnings.py` already uses. `AssistantService._prepare_system_prompt` reads that channel and appends one delimited, clearly-untrusted section. It runs after `ToolkitService.get_tools`, so the values are present by then.

**Tech Stack:** Python 3.12, FastAPI, SQLModel, pydantic v2, LangGraph, pytest.

## Global Constraints

- Branch `EPMCDME-14217_mcp-server-instructions`. Commits: `EPMCDME-14217: <Imperative description>`.
- The JSON key from the bridge is exactly `instructions`, top level of the `tools/list` body. This is the cross-repo contract with `codemie-mcp-connect-service` MR !268.
- `MCPConnectClient.list_tools` keeps its `list[MCPToolDefinition]` return type; `get_mcp_server_tools` keeps its `list[MCPTool]` return type. Neither may change — 14 and 5 external call sites respectively.
- Server text is untrusted third-party content and this is the first such content on the system-prompt path. It must be appended **after** Jinja rendering (never fed into template input, which would let it reach `render_system_prompt_with_vars`), wrapped in a delimiter that names its origin, and length-capped independently of the bridge's own cap.
- Skills need no separate work: `ToolkitService._merge_skill_mcp_servers` unions skill MCP servers into the assistant's list before tool resolution, and skills have no separate prompt builder. One injection covers both acceptance criteria.
- A server that sends no instructions must produce a byte-identical system prompt to before this change.

---

### Task 1: Receive `instructions` from the bridge

**Files:**
- Modify: `src/codemie/service/mcp/models.py:378` (`MCPListToolsResponse`)
- Modify: `src/codemie/service/mcp/client.py:131` (`list_tools`)
- Test: `tests/codemie/service/mcp/test_client_instructions.py`

**Interfaces:**
- Produces: `MCPListToolsResponse.instructions: str | None`; `MCPConnectClient.list_tools_with_instructions(server_config, execution_context) -> MCPListToolsResponse`. `list_tools` delegates to it and returns `.tools`, so its own signature is unchanged.

- [ ] **Step 1: Write the failing test** — assert `list_tools_with_instructions` parses the field, that a response without it yields `None`, and that `list_tools` still returns only the tool list.

- [ ] **Step 2: Run it** — `poetry run pytest tests/codemie/service/mcp/test_client_instructions.py -v` → FAIL (`AttributeError: 'MCPConnectClient' object has no attribute 'list_tools_with_instructions'`).

- [ ] **Step 3: Implement** — add the field to the model; extract the body of `list_tools` into `list_tools_with_instructions` returning the validated `MCPListToolsResponse`; `list_tools` becomes a two-line delegation.

- [ ] **Step 4: Run it** → PASS.

- [ ] **Step 5: Commit** — `EPMCDME-14217: Parse server instructions from the MCP bridge response`

**Test-first: yes** — the new client method does not exist.

---

### Task 2: Carry the value on the toolkit

**Files:**
- Modify: `src/codemie/service/mcp/toolkit.py` (`MCPToolkit` at :478, `MCPToolkitFactory.create_toolkit` at :724, `_list_tools_with_init_timeout` at :800)
- Test: `tests/codemie/service/mcp/test_toolkit_instructions.py`

**Interfaces:**
- Produces: `MCPToolkit.instructions: str | None = None`, populated at creation. A cached toolkit therefore keeps it, satisfying "reuse the already retrieved instructions".

- [ ] **Step 1: Write the failing test** — a factory whose client returns instructions produces a toolkit carrying them; a cached toolkit still carries them on the second call; no instructions → `None`.

- [ ] **Step 2: Run it** → FAIL (unknown field `instructions`).

- [ ] **Step 3: Implement** — `_list_tools_with_init_timeout` calls `list_tools_with_instructions` and returns the response; `create_toolkit` passes `instructions=response.instructions` into `MCPToolkit`.

- [ ] **Step 4: Run it** → PASS.

- [ ] **Step 5: Commit** — `EPMCDME-14217: Carry server instructions on the MCP toolkit`

**Test-first: yes.**

---

### Task 3: Side channel from tool loading to the prompt

**Files:**
- Create: `src/codemie/service/mcp/server_instructions.py`
- Modify: `src/codemie/service/mcp/toolkit_service.py` (`get_mcp_server_tools` at :185 — clear the channel; `_process_single_mcp_server` at :756 — record)
- Test: `tests/codemie/service/mcp/test_server_instructions.py`

**Interfaces:**
- Produces: `record_server_instructions(server_name, instructions)`, `get_server_instructions(*, clear=True) -> list[MCPServerInstructions]`, `clear_server_instructions()`. `MCPServerInstructions` is a frozen dataclass of `server_name` and `instructions`. Modelled directly on `mcp/auth_warnings.py`.

- [ ] **Step 1: Write the failing test** — record/get/clear round-trip; blank and non-string values are ignored; `get_mcp_server_tools` clears the channel at entry so one request never sees a previous request's values; a server whose toolkit carries instructions is recorded under `mcp_server.name`.

- [ ] **Step 2: Run it** → FAIL (module does not exist).

- [ ] **Step 3: Implement** — the contextvar module; `clear_server_instructions()` next to the existing `clear_mcp_auth_warnings()` call in `get_mcp_server_tools`; `record_server_instructions(mcp_server.name, toolkit.instructions)` in `_process_single_mcp_server` right after `toolkit.get_tools()`.

- [ ] **Step 4: Run it** → PASS.

- [ ] **Step 5: Commit** — `EPMCDME-14217: Collect MCP server instructions during tool loading`

**Test-first: yes.**

---

### Task 4: Render the section into the system prompt

**Files:**
- Modify: `src/codemie/service/mcp/server_instructions.py` (add `render_instructions_section`)
- Modify: `src/codemie/service/assistant_service.py:435` (`_prepare_system_prompt`)
- Test: `tests/codemie/service/mcp/test_server_instructions_rendering.py`, `tests/codemie/service/test_assistant_service_mcp_instructions.py`

**Interfaces:**
- Produces: `render_instructions_section(entries) -> str | None`. Appended last in `_prepare_system_prompt`, after every existing suffix and after prompt rendering.

- [ ] **Step 1: Write the failing test** — an assistant whose tool loading recorded instructions gets them appended; no recorded instructions leaves the prompt byte-identical; two servers produce two labelled blocks; the block names its server and marks the content as server-supplied; text is capped; control characters are stripped; a payload containing prompt-injection phrasing and fake delimiters cannot break out of the block.

- [ ] **Step 2: Run it** → FAIL.

- [ ] **Step 3: Implement** — render each entry inside an explicit delimited block that names the server and states the text comes from that server, following the `render_prompt_section()` precedent used for A2UI (`_prepare_system_prompt:463-468`). Strip control characters, neutralize any delimiter sequence appearing inside the payload, and cap each block at `MCP_SERVER_INSTRUCTIONS_MAX_CHARS` (config default 4000).

- [ ] **Step 4: Run it** → PASS.

- [ ] **Step 5: Commit** — `EPMCDME-14217: Apply MCP server instructions to the assistant system prompt`

**Test-first: yes.**

---

### Task 5: Regression coverage for both execution paths

**Files:**
- Modify/create: `tests/codemie/service/test_assistant_service_mcp_instructions.py`
- Test: same

**Interfaces:**
- Consumes: everything above. This is the acceptance criterion the ticket names explicitly: "Automated regression coverage validates that MCP server instructions are applied before tool calls for both Assistant and Skill execution paths."

- [ ] **Step 1: Write the failing test** — assert the ordering contract directly: instructions recorded during `ToolkitService.get_tools` are present in the prompt `build_agent` hands to the agent, for an assistant-attached server and for a skill-attached server (the skill path through `_merge_skill_mcp_servers`).

- [ ] **Step 2: Run it** → FAIL before Tasks 1-4 land; PASS after.

- [ ] **Step 3: Implement** — no source change expected. If the skill path fails, the union in `_merge_skill_mcp_servers` is not reaching the recorder — fix the source, not the test.

- [ ] **Step 4: Run it** → PASS.

- [ ] **Step 5: Commit** — `EPMCDME-14217: Cover assistant and skill instruction paths`

**Test-first: yes.**

---

### Task 6: Quality gates

- [ ] **Step 1:** `make lint` (or the project's configured ruff invocation).
- [ ] **Step 2:** `poetry run pytest tests/codemie/service/mcp tests/codemie/service/test_assistant_service*.py -q`.
- [ ] **Step 3:** full affected-suite run.
- [ ] **Step 4:** `make test-harness` — required before the MR by project rule.
- [ ] **Step 5:** commit any fixes.

**Test-first: no** — verification only.

---

## Deliberately out of scope

- **Workflows.** `src/codemie/workflows/nodes/tool_node.py:134` is a second consumer of `get_mcp_server_tools` with its own prompt path. The ticket's acceptance criteria name Assistants and Skills only, and the contextvar is cleared per tool-loading call, so workflows are unaffected rather than half-supported. Worth a follow-up ticket.
- **Persisting instructions** in the `mcp_configs` table. They are fetched live at handshake; storing them would add a staleness problem this ticket does not ask to solve.
- **A pre-flight `{sysparm_help=true}` tool call.** The ticket's Description mentions that convention, but it is the Support Portal server's own guidance and belongs in the text this change now delivers. Recorded in `decisions.jsonl`.
