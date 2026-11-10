# EPMCDME-16473 Standalone flag and gate Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Commit per task using the repository's existing convention.

**Goal:** Add a `STANDALONE` Config flag, one startup gate, and minimal-dependency ENV defaults in the standalone image.

**Architecture:** Plain pydantic bool field; self-gating `_run_standalone_startup()` in `main.py` called once from lifespan; ENV line in the standalone Dockerfile. Tests extend existing files.

**Spec:** `/home/siarhei/projects/codemie-dev/codemie/docs/superpowers/tasks/2026-10-28-EPMCDME-16473/spec.md`

## Global Constraints

- No existing `config.py` default changes (`ELASTIC_URL`, `MERMAID_SERVER_URL`, `MCP_CONNECT_URL`, `REDIS_HOST`, `MCP_CONNECT_ENABLED`).
- Flag not exposed in customer-config, `GET /v1/config`, or UI.
- Do not edit `standalone/README.md`, `.env.standalone`, entrypoint.sh, helm or other compose files.

## Review Focus

- `STANDALONE=maybe` raises `ValidationError` (Task 1 test).
- Flag off: gate does no logging and no side effects (Task 2 test).
- Gate invoked exactly once per startup (Task 2 test).

negative-constraints: honored — no exposure (Task 1 asserts no `standalone` key in customer-config output), no default changes (Task 1 guard test), no non-listed deployment files (Task 3 touches only Dockerfile and tests), no README edit (none), no consumer tolerance for empty URLs (none).

---

### Task 1: STANDALONE Config field

**Files:**
- Modify: `src/codemie/configs/config.py` (next to `SHAREPOINT_PKCE_ENABLED`, ~l.482)
- Test: `tests/codemie/configs/test_config.py`

**Interfaces:** Produces `Config.STANDALONE: bool = False`.

Test-first: yes — `Config(_env_file=None).STANDALONE` fails with AttributeError before the field exists.

- [ ] **Step 1:** Add tests in `test_config.py` using the existing `Config(_env_file=None)` + `monkeypatch.delenv` pattern: default is False; `STANDALONE=true` env gives True; `STANDALONE=true` in a tmp `.env` passed via `_env_file` gives True; `STANDALONE=maybe` raises `ValidationError`; defaults guard asserting `ELASTIC_URL=="http://localhost:9200"`, `MERMAID_SERVER_URL=="http://localhost:8082"`, `MCP_CONNECT_URL=="http://localhost:3000"`, `REDIS_HOST=="localhost"`; parametrized (flag on/off) check that `"standalone"` is not in the keys of the customer-config component resolution output (use the existing customer-config test fixtures/builder in `tests/codemie`; lowercase-compare keys).
- [ ] **Step 2:** Run `pytest tests/codemie/configs/test_config.py -v`; expect the new field tests to FAIL.
- [ ] **Step 3:** Add `STANDALONE: bool = False` to `Config`. No other change.
- [ ] **Step 4:** Re-run; expect PASS.

### Task 2: Startup gate

**Files:**
- Modify: `src/codemie/rest_api/main.py` (new function near `_check_sharepoint_pkce_redis`, l.503; call once in lifespan startup sequence ~l.1177-1189)
- Create: `tests/codemie/rest_api/test_standalone_startup.py`

**Interfaces:** Consumes `config.STANDALONE`. Produces `_run_standalone_startup() -> None`: returns immediately if flag false; otherwise logs one info line (the only standalone entry point; follow-up tickets add logic here).

Test-first: yes — importing `_run_standalone_startup` fails before it exists.

- [ ] **Step 1:** Write tests: with `config.STANDALONE` patched False, patched `logger` is not called; patched True, `logger.info` called once. Add a lifespan-order check by patching `_run_standalone_startup` is not required; instead assert via `inspect.getsource` of the lifespan function that `_run_standalone_startup()` appears exactly once.
- [ ] **Step 2:** Run `pytest tests/codemie/rest_api/test_standalone_startup.py -v`; expect FAIL.
- [ ] **Step 3:** Implement the function and the single lifespan call, placed after `_check_usage_attribution_settings()`.
- [ ] **Step 4:** Re-run; expect PASS.

### Task 3: Dockerfile ENV and compose contract

**Files:**
- Modify: `standalone/Dockerfile` (final stage, beside `ENV PYTHONPATH` l.119): `ENV STANDALONE=true ELASTIC_URL= MERMAID_SERVER_URL= MCP_CONNECT_URL= REDIS_HOST=`
- Test: `tests/codemie/standalone/test_standalone_compose_contract.py`

Test-first: yes — Dockerfile text assertion fails before the ENV line exists.

- [ ] **Step 1:** Extend the contract test: Dockerfile text contains `STANDALONE=true` and each of the four keys with an empty value (regex `\bKEY=(\s|$|\\)`); the five keys (`STANDALONE`, `ELASTIC_URL`, `MERMAID_SERVER_URL`, `MCP_CONNECT_URL`, `REDIS_HOST`) are absent from compose `environment` entries (keys via `split("=",1)[0]`) and from `standalone/.env.standalone.example`.
- [ ] **Step 2:** Run `pytest tests/codemie/standalone/test_standalone_compose_contract.py -v`; expect Dockerfile assertion FAIL.
- [ ] **Step 3:** Add the ENV line to the Dockerfile.
- [ ] **Step 4:** Re-run; expect PASS.
