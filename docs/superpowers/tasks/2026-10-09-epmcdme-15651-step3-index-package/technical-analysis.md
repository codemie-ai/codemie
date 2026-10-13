# Technical Research

**Task**: index router package split
**Generated**: 2026-10-09
**Research path**: codegraph

---

## 1. Original Context

Implement delivery step 3 of Jira EPMCDME-15651 (split oversized REST API router modules): convert src/codemie/rest_api/routers/index.py into an `index/` package whose __init__.py builds one combined `router` and exports only `router` (no re-exports), split by concern per the ticket's plan item 3; keep move-only, no API change; re-point importers/callers/patch targets (src and tests). The ticket body is in docs/superpowers/tasks/2026-10-08-epmcdme-15651-split-router-modules/ticket.md and the user-resolved criteria in .../spec.md in the same dir. Steps 0-2 are done on this branch: step 1 in docs/superpowers/tasks/2026-10-08-epmcdme-15651-step1-models-move/, step 2 (assistant/ package, the closest precedent: package layout, private support modules, patch-target retargeting rules, snapshot treatment) in docs/superpowers/tasks/2026-10-08-epmcdme-15651-step2-assistant-package/. Guard tests: tests/codemie/rest_api/router_guards. The spec must decide the OpenAPI snapshot treatment of any router-local Pydantic class component-key renames if those classes move.

Notes from earlier steps to build on (do not re-derive): src/codemie/rest_api/routers/index.py is 2,884 lines. Step 0's research (docs/superpowers/tasks/2026-10-08-epmcdme-15651-split-router-modules/technical-analysis.md) covers the five target routers; step 2's technical-analysis.md and spec.md show the format and the findings that mattered (route include order, 500-line module cap, helpers shared between modules, patch retargeting, OpenAPI component-key renames for same-named classes). Focus the new research on: the route/handler inventory of index.py grouped by concern with line ranges (the ticket's plan item 3 gives the intended module list), module-level helpers and which handlers share them, router-local Pydantic classes and any same-named classes elsewhere (OpenAPI component-key rename risk, give the exact baseline keys/line numbers in tests/codemie/rest_api/router_guards/baselines/openapi.json), every importer and every `patch("codemie.rest_api.routers.index...")` / `routers.index` reference in src/ and tests/ (counts per file, including tests/unit/routers and tests/codemie/rest_api), route-precedence-sensitive pairs involving index routes (static vs dynamic path conflicts, include order), `dependencies=` / auth on the router and on individual routes (to preserve), and any auth/access-check helpers that would move.

---

## 2. Codebase Findings

Tools were Read, Glob and codegraph (no grep, nothing executed): counts are exact for the files read, not a tree-wide grep.

### Existing Implementations

`src/codemie/rest_api/routers/index.py`: 2,884 lines, 45 routes (step 0 said 44), 17 private helpers, 13 message constants, 3 constant maps/tuples, 2 router-local Pydantic classes, no module-level state. `router = APIRouter(tags=["Indexing"], prefix="/v1", dependencies=[Depends(authenticate)])` L192; no route sets `tags=`; `logger` is the shared `codemie.configs.logger`.

| Ticket module | Lines (decorator to end) | Contents |
|---|---|---|
| queries | 195-454, contiguous, 11 routes | GET `/index` 195, `/index/users` 229, `/index/find_id` 237, `/index/{index_id}` 256; POST `/index/{datasource_id}/search` 288; GET `.../{index_id}/export` 308, `/settings` 330, `/assistants` 364, `/elasticsearch` 374; GET `/knowledge_bases` 347; DELETE `/index/{index_id}` 418 |
| code_repositories | 457-1018 (562 lines), 5 routes | classes `CreateIndexRequest` 457, `CreateSVNIndexRequest` 468; POST `/application/{app_name}/index` 479, `.../index/svn` 569; PUT `.../index/svn/{repo_name}` 661, `.../index/{repo_name}` 888; GET `/application/{app_name}` 987; maps 546-566; helpers `_update_svn_index_and_repo_fields` 625, `_handle_index_and_repository_update` 746, `_get_repository_with_project_change` 789, `_handle_svn_reindex` 832 (called by the git PUT) |
| KB, 22 routes | 1021-2459 | below |
| providers | 2462-2618, contiguous, 5 routes | POST `/index/provider`; PUT `/index/provider/{index_info_id}/reindex` and `/{index_info_id}`; GET `.../datasources/import/availability` 2558 and POST `.../datasources/import` 2579 (both `admin_access_only`) |
| no home in the ticket list | 2621-2667 | POST `/index/health` 2621; GET `/index/jira/fields` 2637 (also unnamed: `/knowledge_bases`, `GET /application/{app_name}`, `DELETE /index/{id}`) |
| shared helpers | 155, 2670-2884 | `_parse_jwt_exp`; `_get_index_by_id_or_raise` 2670; `_get_elasticsearch_stats` 2685; `_update_datasource_scheduler` 2723; `_index_unique_check` 2751; `_kb_demo_user_check` 2761; `_validate_remote_entities_and_raise` 2776; `_validate_git_credentials` 2788; `_validate_project_change` 2841 |

KB routes (POST / PUT line): Atlassian confluence 1021 / 1710, jira 1076 / 1787, xray 1120 / 1885; Azure DevOps wiki 1161 / 1965, work_item 2046 / 2173; xWiki 1193 / 2077; SharePoint 1235 / 2338 (+ `_get_sharepoint_creds_for_reindex` 2252, `_create_sharepoint_processor_for_reindex` 2282, `_parse_jwt_exp`); Google 1311 / 1427, plus PUT `google/reindex` 1394; Git FAQ 1494 / 1583, plus PUT `git_faq/reindex` 1656 (+ `_GIT_FAQ_SOURCE_FIELDS` 1534, `_build_git_faq_processor_from_index` 1537, `_git_faq_update_params` 1560); Files POST 2415 / PUT 1688 (PUT first). Source order interleaves the types: POST confluence, jira, xray, wiki, xwiki, sharepoint, google; PUT google/reindex, google; POST git_faq; PUT git_faq, git_faq/reindex, file, confluence, jira, xray, wiki; POST work_item; PUT xwiki, work_item, sharepoint; POST file. No per-type module layout reproduces it.

Sizes from the ranges (before 30-50 import lines per module): the code_repositories block alone is 562 lines, over the 500 cap; the seven Atlassian routes sum to about 414. 14 handlers exceed 60 lines, decorator to end (largest: update_index_application 97, jira PUT 96, xwiki PUT 94).

### Architecture and Layers Affected

API router layer only, plus tests, one guide row and the guard baselines. `main.py` is unchanged.

### Integration Points

- `main.py` L65 `from codemie.rest_api.routers import index` (unconditional, so all submodules import at start); L1035-1036 `if ElasticSearchClient.is_configured(): app.include_router(index.router)`, between `category` and `common`. It is the only src importer found: codegraph shows every index.py helper, constant and handler called only inside index.py (`preconfigured_assistants.get_index_info` is an unrelated same-named function); `codemie-enterprise` returned no match. Both are unprovable without grep.
- Uses `routers/utils.py` (`raise_access_denied`, `raise_forbidden`, `raise_unprocessable_entity`, `raise_not_found`) and `security/authentication.py`: `authenticate` (sets `request.state.user`), `admin_access_only` and `application_access_check(request, app_name)` (both read `request.state.user`). None is defined in index.py.
- Route precedence: `route_precedence.json` has 45 pairs, only 2 touch index, both inside queries: GET `/v1/index/users` and `/v1/index/find_id` before GET `/v1/index/{index_id}` (L229, L237, L256). No cross-router pair involves index. Same-path multi-method sets: `/v1/index/{index_id}` GET 256 then DELETE 418; each KB path POST then PUT except `knowledge_base/file` (PUT 1688, POST 2415). Starlette answers 405 from the first PARTIAL match (`starlette/routing.py` L682-691), so this order shows in the `Allow` header. The guards check baseline pairs only and their route dump is `{path, methods}` (`app_probe.py` L79-84), without names or dependencies.
- FastAPI `include_router` (`fastapi/routing.py` L1461-1525) merges include-arg and route dependencies, then `add_api_route` puts the combined router's own dependencies first. The assistant precedent had `dependencies=[]`; index is the first package with a non-empty router-level list.

### Patterns and Conventions

- Auth to preserve: router-level `Depends(authenticate)`; route-level `application_access_check` on 5 routes (L479, 569, 661, 888, 987), `admin_access_only` on 2 (L2558, 2579). 26 handlers also declare `user: User = Depends(authenticate)`; 19 do not and rely on the router-level dependency for `request.state.user` (`/knowledge_bases`, `get_assistants_using_index` which has no per-resource check, `get_application`, 10 KB creates, google reindex, files PUT, sharepoint PUT, provider create, health, jira/fields). Inline `Ability(user).can` with `raise_forbidden`/`raise_access_denied` in 10 handlers (7 queries, svn PUT, git PUT, provider update).
- Auth-related helpers: `_validate_project_change` (target project exists, `user.has_access_to_application`), `_kb_demo_user_check` (demo limit), `_validate_remote_entities_and_raise` (Bedrock entity cleanup), `_parse_jwt_exp` (JWT decode without signature check; `service/security/jwt_utils.parse_jwt_exp` is a public near-duplicate).
- Helper sharing: `_index_unique_check`: code_repositories (2), 10 KB creates, providers (1). `_kb_demo_user_check`: the 10 KB creates. `_validate_project_change`: code_repositories (2) and 9 KB updates. `_update_datasource_scheduler`: code_repositories (3) and the same 9. `_validate_git_credentials`: code_repositories (2) and Git FAQ (3). Single-owner: `_get_index_by_id_or_raise` (3 sites), `_get_elasticsearch_stats`, `_validate_remote_entities_and_raise` in queries; `_parse_jwt_exp` in SharePoint.
- Constants shared across modules: INDEX_NOT_FOUND_MESSAGE/HELP (14 uses), APPLICATION_NOT_FOUND_MESSAGE/HELP, INVALID_INPUT_PARAMETERS_HELP, INCORRECT_DATASOURCE_SETUP_MESSAGE (10), EDIT_SUCCESSFUL (9). Single-module: SVN_REPOSITORY_NOT_FOUND_MESSAGE, CHECK_PERMISSIONS_MESSAGE, ACCESS_DENIED_MESSAGE, INDEX_EXISTS_MESSAGE/HELP. PROJECT_CHANGE_LOG_MESSAGE (L189) is referenced nowhere in index.py or the 5 test files. Function-local imports: `ElasticSearchClient` L2696, `SchedulerSettingsService` L2739.
- Step 2 precedent (`routers/assistant/`): `__init__` holds `router = APIRouter(tags, prefix="/v1", dependencies=[])`, one `include_router` per bare-`APIRouter()` module, `__all__ = ["router"]`; underscore private modules; largest module 481 lines.

### OpenAPI component keys (`baselines/openapi.json`)

| Item | Line |
|---|---|
| `"CreateIndexRequest"` (description = class docstring) | L7403-7612 |
| its `$ref`, POST `/v1/application/{app_name}/index` (path key L52431) | L52449 |
| `"CreateSVNIndexRequest"` | L7658 |
| its `$ref`, POST `/v1/application/{app_name}/index/svn` (path key L52491) | L52509 |

Both are plain unique keys and the only router-local classes. `components.schemas` ends at L34715; its module-qualified tail is `codemie__rest_api__routers__assistant__marketplace__{PublishToMarketplaceRequest L34644, ReactionRequest L34685}` and `codemie__rest_api__routers__skill__ReactionRequest` L34703; no `codemie__rest_api__routers__index__*` key exists (it would sort between them). `GET /v1/application/{app_name}` returns `codemie__core__models__Application` (`$ref` L52400), named by its defining module. Docstrings of classes and handlers reach the schema as `description`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

`.ai-run/guides/integration/faq-git-integration.md` row 70 cites `src/codemie/rest_api/routers/index.py` for `POST/PUT /v1/index/knowledge_base/git_faq`: the only guide reference found. No index.py reference in the jira, confluence, xray, google-docs, endpoint-conventions, project-structure and quality-gates guides. `rest-api-patterns.md` and `layered-architecture.md` carry stale `main.py:658/629/706/804` cites (pre-existing).

### Architectural Decisions

No ADR. The ticket's module list and the resolved criteria are in Section 8.

### Derived Conventions

Step-2 rules: bodies, decorators, docstrings and function-local imports byte-identical; helpers keep names; underscore names only imported from private modules; patch the module that resolves the name; one baseline regeneration after all moves, checked against the expected edits; a one-off before/after script over (method, path, name, dependency names).

---

## 4. Testing Landscape

### Existing Coverage

Five files reference the router (ticket: 5). `tests/unit/routers` has 2 unrelated-named files (not opened); `test_settings_cron_validation.py` read, unrelated.

| File | Patches | Import statements |
|---|---|---|
| `tests/codemie/rest_api/routers/test_index.py` (1,479 lines) | 90 | 8: `router` L28, `_INDEX_FIELD_MAP`, `_parse_jwt_exp` x4, `_update_datasource_scheduler` x2 |
| `.../routers/test_index_git_validation.py` | 56 | 10: `CreateIndexRequest`, `create_index_application` x4, `update_index_application` x5 |
| `.../routers/test_index_git_faq.py` | 20 | 1: `_build_git_faq_processor_from_index` (also imports `app` from test_index) |
| `tests/codemie/rest_api/test_xwiki_index_endpoints.py` | 3 (f-string base L108) | 5: `router`, `index_knowledge_base_xwiki`, `update_knowledge_base_xwiki` x3, `INCORRECT_DATASOURCE_SETUP_MESSAGE`; `inspect.getsource` assertions on 3 handlers |
| `tests/codemie/rest_api/test_main_retrieval_backend_disabled.py` | 0 | 1: `from codemie.rest_api.routers import index`, reads `index.router.routes` |

Totals: 166 literal plus 3 f-string patch strings (ticket: 168), 25 import statements. Ticket module whose code resolves each patched name: test_index.py: kb_atlassian 33 (if `jira/fields` stays there), kb_files 20, kb_azure_devops 15, code_repositories 12, `_common`-bound helpers 5, queries 3, kb_xwiki 2; test_index_git_validation.py: code_repositories 47 (incl. whole-name `Ability` x5, `ensure_application_exists` x4, both `*_in_background`), `_common`-bound 9; test_index_git_faq.py: all 20 in kb_git_faq. The 14 `_common`-bound patches (`SettingsService.get_git_creds` 9, `GitBatchLoader.test_public_access` 5) resolve inside `_validate_git_credentials` while the handler under test is a code_repositories route.

### Testing Framework and Patterns

pytest, real `app`, `TestClient`, `dependency_overrides[authenticate]` (a fake that sets `request.state.user`), `mock.patch`. Attribute patches on shared classes (`SettingsService.get_*`, `Application.get_by_id`, `KnowledgeBaseIndexInfo.filter_by_project_and_repo`) work through any module holding the name; whole-name patches (`_index_unique_check`, `_kb_demo_user_check`, `_validate_git_credentials`, `_validate_project_change`, `Ability`, processors) bind to the using module. Guards: `poetry run pytest tests/codemie/rest_api/router_guards -n 0 --show-capture=no -q`; the guarded app has index on (`test_guarded_app_profile.py`).

### Coverage Gaps

20 of 45 handlers are exercised in the five files. Untested: GET `/index`, `/index/{id}`, search, export, settings, `/knowledge_bases`, assistants; svn create and update; `get_application`; POST confluence, xray, ado wiki, ado work_item, sharepoint, google; PUT google, google/reindex, sharepoint; the 5 provider routes; `/index/health`.

---

## 5. Configuration and Environment

### Environment Variables

`RETRIEVAL_BACKEND` and `ELASTIC_URL` decide registration (`is_configured()` = `retrieval_available(config) and bool(config.ELASTIC_URL)`); the guard profile pins `RETRIEVAL_BACKEND=elasticsearch`, `ELASTIC_URL=http://localhost:9200`, `ENABLE_USER_MANAGEMENT=true`. get_index_info reads `STORAGE_CONFIG.processed_documents_threshold`. index.py reads no env var itself.

### Configuration Files

`pyproject.toml`: ruff E,F,B,N,C4,G,T20,RSE,SIM,C,W,PERF,ISC, mccabe 16, line length 120; no per-file ignore for index.py. `sonar-project.properties` and `.coveragerc` have no per-file entries. Gates: `make ruff`, `make license-check`, tests, `make test-harness` in the MR. Baselines regenerate with `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`.

### Feature Flags and Deployment Concerns

None inside index.py, only the ES gate in `main.py`. No pipeline; one MR after the whole ticket (HANDOFF), which asks a reviewer for `/sanity`. Untracked `opencode.json.lock` and `skills-lock.json` are never staged.

---

## 6. Risk Indicators

- Speculative: per-type KB modules reorder the 22 KB routes, so "sequence equals pre-move" must be restated (same set, baseline pairs ordered, same-path method order kept together); guards cover 2 index pairs only.
- Speculative: code_repositories (562 lines) and Atlassian (about 470 with `jira/fields`) need a private support module and homes for `health`/`jira/fields` to stay under 500.
- Speculative: 5 shared helpers and 7 shared constants need `_common`; 14 patch strings resolve outside the handler's module, and stale whole-name patches (`Ability`, `_index_unique_check`) fail silently.
- 19 handlers rely only on router-level `authenticate`. Speculative: keep sub-routers bare and `dependencies=[Depends(authenticate)]` on the combined router; dependency order and 405 `Allow` order are unguarded.
- Speculative: a one-off route and dependency script is needed; the guard dump lacks names.
- `test_xwiki_index_endpoints.py` uses `inspect.getsource`: bodies must move verbatim.
- 25 of 45 handlers have no direct test.
- Speculative: no OpenAPI or precedence baseline change.
- `/sanity` scope: `_validate_project_change`, `_kb_demo_user_check`, `_parse_jwt_exp`, access dependencies.
- Unverified: no tree-wide grep.

---

## 7. Summary for Complexity Assessment

One 2,884-line file with 45 routes; the ticket lists 11 modules plus `__init__.py`. Router layer only. Surface: 5 test files (169 patch strings, 25 import statements), one guide row, no `main.py` change, and no expected guard-baseline change because both router-local classes have plain unique OpenAPI keys. This is the second use of the step-2 pattern, so novelty is low; new here are a non-empty router-level dependency list with route-level access dependencies, and a KB block whose source order cannot survive a per-type split.

Only 20 of 45 handlers are tested. The work is mostly retargeting patches to the module that resolves each name, under the 500-line cap that the 562-line code_repositories block exceeds as one module. Plan estimate: L, about 2 days.

---

## 8. External References

Read in full: `docs/superpowers/tasks/2026-10-08-epmcdme-15651-split-router-modules/ticket.md`: plan item 3 modules `_common`, queries, code_repositories, kb_atlassian, kb_azure_devops, kb_google, kb_sharepoint, kb_xwiki, kb_git_faq, kb_files, providers; `__init__` exports only `router`. Its `spec.md`: 500-line target binds, OpenAPI identical except approved re-baselines, 45 precedence pairs, move-only, `/sanity` for auth code. Step-2 `spec.md`: patch and underscore rules. `local/router-split/HANDOFF.md`: next step, commit rules.
