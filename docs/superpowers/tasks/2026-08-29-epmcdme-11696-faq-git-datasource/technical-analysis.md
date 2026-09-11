# Technical Research

> **Superseded architecture research (2026-09-01):** The Google Docs whole-document/TOC model
> analyzed below was replaced by the selected Confluence-style Option A implementation. Current Git
> FAQ uses `knowledge_base_git_faq`, embedded H1-H3 chunks, 3/1 section-window joining, and
> `SearchAndRerankKB`. This file is retained as pre-decision research, not implementation guidance.

**Task**: git faq datasource llm_routing loader processor
**Generated**: 2026-08-29
**Research path**: filesystem + parallel explore agents, cross-verified by direct reads

---

## 1. Original Context

EPMCDME-11696 — implement a Git-based FAQ datasource: `FaqGitLoader`, `FaqDatasourceProcessor`,
frontend integration, DB support. Type: Story. The FAQ corpus lives as Markdown in Git
(`codemie-ai/docs/faq/`: 269 files, flat, avg ~38 lines, 100% basic markdown with H1 question titles —
validated directly). The existing Google Docs datasource already implements the desired *processing*
model (whole articles, TOC in `_meta`, LLM routing); the existing Git code index already implements
the desired *transport* (clone, auth, tree walk). The story originally prescribed registering `faq` as
a `CodeIndexType`; team-lead decision changed this to a separate top-level `llm_routing_faq` type.

## 2. Codebase Findings

**Transport (Git).** `GitBatchLoader(GitLoader, BaseDatasourceLoader)` at
`src/codemie/datasource/loader/git_loader.py:195` — rmtree + shallow partial clone
(`--depth=1 --filter=blob:none`, `:273-309`); auth via `_build_clone_url` (PAT URL-embedded /
GitHub App installation token / Basic `http.extraHeader`, `:103-168`; only reads `repo.link`);
public repos clone anonymously. Tree walk `lazy_load()` (`:367-376`) skips submodules/symlinks/binary
MIME; unit of exchange is langchain `Document(metadata={source, file_path, file_name, file_type})`.
`fetch_remote_stats()` (`:311-365`) feeds `IndexInfo.start_progress`. `_validate_git_credentials`
(`rest_api/routers/index.py:2400-2435`) — public path `test_public_access`, else creds resolution,
422s with help text.

**Processing (Google Docs — the model to replicate).**
`GoogleDocDatasourceProcessor._process()` (`google_doc_datasource_processor.py:127-142`) bypasses the
chunk/embed pipeline: `load_with_extra()` → guardrails → `_add_texts` raw `elasticsearch.helpers.bulk`
(`content = f"{title}\n{content}"`, `metadata = {title, content, instructions, reference}`, `_id=uuid4`,
`move_progress(chunks_count=1)` per article) → `_update_kb_info` → `_save_table_of_contents` (strictly
AFTER bulk — `put_mapping` on a missing index 404s). `_meta` read-merge-write via `put_mapping`
(`:205-208`). Resume/incremental rejected inside `_on_process_start` (`:144-148`) — the safe error
shape (visible `error=True`, not stuck-in-progress).

**Retrieval.** `search_kb.py:145` dispatches on `"llm_routing" in index_info.index_type`;
`process_llm_routing_index` (`:292-318`) **hardcodes** `GoogleDocDatasourceProcessor(...)` (`:297-301`)
— must become a registry. `LLM_ROUTING_KB_PROMPT` says "section number" (cosmetic for path refs);
`LLMRouting.sections: list[str]`; `get_documents_by_checksum` matches `reference.startswith(ref)` with
sha512 dedupe — all format-agnostic, so path-derived references work unchanged.

**Type-string conventions (why B1).** `IndexTypeByContextTypeMapping` (`rest_api/models/index.py:89-103`,
startswith), `IndexInfo.is_code_index()` (`:1192-1196`, negative check → True for a plain `"faq"`),
`KnowledgeBaseIndexInfo.get_filter()` (`llm_routing` prefix), stale service `_CODE_INDEX_TYPES`
(`stale_datasource_service.py:62`, tool-usage metric names). A `"faq"` CodeIndexType would need ~6
permanent special cases and makes `get_search_tool` (`tool_execution_service.py:279-298`) hand back
CODE tools for unembedded docs; `llm_routing_faq` resolves all of these correctly by convention.

**Entities/endpoints.** `IndexInfo` already has `branch`/`link`/`setting_id` columns;
`IndexInfo.new(**kwargs)` (`index.py:699-739`) supports type-specific fields (`google_doc_link`
pattern). Google create route `routers/index.py:1259-1339`; update `:1375-1440` (cannot re-point its
source — FAQ adds editable source fields gated on `full_reindex`). `schedule(background_tasks,
func=None)` (`base_datasource_processor.py:118-133`). Uniqueness is `(project, repo_name)`
regardless of type (`_index_unique_check`, `:2363-2370`) — same repo needs a different datasource
name per type. Triggers: webhook `handle_datasource` allow-list + `CodeIndexType(...)` conversion
(`webhook.py:524-603`); cron membership tuple (`cron.py:699-831`, unmatched → log-only silent
no-schedule); `_RESUME_DISPATCH` / `UNSUPPORTED_RESUME_TYPES` (`actors/datasource.py:919-931, :653`).
Monitoring (`DatasourceMonitoringCallback`) and OpenAPI are fully generic — zero registration.

## 3. Documentation Findings

Team process lives in `codemie-onboarding/README.md` § Development Workflow (Jira lifecycle, CI-enforced
commit template `EPMCDME-<id>: <summary>`, `make verify` + `make test-harness` mandatory pre-MR,
author-merges-own-MR, docs via Tech Writer skill to `codemie-ai/docs`, merged only post-release).
The SDLC Factory (`codemie-public-skills/ai-packages/sdlc-factory`) governs runs; ~80 prior task
folders in `docs/superpowers/tasks/` establish the artifact conventions; doctor.json TTL 7d (currently
expired). `.ai-run/guides/` is the repo's AI guidance source (a new `faq-git-integration.md` guide +
AGENTS.md row is the internal docs deliverable).

## 4. Testing Landscape

Google processor test (`tests/codemie/datasource/google_doc/test_google_doc_datasource_processor.py`)
is the spec to clone: `mock_loader` returning `(DOCS, TITLES, DOC_ID)`, `processor.client = mock_elastic`,
patched `bulk` with exact request-list assertion, `_meta` merge assertion, checksum dedupe, resume
NotImplementedError. Loader tests use `Mock(spec=Blob)`/`mock_open`/patched `git_cmd.Git` — no real
repos. Dispatch tests pattern: SVN `TestCreateProcessor`. Router tests: TestClient contract style
(`test_index.py`) + direct handler style (`test_index_git_validation.py`). `tests/conftest.py` mocks
the DB engine; ES always mocked per-test. New files need Apache headers; `make verify` =
ruff + license + gitleaks + test.

## 5. Configuration and Environment

`faq_path` default `faq/` (normalized `faq`) — matches the real corpus root exactly. Local dev factory
(codemie-onboarding Steps 1–8) provides compose stack + superadmin; `make test-harness` runs the e2e
sanity suite (mandatory pre-MR; `--profile full` for all credentials). Corpus scale: 269 articles —
under `_read_chapters` `size=1000` cap; TOC prompt ~3–4k tokens (mitigated by first-question TOC
entries).

## 6. Risk Indicators

1. **Live Google refactor** (base-class extraction) — the only PR touching production behavior of an
   existing feature; regression bar = Google tests unchanged & green, isolated PR-2.
2. **Retrieval wiring** — both generic search paths are vector-based and return nothing on unembedded
   indexes; correctness rests on the `llm_routing` convention + registry dispatch (tested in PR-2/4).
3. **Silent-failure family** — cron no-schedule, webhook 500/stuck, stale-resume loop; dedicated
   trigger tests in PR-6; resume rejected inside `_on_process_start` + `UNSUPPORTED_RESUME_TYPES`.
4. **Stale classification** — must NOT be added to `_CODE_INDEX_TYPES` (wrong choice can mark active
   datasources STALE and, with deletion enabled, delete their ES index).
5. **DB migration** on `index_info` — auto-applied at startup; up/down/up cycle required in DoD;
   MR must request `/sanity`.
6. **Cross-repo delivery** (codemie, codemie-ui, codemie-sdk) — SDK is hand-written; nothing
   propagates automatically.
7. **Spec/plan approvals deferred** — reconcile via factory `mode: "sync"` after team-lead review;
   amended story text pending.

## 7. Summary for Complexity Assessment

Multi-PR feature across 3 repos: parser + loader + processor + base-class refactor + 2 REST endpoints +
`index_info` migration + trigger wiring + FE type/form/details + SDK (py/node/MCP/harness) + docs.
Requirements are fully specified (corpus-validated format spec, settled defaults, amended story);
technical risk concentrates in the Google base-class refactor and retrieval dispatch. Recommend the
existing PR-1…PR-9 split from the implementation plan; mode **standard** (not light).
