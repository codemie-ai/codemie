# Technical Research

**Task**: mermaid rendering standalone feature-flag
**Generated**: 2026-10-30
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-16477: Backend Mermaid Availability in Standalone

IMPORTANT CORRECTION vs any prior research: this branch is now based on EPMCDME-15683_standalone-codemie-improvements, which already contains EPMCDME-16473 "Add STANDALONE flag and standalone image defaults" (commit 243f75eb3). `STANDALONE: bool = False` now exists at src/codemie/configs/config.py:483. Research this branch's actual code for how STANDALONE is used elsewhere (e.g. other features gated by it, startup diagnostics, the standalone Dockerfile/env defaults) so the plan can follow the same pattern for Mermaid.

Story: As a user or admin of a standalone CodeMie installation, I want the backend to report whether Mermaid rendering is usable and to explain rendering failures accurately, so that the UI can avoid broken diagram paths and admins know what to configure.

Background: In standalone mode, the backend tries to reach a renderer that does not exist and then blames the user's diagram syntax. The UI also has no way to know rendering is unavailable. The backend must decide availability, publish it to the UI, warn admins at startup, and classify errors correctly.

Context:
- Mermaid rendering depends on an external renderer: a Mermaid server (defaults to a local address) or the public mermaid.ink if an admin opts in. The standalone image from EPMCDME-16473 leaves the server address empty (check standalone/Dockerfile and standalone env defaults on this branch for the actual empty-default mechanism).
- All rendering failures currently surface as "Mermaid diagram configuration error" with a syntax hint, so connection failures and renderer rejections look the same.
- No Mermaid feature flag exists yet.

Acceptance Criteria:
1. Non-standalone: behavior is unchanged, the default renderer address is kept, and Mermaid is reported as available.
2. Standalone with no Mermaid server address and no mermaid.ink: startup logs a warning naming the missing configuration and each degraded feature (chat diagrams, workflow graph preview and stored workflow diagram image, conversation export diagrams). Startup continues.
3. Standalone with a server address or mermaid.ink configured: no Mermaid warning at startup.
4. Standalone, application settings requested: Mermaid is reported available only if the feature flag is enabled and a renderer is configured; otherwise unavailable.
5. Standalone, flag enabled, reachable renderer, valid diagram: renders successfully.
6. Configured but unreachable renderer: the error says the renderer is unavailable and does not ask the user to check the syntax.
7. Reachable renderer rejects a diagram: the error identifies an invalid diagram and asks the user to check the syntax.
8. Rendering not configured, diagram requested via API: the error says rendering is not configured, distinct from "unavailable" and "invalid diagram".
9. Rendering not configured, workflow create/update: the save succeeds as today.

Out of Scope: UI changes (sibling ticket); removing the default server address for non-standalone; changing conversation export behavior; bundling a renderer in the standalone image.

Decisions:
- D1: Default renderer address kept for non-standalone; "not configured" applies only in standalone.
- D2: mermaid.ink counts as configured.
- D3: A new Mermaid feature flag will be added.
- D4: Outside standalone, Mermaid is always available, as today.
- D5: Availability is published to the UI as a runtime feature in application settings, the same way knowledge-base availability is published (see customer_config.py / knowledgeBases pattern).

Linked: EPMCDME-15693 "Mermaid diagram requests fail in the OSS standalone environment" (related, Blocked).

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/configs/config.py:75,83-85,483` — `ELASTIC_URL = "http://localhost:9200"`, `MERMAID_SERVER_URL = "http://localhost:8082"`, `MERMAID_SERVER_TIMEOUT = 50`, `MERMAID_USE_MERMAID_INC = False`, `STANDALONE: bool = False`. `retrieval_available(cfg)` (line 1180) is a module-level helper `cfg.RETRIEVAL_BACKEND != "none"` — the precedent for a small "is this backend usable" function.
- `src/codemie/service/file_service/mermaid_service.py` — `MermaidService.draw_mermaid()`. Routes to `_make_internal_request` (POST to `{MERMAID_SERVER_URL}/generate`) or `_make_external_request` (GET to `mermaid.ink`, chosen by `config.MERMAID_USE_MERMAID_INC`). All exceptions (connection errors, non-200, bad content-type) are caught in one `except Exception` and re-raised as a single `RuntimeError(f"Failed to draw Mermaid diagram: {e}")` — the "all failures look identical" gap AC6-8 target. `_process_response` separately raises `MermaidDiagramGenerationError` for non-200 and `ValueError` for unexpected content-type, but `draw_mermaid`'s catch-all erases that distinction before it reaches callers.
- `src/codemie/rest_api/routers/files.py:372-435` — `POST /files/diagram/mermaid` → `create_mermaid_diagram` → `generate_diagram_or_raise`. Any exception from `MermaidService.draw_mermaid` becomes `ExtendedHTTPException(400, "Mermaid diagram configuration error", details=str(e), help="Please check your Mermaid syntax and try again.")` — this is the "everything is a syntax error" bug named in Background. A `None` return (dead code today; `draw_mermaid` never returns `None`, only raises) maps to 503 "Mermaid service is not available".
- `src/codemie/rest_api/routers/workflow.py:784-816` — `POST /workflows/diagram` → `WorkflowExecutor.validate_workflow_and_draw`; `None` → `ExtendedHTTPException(503, details="Mermaid is not available.")`.
- `src/codemie/workflows/workflow.py:296-323` — `WorkflowExecutor.validate_workflow_and_draw` calls `graph.draw_mermaid(...)` then `MermaidService.draw_mermaid(..., type=SVG)` inside a `try/except Exception: logger.error(...); return None` — failures are swallowed to `None`, never raised. This is why workflow create/update (AC9) already succeeds today even if rendering fails: `save_workflow_schema` (`src/codemie/service/workflow_service.py:685-699`) wraps `files_repo.write_file` in its own `try/except Exception` too, so a rendering/storage failure never blocks the workflow save.
- `src/codemie/service/conversation/export_utils.py:82-124` — `ExportUtils.replace_mermaid_with_images` catches failures per diagram block and falls back to the original ```mermaid code block (`logger.warning`, no exception propagates) — export behavior already degrades gracefully and is explicitly Out of Scope.
- `src/codemie/configs/customer_config.py` — the D5 reference pattern. `CONFIG_IDS` maps short keys to `"features:<key>"` component ids. `_get_runtime_config()` appends `Component(id=CONFIG_IDS["knowledgeBases"], settings=ComponentSetting(enabled=ElasticSearchClient.is_configured()))` (line 237-242) — a runtime-computed (never YAML/DB-overridable) flag. `is_feature_enabled(feature_key)` / `is_component_enabled` resolve a component through `resolve_component`, honoring runtime > override > YAML precedence. `_apply_feature_env_override` lets any YAML `features:*` component be forced on/off via `FEATURE_<UPPER_SNAKE_ID>` env var (e.g. `features:cliAnalytics` → `FEATURE_CLI_ANALYTICS`).
- `src/codemie/clients/elasticsearch.py:23-30` — `ElasticSearchClient.is_configured()` = `retrieval_available(config) and bool(config.ELASTIC_URL)`. Direct analogue for a prospective `mermaid_configured()`/`is_configured()` check (server URL set, or `MERMAID_USE_MERMAID_INC` true per D2).
- `src/codemie/rest_api/routers/customer_config.py:69-71` — `GET /config` → `resolve_components()` → published to UI; this is literally "application settings" from D5.
- `src/codemie/service/customer_config_service.py:157-174` — `resolve_components()` wraps `component_resolution.resolve_all(...)` and applies `_public_view` before returning.
- `src/codemie/rest_api/main.py:516-520` — `_run_standalone_startup()`: `"""Single entry point for standalone-image startup behaviour; no-op unless STANDALONE is set."""` currently just does `if not config.STANDALONE: return; logger.info("Standalone mode enabled")`. Called once from `lifespan` (line ~1191), after `_check_sharepoint_pkce_redis()` (line 1189) and `_check_usage_attribution_settings()` (line 1190) — both are the precedent shape for a startup diagnostic: guard on a flag, try the real check, `logger.warning`/`logger.error` with a specific message, never raise.
- `standalone/Dockerfile:121-122` — `# Standalone image: no external Elasticsearch/Mermaid/MCP-connect/Redis.` / `ENV STANDALONE=true ELASTIC_URL= MERMAID_SERVER_URL= MCP_CONNECT_URL= REDIS_HOST=`. This is the "empty-default mechanism": the standalone image's own `ENV` layer blanks `MERMAID_SERVER_URL` to `""`, overriding config.py's non-empty default, specifically for that image. `standalone/docker-compose.standalone.yml` and `standalone/.env.standalone.example` deliberately never set these keys (enforced by `tests/codemie/standalone/test_standalone_compose_contract.py`), so an operator can only restore a value by setting `MERMAID_SERVER_URL` themselves.
- `src/codemie/agents/utils.py:450` — `logger.debug(f"STANDALONE TRACE: agent='{agent_name}'")` — unrelated; "STANDALONE" here is an observability trace-type label, not `config.STANDALONE`.

### Architecture and Layers Affected

- **Configuration** (`src/codemie/configs/config.py`): likely new `MERMAID_ENABLED`-style feature flag field, alongside existing `MERMAID_SERVER_URL` / `MERMAID_USE_MERMAID_INC`.
- **Feature-flag/runtime-settings** (`src/codemie/configs/customer_config.py`, `component_resolution.py`, `customer_config_service.py`, `rest_api/routers/customer_config.py`): the D5 publish-to-UI path (`GET /config`).
- **Service layer** (`src/codemie/service/file_service/mermaid_service.py`): the single chokepoint for the renderer call; needs distinct exception types/paths for "not configured" vs "unreachable" vs "rejected".
- **API layer** (`src/codemie/rest_api/routers/files.py`, `rest_api/routers/workflow.py`): error-message mapping consumes whatever `MermaidService` raises.
- **Workflow layer** (`src/codemie/workflows/workflow.py`, `src/codemie/service/workflow_service.py`): already fail-soft (returns `None` / logs and continues) — AC9 is satisfied by the existing swallow-on-`Exception`, as long as the new "not configured" case still raises/returns something this code already treats as non-fatal.
- **Startup diagnostics** (`src/codemie/rest_api/main.py`, `_run_standalone_startup`): where the AC2/AC3 warning belongs, following `_check_sharepoint_pkce_redis`'s shape.

### Integration Points

- Internal: `MermaidService` ← `files.py` router, `workflow.py` router (indirectly via `WorkflowExecutor`), `workflows/workflow.py`, `service/conversation/export_utils.py`, `service/workflow_service.py` (schema storage, not generation).
- External: Mermaid server (`{MERMAID_SERVER_URL}/generate`, via `requests`) or `mermaid.ink` (via `requests.get`) — both unauthenticated HTTP calls with no circuit breaker, no retry.
- `requests.exceptions.ConnectionError` vs `requests.exceptions.HTTPError`/non-200 is already distinguished elsewhere in the repo (`src/codemie/datasource/loader/confluence_loader.py:36-43`, `sharepoint_loader.py:342/387/449`) — a usable precedent for separating "unreachable" (AC6) from "rejected" (AC7) in `MermaidService`.

### Patterns and Conventions

- Runtime (never YAML-overridable) feature components are appended in `customer_config.py#_get_runtime_config()`, each wrapping an `enabled=<live check>` boolean (e.g. `knowledgeBases`, `datasources`, `codeIndexing` all reuse `ElasticSearchClient.is_configured()`).
- YAML-declared `features:*` flags support a `FEATURE_<SNAKE_CASE>` env override at load time (`_apply_feature_env_override`), the likely mechanism for D3's "new Mermaid feature flag" if it is meant to be admin-configurable without redeploy; a purely `config.py`-defined boolean (like `CHAT_CONTEXTUAL_NAMING_ENABLED` or `STANDALONE` itself) is the alternative if it's static/env-var-only — `.ai-run/guides/development/configuration-patterns.md` documents both toggle shapes.
- Startup diagnostics are small, flag-gated, non-fatal functions called once from `lifespan()` (`_check_sharepoint_pkce_redis`, `_check_usage_attribution_settings`, `_run_standalone_startup`) — log and continue, never raise.
- `ExtendedHTTPException(code, message, details, help)` is the uniform API error shape (`files.py`, `workflow.py`); distinct failure modes should get distinct `message`/`help` text per `.ai-run/guides/development/error-handling.md`'s "Accurate Error Details" rule.
- Config-contract tests assert the standalone Dockerfile/compose/env-example triangle stays consistent (`tests/codemie/standalone/test_standalone_compose_contract.py`) — any new standalone-only env var for Mermaid would need the same triangle check.

---

## 3. Documentation Findings

### Guides and Architecture Docs

No guide file specifically covers Mermaid. Relevant general guides:
- `.ai-run/guides/development/configuration-patterns.md` — documents the "Feature Flags" convention (centralize near assembly points) and a worked example (`CHAT_CONTEXTUAL_NAMING_ENABLED`) showing both a static config flag and a DB-backed runtime-togglable flag.
- `.ai-run/guides/development/error-handling.md` — "Accurate Error Details": one message per distinct failure mode, not one catch-all — directly applicable to AC6/7/8.

### Architectural Decisions

- `customer_config.py:279-301` inline comments record a prior decision (MR !4070 review #22) that runtime-computed UI flags must read the same backend source of truth the backend itself enforces, rather than the UI tracking its own copy — the same discipline D5 asks for with Mermaid.
- `standalone/Dockerfile:121` inline comment states the deliberate choice to leave Elasticsearch/Mermaid/MCP-connect/Redis unset in the standalone image.

### Derived Conventions

No guide names Mermaid; conventions above were derived from `customer_config.py`, `main.py` startup checks, and the config file itself.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/file_service/test_mermaid_service.py` — unit tests for `MermaidService.draw_mermaid` (internal/external paths, non-200, bad content-type, exception-to-`RuntimeError`). All failure-path tests currently assert a generic `RuntimeError`/`ValueError` — no test distinguishes "unreachable" from "rejected".
- `tests/codemie/rest_api/routers/test_files.py:417+` — `test_create_mermaid_diagram_service_unavailable` exercises the router's 503 path (today effectively dead code, since `draw_mermaid` never returns `None`).
- `tests/codemie/rest_api/routers/test_workflow.py:1215` — asserts `"Mermaid is not available."` detail text on the diagram-unavailable path.
- `tests/codemie/rest_api/test_standalone_startup.py` — exercises `_run_standalone_startup()` as a no-op/flag-gated gate; this is the file most likely to grow Mermaid-warning assertions.
- `tests/codemie/configs/test_config.py:159-195` — `STANDALONE` env/file/boolean-coercion tests — the pattern for any new Mermaid flag's config tests.
- `tests/codemie/standalone/test_standalone_compose_contract.py` — asserts the Dockerfile/compose/env-example triangle for `STANDALONE_ENV_KEYS` (currently `STANDALONE, ELASTIC_URL, MERMAID_SERVER_URL, MCP_CONNECT_URL, REDIS_HOST`).
- `tests/codemie/configs/test_customer_config.py`, `test_customer_config_override.py`, `tests/codemie/service/test_customer_config_service.py`, `tests/codemie/rest_api/routers/test_customer_config.py`/`test_customer_config_router.py` — cover the runtime-component/feature-flag machinery a new Mermaid runtime component would plug into.

### Testing Framework and Patterns

pytest, with `unittest.mock.patch`/`Mock` for isolating `config` and `requests` calls (see `test_mermaid_service.py`), and `patch.object(main.config, "STANDALONE", ...)` plus `patch.object(main, "logger")` for startup-gate assertions (see `test_standalone_startup.py`).

### Coverage Gaps

- No test distinguishes a connection failure from an HTTP-rejection from a "not configured" state in `MermaidService` — AC6/7/8 require three distinct, currently-unproven code paths.
- No test asserts the AC2/AC3 startup-warning content (missing-config message naming each degraded feature) or its absence when a renderer is configured.
- No test covers the AC4 "available only if flag enabled AND renderer configured" publish-to-UI logic (customer_config.py has no Mermaid entry yet).
- No test covers non-standalone AC1 (Mermaid always reported available, default URL kept) as an explicit regression guard.

---

## 5. Configuration and Environment

### Environment Variables

- `MERMAID_SERVER_URL` (default `"http://localhost:8082"`, config.py:83) — blanked to `""` in the standalone image only, via Dockerfile `ENV`.
- `MERMAID_SERVER_TIMEOUT` (default `50`, config.py:84).
- `MERMAID_USE_MERMAID_INC` (default `False`, config.py:85) — opts into `mermaid.ink`; per D2 this counts as "configured" regardless of `MERMAID_SERVER_URL`.
- `STANDALONE` (default `False`, config.py:483) — gates `_run_standalone_startup()` in `main.py` and is asserted by `tests/codemie/configs/test_config.py`.
- `ELASTIC_URL`, `MCP_CONNECT_URL`, `REDIS_HOST` — sibling standalone-blanked variables (not Mermaid-specific, but part of the same `ENV STANDALONE=true ELASTIC_URL= MERMAID_SERVER_URL= MCP_CONNECT_URL= REDIS_HOST=` line and the same `STANDALONE_ENV_KEYS` contract test).
- No `FEATURE_MERMAID`-style env var exists yet; D3's new flag would need to pick between this override mechanism (if declared as a YAML `features:*` component) or a bare `config.py` field (if static).

### Configuration Files

- `src/codemie/configs/config.py` — central `Config(BaseSettings)`; Mermaid settings live at lines 83-85, `STANDALONE` at 483.
- `config/customer/customer-config.yaml` — YAML-declared `features:*` components (29+ existing flags, e.g. `features:cliAnalytics`, `features:webSearch`); no Mermaid entry yet.
- `standalone/Dockerfile` — standalone-image-only `ENV` overrides (lines 121-122).
- `standalone/.env.standalone.example`, `standalone/docker-compose.standalone.yml` — deliberately silent on the `STANDALONE_ENV_KEYS` set; contract-tested.

### Feature Flags and Deployment Concerns

- Runtime feature-flag publishing: `customer_config.py#_get_runtime_config()` → `GET /config` (`rest_api/routers/customer_config.py:69-71`). A Mermaid entry here (per D5) would follow the `knowledgeBases` shape: `Component(id="features:mermaid", settings=ComponentSetting(enabled=<flag> and <renderer configured>))`.
- Standalone image build: `standalone/Dockerfile` has no Mermaid server bundled (explicitly Out of Scope to add one) and no build-time secret/credential handling specific to Mermaid.
- No feature-flag-admin-UI secrets or vault references touch Mermaid; it is a pure HTTP-reachability/config concern, not a credentials one.

---

## 6. Risk Indicators

- **Error-path fan-in**: `MermaidService.draw_mermaid`'s single `except Exception` (mermaid_service.py:51-59) currently destroys the distinction between connection failure, HTTP rejection, and bad-content-type before it reaches any caller. Implementing AC6/7/8 requires restructuring this method's exception handling, which three call sites (`files.py`, `workflow.py` router via `workflows/workflow.py`, `export_utils.py`) all consume differently (one raises to the user, one swallows to `None`, one swallows and falls back) — a change here has three different blast radii.
- **D3's flag mechanism is unspecified in code**: no existing Mermaid feature flag exists to extend; the plan must choose between the YAML `features:*` + `FEATURE_*` env-override pattern (dynamic, admin-togglable) or a static `config.py` boolean (restart-required) — both patterns coexist elsewhere in this codebase for different reasons (see `configuration-patterns.md`'s `CHAT_CONTEXTUAL_NAMING_ENABLED` table), so this is a design choice, not a discovered constraint.
- **AC9 may already be satisfied**: `workflows/workflow.py#validate_workflow_and_draw` already swallows all Mermaid failures to `None`, and `workflow_service.py#save_workflow_schema` already swallows file-write failures — a "not configured" classification must be careful to keep flowing into these same `except Exception` branches, or AC9 could regress if a new exception type isn't caught at the same level.
- **Startup check placement**: `_run_standalone_startup()` is documented as "Single entry point for standalone-image startup behaviour" — adding Mermaid diagnostics there is consistent, but the function must still name each degraded feature (AC2: chat diagrams, workflow graph preview, stored workflow diagram image, conversation export diagrams) in one warning, which has no existing precedent (other startup checks name one feature each).
- **No coverage for the three distinct new error classifications** (not-configured / unreachable / rejected) anywhere in the test suite today — this is new ground, not a regression risk, but a real gap the plan must size for.
- **mermaid.ink as "configured" (D2)** interacts with `MERMAID_SERVER_URL` being blank in standalone: the "is Mermaid configured" check must OR `MERMAID_USE_MERMAID_INC` with `bool(MERMAID_SERVER_URL)`, mirroring `ElasticSearchClient.is_configured()`'s `and`-style check but with the opposite boolean operator.

---

## 7. Summary for Complexity Assessment

This task touches four layers: configuration (`config.py`, a new feature flag), the feature-flag/runtime-settings publishing path (`customer_config.py` → `GET /config`, following the existing `knowledgeBases` runtime-component pattern exactly), the Mermaid service itself (`mermaid_service.py`, which needs its single catch-all exception handler split into three distinguishable failure modes), and startup diagnostics (`main.py`'s `_run_standalone_startup`, extending an existing no-op hook). Call sites in `files.py` and `workflow.py` routers, plus `workflows/workflow.py` and `export_utils.py`, each consume `MermaidService` differently (raise-to-user vs. swallow-to-`None` vs. swallow-and-fallback) and must each continue behaving correctly once the error taxonomy changes underneath them.

Technical novelty is moderate: every piece has a direct precedent elsewhere in the repo (`ElasticSearchClient.is_configured()` for "is this backend reachable", `knowledgeBases` for runtime-published availability, `_check_sharepoint_pkce_redis`/`_check_usage_attribution_settings` for non-fatal startup diagnostics, `confluence_loader.py`'s `ConnectionError` vs `HTTPError` split for distinguishing unreachable-vs-rejected). The main open design question the codebase does not answer is which flag mechanism (dynamic YAML `features:*` + env-override, vs. static `config.py` boolean) the new D3 flag should use — both exist side-by-side for different reasons today.

Test coverage posture: existing `MermaidService` tests only assert generic `RuntimeError`/`ValueError`, with no distinction between failure modes, and no test exists yet for the startup-warning content, the AC4 publish-to-UI gating, or the AC1 non-standalone regression guard. The `test_standalone_compose_contract.py` triangle test will need extending if any new standalone-only env var is introduced for Mermaid. Key risks are the fan-in/fan-out of `MermaidService`'s error handling across three differently-behaving call sites, and keeping AC9 (workflow save succeeds today) true once new exception types are introduced into code that currently catches `Exception` broadly.

---

## 8. External References

None named by the task. The task names repo-internal paths (`src/codemie/configs/config.py:483`, `standalone/Dockerfile`, standalone env defaults, `customer_config.py` / `knowledgeBases` pattern) as pointers for in-repo research, not as external sources of truth outside this repository; these were researched directly and are cited throughout Section 2 and Section 5 above.
