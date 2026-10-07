# EPMCDME-15109: Blast radius of the delivered changes

Date: 2026-09-26. Scope: every production symbol changed on backend branch
`EPMCDME-15110_default-project-foundation` (through `56baa9a1a`) and UI branch
`EPMCDME-15112_user-management-default-project` (through `693995c40`), traced to every consumer in
both repositories and in the `codemie_enterprise` package inside the built image.

## Consumers of changed symbols

| Changed symbol | Consumers found | Assessment |
|---|---|---|
| `User.current_project` (default → personal → sorted) | 15 sites. Billing: 9 `set_llm_context(None, user.current_project, …)` call sites (assistant/skill/workflow generation, marketplace indexing, workflow output changes) — covered by live tests. Non-billing: new conversation `project` (`conversation_service.py:1123`), virtual assistants (`assistant.py:983`, `ide.py:109`), prompt generation datasource scope (`assistant.py:1874`), generator index visibility (`assistant_generator_service.py:577,881`, `workflow_generator_service.py:68,179`), prebuilt assistant/workflow **templates** (`prebuilt_assistants.py:247`, `workflow.py:324`), plugin tools scope (`plugin_tools_info_service.py:62`), skill metric fallback. | No site persists an asset into the default project on its own. Conversation `project` feeds analytics filters only (listing is by user). Templates are returned with `project` **prefilled** to the default; the user still chooses on save. Visible behaviour change: template/generator prefills and virtual-assistant scope follow the default instead of the first-listed project — consistent with the story's "one authoritative answer for every downstream consumer". |
| `UserProjectRepository.get_by_user_id` / `aget_by_user_id` (now `ORDER BY project_name`) | 9 consumers: auth snapshots (`_finalize_authentication`, `load_user_for_auth`, `LocalIdp`), `/v1/user`, profile update, login response, admin project list, member-spend analytics, registration. | Order-only change. The UI never derives a "current" project from list position (no `projects[0]`/`applications[0]` use), so the only effect is alphabetical listing. |
| `ProjectInfo`, `ProjectInfoResponse`, `AdminUserProject` — `is_default` required | 7 constructor sites, all populate it; no `model_validate`/`parse_obj` of persisted or inbound JSON; enterprise package has zero `codemie.*` imports. | Safe. A forgotten site now fails at construction (intended). |
| `LiteLLMContext` (+`budget_fallback_from`, `personal_project`) | Single constructor (`utils.py:148`); consumers: `dependecies.get_current_project/get_budget_fallback_from`, llm_factory, monitoring. Not constructed by the enterprise package. | Safe. Contextvar copies into threads carry the new fields. |
| `set_llm_context(…, llm_model=None)` | 24 callers; agents and the chat-history handler pass the model, routers do not (falls back to `asset.llm_model_type`, else runtime recording in the same thread). All router callers are sync `def` (threadpool), so the synchronous budget probe never blocks the event loop. | Safe. Router aliases unknown to `llm_service.get_model_details` degrade to "no prediction" and rely on runtime recording. |
| `send_log_metric` / `send_count_metric` (+`budget_fallback_from` attribute) | Every business metric; sink is OTLP → Elasticsearch `codemie_metrics_logs*` with dynamic mapping (no strict mapping in repo). | Additive field; dashboards ignore it until used. |
| `_probe_direct_project_budget_scopes` (+`b.is_active = TRUE`) | `llm_factory` runtime availability, `utils._unfunded_project`. `resolve_sync` and the async batch probe already filtered inactive budgets, so scopes and resolution now agree. | Behaviour change only for inactive project budgets: they no longer count as a funded scope (they were never actually charged). |
| `UserAccessService` (cache invalidation on grant/update/revoke/set/clear) | In-process `_auth_token_cache` only; `replicaCount: 1` in `deploy-templates/values.yaml`. | With more replicas, other pods still rely on the 30 s TTL — within the story's "about one minute". |
| `set_default`/`clear_default` (`SELECT … FOR UPDATE` on `users`) | Login updates `users.last_login_at`; bulk/single removal delete `user_projects` rows; neither takes the user lock after touching `user_projects`, so no lock-order cycle. | Login may wait briefly behind an in-flight default change for the same user. |
| Migration `bf3cb9db22b7` | `ADD COLUMN … NOT NULL DEFAULT false` is metadata-only on PostgreSQL ≥ 11; `CREATE UNIQUE INDEX` (non-concurrent) takes a share lock on `user_projects` for the build. | Table is users × memberships; build is sub-second at realistic sizes. |
| UI `UserProjectsTable.onProjectsChange(update?)`, `UserDetailsPopup` | Single consumer (`UsersManagementPage`); `UserAssignedProject.is_default` required — `tsc` clean. | Contained. |

## Validation performed for this sweep

- Enterprise package inside the image: 105 modules, zero `codemie.*` imports, no reference to any
  changed symbol.
- Full backend test suite on the final branch (`pytest tests/ --import-mode=importlib`, single
  process, 21 min): 16557 passed, 163 failed, 190 skipped. Every failure was classified by
  re-running the failing files at the merge-base (`6424c51e8`) with the same interpreter:
  - 160 fail identically at the merge-base or are Windows-only: code-executor filesystem sandbox
    (POSIX `resource`/`/proc`), git-hook script tests, enterprise `mcp_auth`/`switchyard` bridges
    (venv carries an older enterprise stub), mime-type/docx/multiprocessing environment cases.
  - **3 were regressions from this story that the targeted subsets never covered** — fixed in
    `49ecf4f87` (test updates only, no production change):
    - `test_settings_litellm.py::TestLiteLLMContext::test_context_serialization` — the expected
      `model_dump()` lacked the new `personal_project` / `budget_fallback_from` keys.
    - `test_user_management_router_crud.py::TestGetUserProjects::test_get_user_projects_success` —
      mocked service payload lacked the now-required `is_default`, so `AdminUserProject(**row)`
      raised. Confirms the "forgotten site fails at construction" property above.
    - `test_plugin_tools_info_service.py::…::test_get_plugin_toolkit_info_uses_default_project` —
      the service resolves through `User.current_project` instead of `project_names[0]`; the
      `Mock(spec=User)` fixture did not set it. This is the one production behaviour change
      outside billing that a pre-existing test pinned: plugin-tool scope now follows the default.
  Why the earlier passes missed them: every pass ran per-file or per-directory subsets; the full
  suite (no xdist in this venv) had never been run on the final branch until this sweep.
- Full frontend suite (unit + integration, 2 threads): see "Frontend suite" below.
- Live: category-aware fallback (CLI-only funded default), regression subset, UI flows — recorded
  in the three tickets' `post-analysis-fixes.md`.

### Frontend suite

Three attempts on 2026-09-26, none reached the integration project end-to-end:

- Default parallelism: node segfaulted after 493 files passed, 0 failed.
- Unit project, 2 threads: 287 files / 2947 tests passed; 5 files failed. Two are genuine on this
  machine — `modalSurfaces.guard.test.ts` (the guard builds paths with `path.relative`, so on
  Windows it never contains `components/Popup/Popup.tsx`; deterministic, unrelated to the story)
  and `Popup.test.tsx` focus-trap (passed on the next run; flaky under load). The other three were
  load errors caused by a `node_modules` junction that a worktree removal followed and partially
  deleted mid-run (restored with `npm ci`, 1247 packages); not test failures.
- Unit project after `npm ci`: 280 files passed, only the Windows path-separator guard failed,
  before the host killed the run for low memory. Not restarted.

None of the 12 files the branch touches overlaps a failing file. The story's own suites were run
in isolation and are recorded in the EPMCDME-15112 `post-analysis-fixes.md` (7 unit files / 60
tests, the integration file, `tsc`, `eslint`). A full integration-project run on this branch is
still outstanding and should be run on CI or a machine with more memory.

## Residual observations (not changed)

- `llm_proxy_requests_total` (a request counter, no money) is emitted before budget resolution and
  still reports the routing project alongside `budget_fallback_from`; the usage/money metric is
  attributed correctly.
- Personal identity spelling differs between the proxy (`username`) and web (`email`) paths; both
  are the provider's customer keys, so analytics for personal spend group under two labels for
  users whose username ≠ email. Pre-existing; re-keying would move historical spend.
- Users created through the admin API get their personal membership row on first authenticated
  request, so "personal project as default" is only offered after the user has used the app once.
