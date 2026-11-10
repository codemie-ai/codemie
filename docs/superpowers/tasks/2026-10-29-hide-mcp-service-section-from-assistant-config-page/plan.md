# Standalone MCP Servers Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `mcpConnect` availability (and the MCP runtime behavior behind it) reflect whether MCP Connect is actually usable, but only in standalone mode; non-standalone stays byte-for-byte unchanged.

**Architecture:** One new helper, `mcp_connect_available(cfg)`, becomes the single source of truth; three existing gates (`GET /v1/config` resolution, assistant toolkit MCP tools, workflow MCP tool node) switch from reading `MCP_CONNECT_ENABLED` directly to calling it, plus one new standalone-only startup warning.

**Tech Stack:** Python/FastAPI, pytest + `unittest.mock.patch`.

**Spec:** `docs/superpowers/tasks/2026-10-29-hide-mcp-service-section-from-assistant-config-page/spec.md`

## Global Constraints

- Backend-only (`codemie` repo); no UI code changes.
- Outside standalone (`config.STANDALONE is False`), every integration point must reduce to exactly today's `config.MCP_CONNECT_ENABLED` check — zero behavior change.
- No new feature flag; only `MCP_CONNECT_ENABLED`, `MCP_CONNECT_URL`, `STANDALONE`, and `mcpConnect` are used.
- `config.STANDALONE` (default `False`, `config.py:483`) and `main._run_standalone_startup()` (`main.py:516-520`, called once from `lifespan`) already exist on `main` (EPMCDME-16473 landed) — no dependency gap remains.
- Commit per task using the repository's existing convention.

## Review Focus

- Standalone + `MCP_CONNECT_ENABLED=False` (operator deliberately disabled) must **not** trigger the startup warning — only an empty/missing URL does. Covered in Task 5.
- `mcp_connect_available` must treat `MCP_CONNECT_URL` that is `None` the same as `""` (both falsy) — Covered in Task 1.
- The `resolve_components` post-filter must only drop the component when the id `"mcpConnect"` is actually present in `enabled` (no KeyError/no-op on absence) — Covered in Task 2.
- `tool_node.py`'s existing error message text ("MCP Connect is not enabled in configuration") stays unchanged so saved workflows see the same string, only the guard condition changes — Covered in Task 4.
- `toolkit_service.py`'s warning log must fire only on the skip path (standalone, unavailable), not on every assistant run — Covered in Task 3.

---

## Task 1: `mcp_connect_available` helper

**Files:**
- Modify: `src/codemie/configs/config.py` (add near `retrieval_available`, `config.py:1180-1181`)
- Test: `tests/codemie/configs/test_config.py` (create if absent, or extend if a suitable module-level test file exists)

**Interfaces:**
- Produces: `mcp_connect_available(cfg: Config) -> bool` — used by Tasks 2, 3, 4, 5.

- [ ] **Step 1: Write the failing tests**

```python
def test_non_standalone_ignores_url(make_config):
    cfg = make_config(STANDALONE=False, MCP_CONNECT_ENABLED=True, MCP_CONNECT_URL="")
    assert mcp_connect_available(cfg) is True

def test_standalone_requires_enabled_and_url(make_config):
    cfg = make_config(STANDALONE=True, MCP_CONNECT_ENABLED=True, MCP_CONNECT_URL="http://x")
    assert mcp_connect_available(cfg) is True
    assert mcp_connect_available(make_config(STANDALONE=True, MCP_CONNECT_ENABLED=False, MCP_CONNECT_URL="http://x")) is False
    assert mcp_connect_available(make_config(STANDALONE=True, MCP_CONNECT_ENABLED=True, MCP_CONNECT_URL="")) is False
```

Use whatever existing `Config`-construction helper `test_config.py`/`test_customer_config.py` already uses (e.g. a `Config(...)` instance or `SimpleNamespace`); do not invent a new fixture pattern.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/codemie/configs/test_config.py -k mcp_connect_available -v`
Expected: FAIL with `ImportError`/`AttributeError` (function not defined).

- [ ] **Step 3: Implement `mcp_connect_available(cfg: Config) -> bool` in `src/codemie/configs/config.py`**

```python
def mcp_connect_available(cfg: Config) -> bool:
    if not cfg.STANDALONE:
        return cfg.MCP_CONNECT_ENABLED
    return cfg.MCP_CONNECT_ENABLED and bool(cfg.MCP_CONNECT_URL)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/codemie/configs/test_config.py -k mcp_connect_available -v`
Expected: PASS

- [ ] **Step 5: Commit**

Test-first: yes — `mcp_connect_available` has no existing test; tests above fail with `ImportError` before Step 3.

---

## Task 2: Drop `mcpConnect` from `GET /v1/config` when unavailable

**Files:**
- Modify: `src/codemie/service/customer_config_service.py:157-171` (mirror the `LEADERBOARD_ENABLED` filter at lines 168-169; `_LEADERBOARD_COMPONENT_ID` is defined at line 61)
- Test: `tests/codemie/configs/test_customer_config.py` (near `PRE_EXISTING_SHIPPED_COMPONENT_IDS`, ~L828-918)

**Interfaces:**
- Consumes: `mcp_connect_available(cfg: Config) -> bool` (Task 1).

- [ ] **Step 1: Write the failing test**

Add a test that builds the shipped YAML components, calls `resolve_components()` with `config.STANDALONE=True`, `config.MCP_CONNECT_ENABLED=True`, `config.MCP_CONNECT_URL=""` patched, and asserts `"mcpConnect" not in [c.id for c in result]`. Add a second case with `STANDALONE=False` and the same disabled values asserting `"mcpConnect" in [c.id for c in result]` (non-standalone unchanged).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/configs/test_customer_config.py -k mcp_connect -v`
Expected: FAIL (`mcpConnect` still present when it shouldn't be).

- [ ] **Step 3: Add the post-filter in `resolve_components()`**

Right after the `LEADERBOARD_ENABLED` filter (`customer_config_service.py:168-169`), add an equivalent block: `if not mcp_connect_available(config): enabled = [c for c in enabled if c.id != "mcpConnect"]`. Import `mcp_connect_available` from `codemie.configs.config`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/codemie/configs/test_customer_config.py -k mcp_connect -v`
Expected: PASS

- [ ] **Step 5: Commit**

Test-first: yes — new test asserts `mcpConnect` is dropped in standalone+unavailable; fails against current code, which never filters it.

---

## Task 3: Toolkit service MCP tools gate + warning log

**Files:**
- Modify: `src/codemie/service/tools/toolkit_service.py:1055` (and the `else` branch that currently adds no tools)
- Test: `tests/codemie/service/tools/test_toolkit_service_headers.py` (existing cases mock `mock_config.MCP_CONNECT_ENABLED`, e.g. lines 88, 163, 237, 313, 391, 456)

**Interfaces:**
- Consumes: `mcp_connect_available(cfg: Config) -> bool` (Task 1).

- [ ] **Step 1: Write the failing test**

Add a case alongside the existing `MCP_CONNECT_ENABLED = False` test (`test_toolkit_service_headers.py:163`): set `mock_config.STANDALONE = True`, `mock_config.MCP_CONNECT_ENABLED = True`, `mock_config.MCP_CONNECT_URL = ""`, assert MCP tools are skipped (same assertions as the existing disabled case) and that a warning was logged (patch the module logger and assert `logger.warning` called).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/service/tools/test_toolkit_service_headers.py -k standalone -v`
Expected: FAIL — MCP tools still added because the code only checks `MCP_CONNECT_ENABLED`.

- [ ] **Step 3: Update the gate at `toolkit_service.py:1055`**

Change `if config.MCP_CONNECT_ENABLED:` to `if mcp_connect_available(config):`. In the corresponding `else` path, add `logger.warning("MCP Connect is not available in standalone mode; skipping MCP tools for assistant %s", assistant.id)` (or the nearest available identifier in scope) using the module's existing logger. Import `mcp_connect_available` from `codemie.configs.config`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/codemie/service/tools/test_toolkit_service_headers.py -k standalone -v`
Expected: PASS

- [ ] **Step 5: Commit**

Test-first: yes — new standalone+unavailable case expects tools skipped and a warning logged; fails until the gate and log line are added.

---

## Task 4: Workflow MCP tool node gate

**Files:**
- Modify: `src/codemie/workflows/nodes/tool_node.py:125-126`
- Test: `tests/codemie/workflows/test_tool_node_context.py` (existing MCP setup at lines 283, 830)

**Interfaces:**
- Consumes: `mcp_connect_available(cfg: Config) -> bool` (Task 1).

- [ ] **Step 1: Write the failing test**

Add a test setting `mock_config.STANDALONE = True`, `mock_config.MCP_CONNECT_ENABLED = True`, `mock_config.MCP_CONNECT_URL = ""`, and asserting the MCP tool execution raises `ValueError("MCP Connect is not enabled in configuration")` — same message as today's disabled-flag case.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/workflows/test_tool_node_context.py -k standalone -v`
Expected: FAIL — no `ValueError` raised, since `MCP_CONNECT_ENABLED` alone is `True`.

- [ ] **Step 3: Update the gate at `tool_node.py:125`**

Change `if not config.MCP_CONNECT_ENABLED:` to `if not mcp_connect_available(config):`. Keep the raised message text unchanged. Import `mcp_connect_available` from `codemie.configs.config`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/codemie/workflows/test_tool_node_context.py -k standalone -v`
Expected: PASS

- [ ] **Step 5: Commit**

Test-first: yes — new standalone+unavailable case expects the existing `ValueError`; fails until the condition changes.

---

## Task 5: Standalone-only startup warning for empty `MCP_CONNECT_URL`

**Files:**
- Modify: `src/codemie/rest_api/main.py:516-520` (`_run_standalone_startup`)
- Test: `tests/codemie/rest_api/test_standalone_startup.py` (existing `test_gate_logs_once_when_flag_on`, `test_gate_is_noop_when_flag_off`)

**Interfaces:**
- Consumes: nothing new (reads `config.MCP_CONNECT_ENABLED`, `config.MCP_CONNECT_URL` directly — this function already runs only when `config.STANDALONE`, so it does not need `mcp_connect_available`).

- [ ] **Step 1: Write the failing test**

```python
def test_warns_when_mcp_url_empty_and_enabled():
    with patch.object(main.config, "STANDALONE", True), \
         patch.object(main.config, "MCP_CONNECT_ENABLED", True), \
         patch.object(main.config, "MCP_CONNECT_URL", ""), \
         patch.object(main, "logger") as log:
        main._run_standalone_startup()
    log.warning.assert_called_once()

def test_no_warning_when_mcp_deliberately_disabled():
    with patch.object(main.config, "STANDALONE", True), \
         patch.object(main.config, "MCP_CONNECT_ENABLED", False), \
         patch.object(main.config, "MCP_CONNECT_URL", ""), \
         patch.object(main, "logger") as log:
        main._run_standalone_startup()
    log.warning.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/codemie/rest_api/test_standalone_startup.py -k mcp -v`
Expected: FAIL — `log.warning` never called today.

- [ ] **Step 3: Extend `_run_standalone_startup()` in `main.py`**

After the existing `logger.info("Standalone mode enabled")` line, add: `if config.MCP_CONNECT_ENABLED and not config.MCP_CONNECT_URL: logger.warning(...)` with a message listing the degraded features (MCP Servers on assistants, workflow MCP tools, the admin MCP page, the MCP settings tab, the MCP onboarding steps), per the spec.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/codemie/rest_api/test_standalone_startup.py -v`
Expected: PASS (including the two pre-existing tests, still unaffected).

- [ ] **Step 5: Commit**

Test-first: yes — two new cases (warn vs. no-warn-when-deliberately-disabled) fail against the current no-op body.

---

## Negative-constraints check

- "No new feature flag introduced" — no task adds one; all read `MCP_CONNECT_ENABLED`/`MCP_CONNECT_URL`/`STANDALONE`/`mcpConnect` only (Tasks 1-5).
- "Non-standalone ... unchanged" / "no new warning" — `mcp_connect_available` returns `cfg.MCP_CONNECT_ENABLED` unchanged when not standalone (Task 1); the Task 5 warning lives inside `_run_standalone_startup()`, which returns immediately when `not config.STANDALONE` (pre-existing guard, untouched).
- "No deployment-level MCP Connect key setting" — no task adds a key/token setting.
- "No user-facing chat notice" — Task 3 adds a server-side `logger.warning` only, no response/message content change.
- "UI code changes beyond tests" (none required) — no UI file in any task.
- "Removing or migrating MCP servers already saved" — no task touches assistant/workflow storage.
