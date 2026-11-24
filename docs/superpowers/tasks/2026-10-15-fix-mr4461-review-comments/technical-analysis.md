# Technical Research

**Task**: headroom compressor singleton module-structure alembic compose-profiles
**Generated**: 2026-10-15
**Research path**: filesystem

---

## 1. Original Context

"fix these comments" (user), referring to the unresolved MR 4461 review threads fetched earlier:

1. [Andriy_Lukashchuk] src/codemie/service/headroom/sdk_compressor.py:67 - "Is there any justification for global? Let's use singleton method"
2. [Andriy_Lukashchuk] src/codemie/service/headroom/compressor.py:36 - "Looks like we don't use this protocol" (HeadroomCompressor Protocol)
3. [Andriy_Lukashchuk] src/codemie/service/headroom/__init__.py:14 - "I suggest to do one more pass on this module architecture and file structure so it's easier to digest. To keep high-level stuff in the headroom, and dependencies for example compressors can be moved to a subfolder"
4. [Dmytro_Bondarenko5] src/external/alembic/versions/f8bd0b700a64_add_headroom_config_to_assistants.py:31 - after merging main there are two Alembic heads (e091153f77dd and y1z2a3b4c5d6); re-parent f8bd0b700a64 onto main's current head y1z2a3b4c5d6 and update EXPECTED_DOWN_REVISION in tests/codemie/migrations/test_add_headroom_config_to_assistants.py. main's test_add_ms_exchange_credential_type.py already has the single-head check - take main's version in that conflict.
5. [Dmytro_Bondarenko5, summary comment] main moved docker-compose.yml to compose profiles ("no profile = base services only"); the `headroom` sidecar service needs a `profiles:` entry or it becomes a base service and restart-loops for anyone without HEADROOM_PROXY_TOKEN. The MR is also a Draft, 17 commits behind main, with conflicts in docker-compose.yml and tests/external/alembic/test_add_ms_exchange_credential_type.py.

Raw threads: /tmp/mr4461_discussions.json. Branch EPMCDME-15645_headroom-workflow-scope.

---

## 2. Codebase Findings

### Existing Implementations
`src/codemie/service/headroom/` (827 lines, flat, 9 modules + empty-ish `__init__.py` docstring only):
- `models.py` (53) - `HeadroomStrategy`, `HeadroomConfig`, `HeadroomOverride`, `ResolvedHeadroomConfig`.
- `resolver.py` (61) - `resolve()`; imports `factory.get_compressor_mode`. `scope.py` (57) - `WorkflowHeadroomLayers`, `resolve_headroom_config`.
- `strategies.py` (53) - `sidecar_config`, `sdk_compress_kwargs`.
- `compressor.py` (39) - `CompressOutcome` (frozen dataclass), `HeadroomCompressionError`, and `HeadroomCompressor(Protocol)` with `mode: str` and `compress(...)`.
- `sidecar_compressor.py` (85) - `SidecarCompressor` (class attr `mode = "sidecar"`, `httpx.post`).
- `sdk_compressor.py` (174) - `SDKCompressor` (`mode = "sdk"`) plus module-level mutable state: `_executor = ThreadPoolExecutor(max_workers=4)`, `_lock = threading.Lock()`, `_sdk: _SDK | None`, `_unavailable: bool`; functions `_reset_for_tests()` (line 63, `global _sdk, _unavailable` at 64 - the thread anchor is line 67 area), `_load_sdk()` (double-checked lock, `global`), `_apply_sdk_env`, `_warn_if_kompress_unavailable`, `_apply_marker_free_pipeline`.
- `factory.py` (83) - `get_compressor_mode()`, `_resolve_mode()` (`global _legacy_warned`), `_report_missing_sdk_once()` (`global _sdk_missing_reported`), `get_compressor()` decorated `@lru_cache(maxsize=1)`, lazy-imports `SidecarCompressor`/`SDKCompressor` by mode, raises `HeadroomCompressionError("Headroom is off")` when off.
- `hook.py` (207) - `build_headroom_pre_model_hook`; only calls `get_compressor().compress(...)` and `get_compressor_mode()`.

### Relation of factory, Protocol and the two compressors (comment 2)
- `HeadroomCompressor` is referenced in exactly two places in the whole repo: its definition and the return annotation `-> HeadroomCompressor` of `factory.get_compressor` (plus the import in factory.py:22). No class inherits it, no `isinstance`/`runtime_checkable`, no test mentions it. `SidecarCompressor` and `SDKCompressor` satisfy it only structurally (same `mode` class attr, same `compress` signature).
- `.mode` on a compressor instance is never read in src: `hook.py` uses `get_compressor_mode()` (config-derived). Only tests assert `SDKCompressor.mode == "sdk"` (test_sdk_compressor.py:150) and `SidecarCompressor.mode == "sidecar"` (test_sidecar_compressor.py:67).
- Options (design choice, for spec): (a) delete the Protocol and annotate `-> SidecarCompressor | SDKCompressor`; keep `CompressOutcome`/`HeadroomCompressionError` where they are; (b) turn it into an `ABC` base class that both compressors inherit (repo has 32 `(ABC)` classes; Protocols are also used for ports, e.g. `service/llm_proxy/provider.py` `LLMProxyProvider` with `runtime_checkable`, `repository/cli_analytics/ports.py`). Reviewer wording "we don't use this protocol" favours (a) or (b) with explicit inheritance; both are small edits.

### Singleton / lazy-state conventions elsewhere (comment 1)
Dominant repo idiom for a process-wide singleton is a class with `_instance` + `_lock` and `__new__` double-checked locking:
- `src/codemie/service/request_summary_manager.py:87-96` (`_instance = None`, `_lock = threading.Lock()`, `__new__` with `with cls._lock:` + `_initialize()`).
- `src/codemie/service/security/token_exchange_service.py:94-117` (`ClassVar` typed `_instance`/`_lock`), `oidc_token_exchange_service.py:67`, `codemie_tools/.../code_executor/session_manager.py:66-76`, `batch_job_runner.py:139-149`, `workflow_pool.py:36-46` (no lock), `generation_manager.py:21`, `datasource/loader/file_processor_pool.py:38`.
- "Singleton method" form (classmethod accessor): `src/codemie/service/mcp/toolkit_service.py` `_instance: ClassVar[...]`, `get_instance()` (line ~1070), `init_singleton()`, `reset_instance()` (~1125; documented "primarily for testing").
- Module-level `global` accessors also exist (`enterprise/litellm/client.py:43/65`, `repository/cli_analytics/factory.py:64/77`, `enterprise/observability/factory.py`, `service/llm_proxy/provider_registry.py:34`, `service/leaderboard/framework_metadata.py:38`) - so the reviewer's objection is a style preference, not a repo rule.
- `functools.lru_cache`/`@cache` is used for lazy factories (`codemie_tools/base/toolkit_provider.py`, `get_compressor` itself) with `cache_clear()` in test fixtures.
- Other `global` state in this package: `factory.py` `_legacy_warned`, `_sdk_missing_reported` (same pattern; the reviewer only flagged sdk_compressor but these would be the next target; tests reset them by assigning `factory._legacy_warned = False` at test_factory.py:34-39).

### Architecture and Layers Affected
Service layer only (`codemie.service.headroom`) plus tests, one Alembic migration + migration test, `docker-compose.yml`, the guide `.ai-run/guides/integration/headroom-integration.md`, and (after merging main) `README.md`/`.env.example` profile docs. Dependency direction: `hook -> factory -> {compressor, sidecar_compressor, sdk_compressor}`; compressors -> `{compressor, models, strategies}`; `resolver -> factory, models`; `scope -> resolver, models`. No cycles; `factory` imports compressors lazily inside `get_compressor` (needed so `headroom` is never imported in sidecar/off mode; asserted by subprocess tests).

### Importers of the package (comment 3) - everything that breaks on a move
Production importers (all use full submodule paths, `__init__` re-exports nothing, guide line 29 documents this):
- `codemie/core/workflow_models/workflow_config.py:44`, `workflow_models.py:33`, `workflows/utils/utils.py:39-40`, `agents/langgraph_agent.py:108-109`, `service/assistant_service.py:54`, `rest_api/models/assistant.py:61` - they import only `models`, `scope`, `hook`. Compressor modules are imported by nobody outside the package except tests; `compressor.py` symbols (`CompressOutcome`, `HeadroomCompressionError`) are imported by `hook`'s tests and the compressor modules.
- Intra-package imports of the compressor files: `factory.py:22` (compressor), `:76`/`:80` (lazy sidecar/sdk), `sdk_compressor.py:32-34`, `sidecar_compressor.py:24-26`, `compressor.py:22`.

If `compressor.py`, `sidecar_compressor.py`, `sdk_compressor.py` (and possibly `strategies.py`) move to a subpackage such as `codemie/service/headroom/compressors/`, these test strings/imports change (all under `tests/codemie/service/headroom/`):
- `test_factory.py:21-25` imports `HeadroomCompressionError`, `SDKCompressor`, `SidecarCompressor`; patch targets `codemie.service.headroom.factory.config|logger|importlib...` (factory stays, so unchanged unless factory moves too).
- `test_sdk_compressor.py:31-35` imports (`sdk_compressor`, `compressor`, `strategies`); `:37` `LOGGER = "codemie.service.headroom.sdk_compressor.logger"`; `:158-170` two subprocess scripts with `"import codemie.service.headroom.sdk_compressor"` (string, would only fail at runtime); `:490`, `:494` (`patch("codemie.service.headroom.sidecar_compressor.httpx.post")`), `:502`; `sdk_compressor.config` monkeypatches at :234/:277; `sdk_compressor._reset_for_tests()` (:109,111,233) and `_load_sdk()` (:439,:547).
- `test_sidecar_compressor.py:24-27`; `:29` `POST = "codemie.service.headroom.sidecar_compressor.httpx.post"`; `:38` `patch("codemie.service.headroom.sidecar_compressor.config")`.
- `test_hook.py:25` imports `compressor.CompressOutcome, HeadroomCompressionError` (other hook patch targets `...hook.*` stay).
- `test_strategies.py:24`, `:31` (`patch("codemie.service.headroom.strategies.config")`) only if `strategies.py` moves.
- Outside the package tests: `test_assistant_service_headroom.py:29`, `test_assistant_service_workflow_headroom.py:32`, `test_resolver.py:35` patch `codemie.service.headroom.factory.config` and `...resolver.customer_config`: unaffected as long as `factory.py`/`resolver.py` stay at package level.
- Docs: guide `headroom-integration.md` lines 14, 17, 29, 105 (`strategies.py`/`models.py` paths in "Adding a strategy" section), 224-ish Verification list. No pyproject/sonar/Makefile/CI path references to `service/headroom` (grep clean).
- Ruff/mypy path config: none found.
- Re-export shims: nothing outside the package imports the compressor modules, so shims are not needed for production code. A shim would only keep old test patch strings working; since patch strings resolve against the module where the name is looked up, tests must be updated regardless (a shim module that re-exports does not make `patch("...sdk_compressor.logger")` patch the real logger). Recommendation: no shims; update tests.
- Precedent for sub-package layouts under `service/`: `llm_proxy/` (provider protocol + registry), `security/token_providers/`, `budget/`, `mcp/`. `__init__.py` files in these are mostly empty/docstring.

### Patterns and Conventions
Apache license header on every file; `from __future__ import annotations`; logger from `codemie.configs.logger`; errors never carry tokens or message content; strategy values only in `strategies.py` (guide); guide convention "import from submodules, `__init__` re-exports nothing".

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/headroom-integration.md` is the single guide; it names module paths (lines 14, 17, 29, "Adding a strategy"), the f8bd0b700a64/e091153f77dd migration ids in Deployment step 1 (line ~185), and Deployment step 2 "Run the `headroom` sidecar service from `docker-compose.yml` (no published port ...)". It has user-owned content; edit only the lines concerned.
- `.ai-run/guides/architecture/layered-architecture.md`, `project-structure.md` apply to the module move; `.ai-run/guides/agents/custom-tool-creation.md:36` warns "run `alembic heads` right before writing the migration and again right before merging".
- On `origin/main` (not on branch): README.md "Launch variants (`COMPOSE_PROFILES`)" table (lines 104-115), `.env.example` lines 62-76 "Docker Compose profiles". Neither mentions headroom.

### Architectural Decisions
None recorded for the module layout. Design spec `docs/superpowers/specs/2026-10-08-headroom-per-assistant-workflow-design.md:190` records the `headroom` compose service (image `ghcr.io/headroomlabs-ai/headroom:0.40.0`).

### Derived Conventions
Alembic tests in this repo avoid pinning the repo-wide head: main's `test_add_ms_exchange_credential_type.py` now asserts exactly one head and that the migration is reachable from it (`script.iterate_revisions(heads[0], "base")`); the branch's `test_add_headroom_config_to_workflows.py` already does the same (walk_revisions) - the assistants test also does (`REVISION in walk_revisions()`).

---

## 4. Testing Landscape

### Existing Coverage
`tests/codemie/service/headroom/`: test_factory (4 fns, autouse fixture clears `get_compressor.cache_clear()` + resets two module globals), test_sdk_compressor (~22, uses `isolated_state` autouse fixture calling `sdk_compressor._reset_for_tests()`, fake `headroom` modules, 2 subprocess tests asserting `headroom` is not imported, 1 opt-in real-Kompress test via `HEADROOM_TEST_KOMPRESS=1`, real-SDK tests skipped when `headroom-ai` absent), test_sidecar_compressor, test_hook, test_resolver, test_models, test_strategies. Migrations: `tests/codemie/migrations/test_add_headroom_config_to_assistants.py` (constants `REVISION="f8bd0b700a64"`, `EXPECTED_DOWN_REVISION="5189d4d2afaf"` at line 27 with a comment "re-parented after merging main (2026-10-12)"; `test_revision_chain`, upgrade/downgrade, `test_migration_graph_has_single_head`), `..._workflows.py` (`EXPECTED_DOWN_REVISION="f8bd0b700a64"`, unaffected by re-parenting f8bd).
`tests/external/alembic/test_add_ms_exchange_credential_type.py` on the branch has a modified `test_alembic_chain_has_single_head_at_this_migration` (len(heads)==1 plus reachability); main renamed it `test_alembic_chain_has_a_single_head_that_includes_this_migration` with equivalent logic. Taking main's version in the conflict resolves it.

### Testing Framework and Patterns
pytest + `unittest.mock.patch`/`monkeypatch`; module-state reset through a private function or direct global assignment. Any singleton refactor must keep a reset hook for these fixtures (the repo precedent is `MCPToolkitService.reset_instance()`), and the executor/lock semantic (timeout test at :277 uses `monkeypatch.setattr(sdk_compressor.config, "HEADROOM_TIMEOUT_SECONDS", 0.05)`).

### Coverage Gaps
No test asserts the `HeadroomCompressor` Protocol. No test covers docker-compose profile content (repo has none for compose). No test checks `SDKCompressor` thread-safety of concurrent first load.

---

## 5. Configuration and Environment

### Alembic chain (comment 4) - verified read-only against `origin/main` (564fe17f1, local ref already fetched)
- Branch HEAD currently has a single head: `5189d4d2afaf` (MS Exchange credential type) <- `f8bd0b700a64` (headroom assistants, `down_revision = "5189d4d2afaf"` at file line 31) <- `e091153f77dd` (headroom workflows, `down_revision = "f8bd0b700a64"`).
- `origin/main` is 17 commits ahead of HEAD (`git rev-list --count HEAD..origin/main` = 17; merge-base `2b039d66c6ad`). On `origin/main`: `5189d4d2afaf` <- `a8f23cf09c37` (cache tokens on litellm_spend_logs) <- `y1z2a3b4c5d6` (agent workspace folder owners, `down_revision: Union[str, Sequence[str], None] = "a8f23cf09c37"`). I computed heads from every version file on origin/main: exactly one head, `y1z2a3b4c5d6`. The reviewer's claim is confirmed.
- **`y1z2a3b4c5d6` and `a8f23cf09c37` do not exist on the working branch** (`ls src/external/alembic/versions` finds only the headroom files and 5189d4d2afaf). Changing `f8bd0b700a64.down_revision` to `"y1z2a3b4c5d6"` before main is merged/rebased would produce a dangling reference and break `alembic` and the migration tests. Re-parenting therefore requires merging/rebasing `origin/main` first (the user said not to do that in this research; it is a task step). Merge shows no textual conflict on the versions dir (diff 2b039d66..origin/main there only adds the two migration files).
- After main is in the tree: set `f8bd0b700a64.down_revision = "y1z2a3b4c5d6"` (and its `Revises:` docstring line 3 + Create Date unchanged), set `EXPECTED_DOWN_REVISION = "y1z2a3b4c5d6"` in `tests/codemie/migrations/test_add_headroom_config_to_assistants.py:27` and refresh the comment on line 26. `e091153f77dd` keeps `down_revision = "f8bd0b700a64"` and `test_add_headroom_config_to_workflows.py` stays correct. Final chain: ...`5189d4d2afaf` <- `a8f23cf09c37` <- `y1z2a3b4c5d6` <- `f8bd0b700a64` <- `e091153f77dd` (single head).
- Main's migrations touch only `litellm_spend_logs` and `agent_workspace_folder_owners`, so order vs. headroom columns does not matter (reviewer's statement). Implication for already-deployed DBs: none for this MR (unmerged). But any environment that already ran the branch's old chain would hold `f8bd0b700a64` stamped with the old parent; alembic only tracks revision ids, so upgrading is fine.
- Tests that will be exercised: `tests/codemie/migrations/test_credentialtypes_enum_migrations.py::test_head_enum_matches_credential_types_model` (reviewer says it failed with two heads), both headroom `test_migration_graph_has_single_head`, `tests/external/alembic/test_add_ms_exchange_credential_type.py`, and main's new `tests/external/alembic/test_add_cache_tokens_to_litellm_spend_logs.py`, `tests/codemie/migrations/test_add_agent_workspace_folder_owners.py`.
- Other repo references to the old parent id: only the two test files above and `.ai-run/guides/agents/custom-tool-creation.md:36` (a pointer to the 5189d4d2afaf file, unaffected). Guide Deployment step 1 lists the two headroom ids (unchanged ids).
- Merge conflicts the reviewer reports: `docker-compose.yml` (see below) and `tests/external/alembic/test_add_ms_exchange_credential_type.py` (take main's version; the branch only changed the same assertion, so main's logic is a superset).

### docker-compose.yml (comment 5) - READ ONLY; user has uncommitted edits
- Working-tree-only (uncommitted) diff vs HEAD: litellm `AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY` -> `${...:-}` passthrough (lines ~133-134) and `ports: - "8787:8787"` added to the `headroom` service (line ~159). Already committed (HEAD) in the headroom service: `HEADROOM_OFFLINE: "0"`, `mem_limit: 2g`, `cpus: 1.0`. Must not be reverted by the merge resolution.
- Headroom-related spots in the current file: codemie `environment` line 46 (`HEADROOM_PROXY_TOKEN=${HEADROOM_PROXY_TOKEN:-}`), service `headroom:` at line 154 (image `ghcr.io/headroomlabs-ai/headroom:0.40.0`, container_name `headroom`, `restart: unless-stopped`, healthcheck on `/readyz`). Comment at 155-156 says "no host port is published and no service depends_on it" - now false for the port because of the uncommitted `ports` edit; comment at 172-174 says "With no token the server refuses to bind 0.0.0.0 and this container exits; nothing depends on it, so the rest of the stack still starts" (matches the reviewer's restart-loop concern because of `restart: unless-stopped`).
- How main applies profiles (`git show origin/main:docker-compose.yml`): header comment lines 1-7 list "Profiles (select in .env via COMPOSE_PROFILES, comma separated; no profile = base services only)" with groups `base (always)`: postgres, elasticsearch, mermaid-server, k3s; `app`: codemie; `litellm`: litellm; `observability`: jaeger, kibana, prometheus, grafana, pyroscope; `full`: everything above. Services opt in with a `profiles:` list placed directly after `container_name`: `codemie` `profiles: [app, full]`, `litellm` `profiles: [litellm, full]`, `jaeger/kibana/pyroscope/prometheus/codemie-grafana` `profiles: [observability, full]`. Base services have no `profiles:`. Main also adds a `k3s` service, `k3s_data` volume, codemie `depends_on: k3s` and `./.kube-local` volume - these merge in via the conflict.
- Naming convention implies: new `profiles: [headroom, full]` on the `headroom` service (profile named after the service/feature, plus `full`, mirroring litellm). Update the header comment block (add `headroom:   headroom` row; keep `full: everything above`; add example `COMPOSE_PROFILES=app,headroom`). Profile also needs to be added to docs main already has: README.md "Launch variants" table (e.g. `app,headroom`), `.env.example` profile list lines 62-76 (plus optionally a `HEADROOM_PROXY_TOKEN` hint); `.env.example` currently has no Headroom lines at all, and `tests/codemie/configs/test_env_example.py` checks a REQUIRED_KEYS set (does not forbid new comments).
- Branch docs mentioning enabling the sidecar: guide Deployment step 2 ("Run the `headroom` sidecar service from `docker-compose.yml` (no published port; reached at `http://headroom:8787`). Its `HEADROOM_PROXY_TOKEN` must equal the backend's.") and the compose comments above; `docs/headroom/research-findings.md` and the spec line 190 mention the service. Update to "enable the `headroom` compose profile (`COMPOSE_PROFILES=app,headroom` or `full`)".
- Interplay: `codemie` is in profile `app`; with `COMPOSE_PROFILES=app` only, `http://headroom:8787` does not exist, so sidecar mode fails open (every call waits up to `HEADROOM_TIMEOUT_SECONDS` then falls back) - docs should say sidecar mode needs `app,headroom`.

### Environment Variables
`HEADROOM_MODE`, `HEADROOM_ENABLED`, `HEADROOM_SIDECAR_URL` (default `http://headroom:8787`), `HEADROOM_PROXY_TOKEN`, `HEADROOM_TIMEOUT_SECONDS`, `HEADROOM_MIN_TOKENS`, `HEADROOM_AGGRESSIVE_TARGET_RATIO`, `HEADROOM_SDK_ALLOW_MODEL_DOWNLOAD` (all in `src/codemie/configs/config.py`, documented in the guide Settings table). SDK-process env set by `sdk_compressor._apply_sdk_env`: `HEADROOM_OFFLINE|BEACON|CCR_BACKEND`, `DO_NOT_TRACK`.

### Configuration Files
`config/customer/customer-config.yaml` (`features:headroom`; uncommitted user modification present), `docker-compose.yml`, `.env.example` (no headroom entries on branch or main).

### Feature Flags and Deployment Concerns
Flag matrix in the guide. `deploy-templates/` (Helm) has no sidecar (raised by the reviewer as a question, not a comment to fix).

---

## 6. Risk Indicators

- Re-parenting depends on merging origin/main (17 commits, conflicts in docker-compose.yml and one alembic test). Doing it before the merge corrupts the chain. Merge order matters: resolve the single head only after main is in.
- docker-compose.yml merge must preserve the user's uncommitted edits (AWS passthrough, `8787:8787`) and committed `HEADROOM_OFFLINE`/`mem_limit`; the dirty working tree makes `git merge` refuse/overwrite risk - a stash/commit is needed first (user action). The `8787:8787` edit contradicts the "no host port is published" comment.
- Speculative: moving compressors to a subpackage changes ~20 test patch strings and two subprocess-script strings that only fail at runtime (test_sdk_compressor.py:158-170); easy to miss with grep for imports only. `test_factory.py` also relies on factory lazy-import behavior staying inside `get_compressor`.
- Speculative: a class-based singleton for the SDK state must keep (1) lazy, lock-protected first load, (2) the "failed import disables SDK for the process" semantic, (3) a reset hook used by `isolated_state` and ~4 tests, (4) `import codemie.service.headroom.sdk_compressor` not importing `headroom`. The module-level `_executor` is created at import; moving it into the singleton changes import-time side effects.
- Speculative: removing the Protocol changes `get_compressor`'s return annotation; the ABC option adds an inheritance edge between compressors and requires `mode` as class attr (already satisfied).
- The guide (`headroom-integration.md`) has uncommitted user edits and many hard-coded paths; doc updates for module layout, compose profile and README/.env.example (README/.env.example only exist after merging main) are easy to forget.
- Other `global` state in `factory.py` (`_legacy_warned`, `_sdk_missing_reported`) is likely to draw the same review comment.
- Not in scope per user's list but open: UI help text vs `_is_compressible` mismatch, Helm sidecar question, Draft title prefix.

---

## 7. Summary for Complexity Assessment

The change set is five small-to-medium edits, all in one service package plus config: (1) replace the module-level `global` state in `sdk_compressor.py` with the repo's singleton idiom (class with `_instance`/`_lock` double-checked `__new__`, as in `RequestSummaryManager`/`TokenExchangeService`, or a `get_instance()`/`reset_instance()` classmethod as in `MCPToolkitService`); (2) remove or make real the unused `HeadroomCompressor` Protocol (referenced only by the `factory.get_compressor` return annotation); (3) reorganize the 9-file flat `service/headroom` package, moving the compressor trio (and optionally `strategies`) into a subpackage while keeping models/resolver/scope/hook/factory high level - production importers only touch `models`, `scope`, `hook`, so the blast radius is the headroom test files (about 20 import/patch strings, including two subprocess code strings) and guide paths; no shims needed; (4) re-parent `f8bd0b700a64` onto `y1z2a3b4c5d6` and update one test constant; (5) add `profiles: [headroom, full]` to the compose service and document it.

Technical novelty is low (all patterns exist in the repo). The notable difficulty is sequencing and git hygiene: item 4 is impossible until `origin/main` (17 commits ahead, which alone contains `a8f23cf09c37` and `y1z2a3b4c5d6`) is merged or rebased in, the merge conflicts in docker-compose.yml and one alembic test, and the working tree has the user's uncommitted compose and customer-config edits that must be preserved.

Test posture is good for the package (about 80 tests across 7 files), with fixtures that reset module state and so constrain how the singleton is shaped. There are no tests for compose files. Risk is concentrated in the module move (mechanical but broad in tests), the merge itself, and keeping lazy SDK import semantics (`headroom` must not be imported at module import or in sidecar/off mode).

---

## 8. External References

None named by the task as a source of truth beyond the MR discussions file `/tmp/mr4461_discussions.json`, which resolved and was read (note ids: sdk_compressor.py:67 thread `f07e73cf`, compressor.py:36 `6e3d0803`, `__init__.py:14` `c3bd65f2`, alembic thread `461b7cab`, summary note `9d120fb9`). Key quoted facts: the alembic thread states `f8bd0b700a64.py:31` has `down_revision = "5189d4d2afaf"`, main added `a8f23cf09c37` then `y1z2a3b4c5d6`, merged `origin/main` locally at `8f7bcda50` gave heads `e091153f77dd` and `y1z2a3b4c5d6`, 6 migration tests failed (including `test_head_enum_matches_credential_types_model` and both new `test_migration_graph_has_single_head`); the summary note adds the questions about the Helm sidecar and the UI help text. `origin/main` head commit used for verification: `564fe17f1`.
