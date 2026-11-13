# Turn Katas Off by Default Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Ship `features:katas` disabled and refuse every kata API route with 404 while it is off.

**Architecture:** One guard `_ensure_katas_enabled` in `ai_kata.py`, attached as a router-level dependency after `authenticate` on both kata routers. YAML entry in both shipped configs; seed untouched.

**Spec:** `docs/superpowers/tasks/2026-11-04-turn-katas-off-by-default/spec.md`

Commit per task using the repository's existing convention.

## Global Constraints

- Response: 404, message `Katas feature is not available`, with `details` and `help` fields as in `cli_analytics.py:154-161` (the 404 style; cost centers/teams use 403 in code at `cost_center_service.py:63-69`, which is a different, non-followed convention).
- Check is `customer_config.is_feature_enabled("katas")`; missing component = off, no error.
- Do not touch `main.py` registration or `_initialize_preconfigured_content` (`main.py:544-553`), the leaderboard, or add a `SettingDeclaration`.

## Task 1: Shipped config entry, env override, docs

**Files:**
- Modify: `config/customer/customer-config.yaml` and `standalone/external-config/customer-config.yaml` (add after the `features:cliAnalytics` block, line ~119+)
- Modify: `tests/codemie/configs/test_customer_config.py:852` (add `"features:katas"` to the enumerated list)
- Modify: `standalone/README.md` (short paragraph near line 262)

**Interfaces:** Produces component id `features:katas`, consumed by Task 2.

Test-first: yes — a test loading the shipped YAML asserts `features:katas` exists with `enabled` false and `is_feature_enabled("katas")` is false; with `FEATURE_KATAS=true` it is true; with a config lacking the component it is false without raising. The enumerated-list test fails until the entry is added.

- [ ] Add tests next to the existing shipped-config tests in `tests/codemie/configs/test_customer_config.py` (follow how the file loads config and patches env); update the list at line 852. Run `pytest tests/codemie/configs/test_customer_config.py -q`, expect FAIL.
- [ ] Add to both YAMLs an entry `id: "features:katas"`, `settings`: `enabled: false`, `name: "Katas"`, `description` (one line on AI katas learning feature), `availableForExternal: false`, same shape as cliAnalytics.
- [ ] README paragraph: katas turn off on upgrade; enable with `FEATURE_KATAS=true` on the shipped config, or a `features:katas` entry with `enabled: true` in a customer config (note the env override does not apply to a custom config lacking the entry). Put the same text in the MR description when the MR is made.
- [ ] Rerun the tests, expect PASS.

## Task 2: Router-level guard on both kata routers

**Files:**
- Modify: `src/codemie/rest_api/routers/ai_kata.py:71-75` (define `_ensure_katas_enabled` above `router`, add `Depends(_ensure_katas_enabled)` after `Depends(authenticate)`; import `customer_config` as `cli_analytics.py` does)
- Modify: `src/codemie/rest_api/routers/user_kata_progress.py:34-38` (import the guard from `ai_kata`, add to `dependencies` after `authenticate`)
- Create: `tests/codemie/rest_api/routers/test_kata_feature_gate.py`

**Interfaces:**
- Consumes: component `features:katas` (Task 1); `customer_config.is_feature_enabled`.
- Produces: `def _ensure_katas_enabled() -> None` raising `ExtendedHTTPException(code=status.HTTP_404_NOT_FOUND, message="Katas feature is not available", details=..., help=...)`.

Test-first: yes — gate test builds a FastAPI app including both routers, overrides `authenticate`, iterates `router.routes` of both routers (so later routes are covered automatically; do not hand-list), and with `is_feature_enabled` patched False asserts every route returns 404 with the message and that `AIKataService`/progress service/interaction service mocks are not called; with it patched True asserts the guard does not 404 (service mocked, status is not the gate's 404). Also an unauthenticated call stays 401 when off (authenticate runs first). Use TestClient since direct handler calls skip router dependencies.

- [ ] Write the gate test; run `pytest tests/codemie/rest_api/routers/test_kata_feature_gate.py -q`, expect FAIL.
- [ ] Implement the guard and attach it to both routers; run the gate test, expect PASS.
- [ ] Run `pytest tests/codemie/rest_api/routers/test_ai_kata.py tests/codemie/rest_api/routers/test_user_kata_progress.py -q`, expect PASS (these call handlers directly, unaffected).
- [ ] Add a test (same file) asserting `import_preconfigured_katas` is still invoked by `_initialize_preconfigured_content` with default config (AC4); expect PASS without code change. Test-first: no for this step, it pins existing behavior.

## Negative-constraint pass

Honored: no router unregistering (main.py untouched; Task 2), seed untouched (Task 2 pinning test), no admin `SettingDeclaration` (none added), no leaderboard change, no frontend change, no new shared module (guard lives in `ai_kata.py`).
