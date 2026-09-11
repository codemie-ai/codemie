# EPMCDME-11696 — Git-based FAQ Datasource — Completed Implementation Plan

> The task log below records the original B1 prototype work. Option A superseded its retrieval
> architecture on 2026-09-01. Current implementation requirements are summarized first; B1 details
> and evidence are retained only as decision history.

**Goal:** Ship `knowledge_base_git_faq` across backend, frontend, SDK, MCP, and documentation using Git
transport plus Confluence-style chunking and standard hybrid knowledge-base retrieval.

**Architecture:** Option A (see `docs/2026-09-01-faq-retrieval-strategy-comparison.md`).
`GitFaqLoader(GitBatchLoader)` emits parsed articles; `GitFaqDatasourceProcessor(BaseDatasourceProcessor)`
creates embedded H1-H3 section chunks with 3/1 window joining; `SearchAndRerankKB` performs retrieval.

**Tech Stack:** Python (SQLModel, FastAPI, elasticsearch, GitPython, alembic) · React 18 + TS
(codemie-ui) · pytest / vitest.

**Spec:** `docs/superpowers/tasks/2026-08-29-epmcdme-11696-faq-git-datasource/spec.md`
**Historical code-level detail:** `docs/2026-08-29-faq-git-datasource-implementation-plan.md`
describes the superseded B1 prototype.

## Global Constraints

- Type string is `knowledge_base_git_faq`.
- Never add Git FAQ to `CodeIndexType`, `_CODE_INDEX_TYPES`, or `_LLM_ROUTING_PROCESSOR_FACTORIES`.
- Parse one logical article per selected file, then split H1-H3 sections and join them in 3/1 windows.
- Use the base size splitter, embeddings pipeline, and `SearchAndRerankKB`; do not create FAQ TOC metadata.
- Resume unsupported: raise inside `_on_process_start` + `UNSUPPORTED_RESUME_TYPES` entry.
- Source-field edits (`faq_path`/`files_filter`/`branch`/`link`/`setting_id`) require `full_reindex=true` (422).
- Frontmatter detection is anchored at line 0 (corpus has mid-file `---` rules); first H1 wins.
- Commits: `EPMCDME-11696: <summary>` (CI-enforced). Pre-MR: `make verify` AND `make test-harness`.
- MRs touching the migration / security-relevant code request `/sanity` from a reviewer.
- New files carry Apache license headers.

---

## Current completed work

- [x] Parser preserves FAQ title, instructions, reference, references, and source metadata.
- [x] Loader supports `faq_path`, optional gitignore-style `files_filter`, Git auth modes, and public repos.
- [x] Processor performs H1-H3 splitting, 3/1 window joining, standard chunk splitting, and embeddings.
- [x] `knowledge_base_git_faq` uses the regular hybrid KB path with source selection and coverage handling.
- [x] API, persistence, migration, health checks, cron, webhook, and reindex lifecycle are wired.
- [x] Frontend, Python SDK, Node SDK, MCP tools, and test harness expose matching fields and routes.
- [x] Decision record, story, work item, active spec, integration guide, and MCP descriptions are aligned.

## Superseded B1 execution log

The tasks and evidence below describe the discarded whole-document/TOC prototype. They demonstrate
the implementation chronology but are not the final runtime contract.

### Task 1 (PR-1): FAQ markdown parser + fixtures

- [x] Copy fixture repo from `.zcode/faq-fixture/` to `tests/codemie/datasource/loader/fixtures/faq/`
- [x] Write `src/codemie/datasource/loader/faq_markdown_parser.py`: `FaqArticle` dataclass
      (title/content/instructions/reference/references), `FaqParseError`,
      `parse_faq_markdown(rel_path, raw, faq_path)`, `toc_entry()`, `normalize_faq_path()`
- [x] Implement precedence chains: title fm→H1→filename; instructions fm→`Prompt Instruction:`;
      reference fm→path-derived; line-1-only frontmatter; first-H1-wins; bad YAML → `FaqParseError`
- [x] Write `tests/.../test_faq_markdown_parser.py` parametrized over the fixture expectations table
- [x] `make verify` green · evidence/pr-1.json

### Task 2 (PR-2): BaseLLMRoutingDatasourceProcessor + Google refactor + search_kb registry

- [x] Extract mixin (`_add_texts`, `get_metadata`, `_update_metadata`, `get_table_of_contents`,
      `_save_table_of_contents`, `get_documents_by_checksum`, `_read_chapters`, `_update_kb_info`
      generalized to `version_id`) → `datasource/llm_routing_mixin.py`
- [x] `GoogleDocDatasourceProcessor` consumes the base class; ZERO behavior change
- [x] `search_kb.py`: replace hardcoded Google construction (`:297-301`) with factory registry
      (Google entry only in this PR)
- [x] Google test suite unchanged and green; new dispatch test; evidence/pr-2.json

### Task 3 (PR-3): GitFaqLoader

- [x] Refactor `_build_clone_url` to a link-string variant (only reads `repo.link` today); update
      `create_loader`/`test_connection` call sites + tests
- [x] Write `datasource/loader/git_faq_loader.py`: `GitFaqLoader(GitBatchLoader)` with
      primitives-based `create_loader` (no GitRepo), `_is_faq_file` filter, overridden `lazy_load`
      (parse via PR-1; `FaqParseError` → skip + stats), `load_with_extra()` → (docs, TOC sorted,
      commit sha), restricted `fetch_remote_stats()`
- [x] Loader tests (Mock(spec=Blob)/mock_open style); existing git loader tests stay green;
      evidence/pr-3.json

### Task 4 (PR-4): GitFaqDatasourceProcessor + IndexInfo.faq_path + migration

- [x] `FullDatasourceTypes.GIT_FAQ = "llm_routing_git_faq"` (`service/constants.py`)
- [x] `datasource/faq/git_faq_datasource_processor.py` per spec (constructor, `_index_name` via
      `KnowledgeBase`, `_init_index` with `IndexInfo.new(..., faq_path=...)`, `_init_loader` via
      `get_git_creds` (empty→public), `_process` per Global Constraints, resume rejection)
- [x] `IndexInfo`: `faq_path` column + `new()` kwarg (google_doc_link pattern)
- [x] Alembic migration `add_faq_path_to_index_info` (+ downgrade); up/down/up verified
- [x] Add FAQ factory to the search_kb registry; processor tests cloned from the Google test
      structure; evidence/pr-4.json

### Task 5 (PR-5): REST endpoints + health arm

- [x] `CreateKnowledgeBaseFaqRequest` / `UpdateKnowledgeBaseGitFaqRequest` models
- [x] `POST /v1/index/knowledge_base/git_faq` (Google create template + `_validate_git_credentials`)
- [x] `PUT /v1/index/knowledge_base/git_faq` (Google update template + source-edit/full-reindex 422 delta)
- [x] `DatasourceTypes.GIT_FAQ` + health-check arm reusing git probes
- [x] Router contract tests + handler validation tests; OpenAPI renders new schemas; evidence/pr-5.json

### Task 6 (PR-6): Trigger wiring

- [x] `validate_datasource` KB whitelist + `handle_datasource` FAQ branch → `reindex_git_faq` actor
- [x] Cron `__schedule_datasource_job` FAQ branch (no silent no-schedule)
- [x] `UNSUPPORTED_RESUME_TYPES` += `llm_routing_git_faq`
- [x] Trigger tests: webhook dispatches, cron schedules, stale-watchdog skips with warning;
      evidence/pr-6.json

### Task 7 (PR-7): Frontend (codemie-ui)

- [x] `INDEX_TYPES.FAQ` + type helpers (`getFullIndexType('faq')→'llm_routing_git_faq'`; NOT in code
      context list) + selector option + icon + humanize "Git FAQ"
- [x] `IndexTypeFaq.tsx` (repoLink, branch, faqPath `faq/`, IntegrationSection, InfoBoxes; no
      filesFilter/embeddings) + form wiring + Yup/defaults/hydration
- [x] `createOrUpdateFaqIndex` + store methods + `faq_path` response type
- [x] Details rows (link/branch/FAQ path) + KB action gating + hide Resume
- [x] vitest unit tests + `typecheck`; `npm run test-harness:fast` before MR; evidence/pr-7.json

### Task 8 (PR-8): SDK (codemie-sdk)

- [x] Python: `DataSourceType.FAQ`, `FaqDataSourceRequest`, service methods, response extraction
- [x] Node: enum, interfaces/params, mapper emit/extract (`faqPath`)
- [x] MCP datasources: `create_faq_datasource`/`update_faq_datasource` tools + README
- [x] Test harness passthrough (`index_type`, `faq_path`); SDK suites green; evidence/pr-8.json

### Task 9 (PR-9): Documentation

- [x] `.ai-run/guides/integration/faq-git-integration.md` + `AGENTS.md` guides-table row
      (format spec, config, reindex/webhook behavior, limits, repo-name-409 wording)
- [x] Post-release follow-up (tracked, not in this MR): public docs PR to `codemie-ai/docs` via
      Tech Writer skill — merge only after release
- [x] evidence/pr-9.json

---

## Verification matrix

| Task | Targeted tests | Gate |
|---|---|---|
| 1 | `pytest tests/codemie/datasource/loader/test_faq_markdown_parser.py` | `make verify` |
| 2 | `pytest tests/codemie/datasource/google_doc/` + dispatch test | Google regression = review bar |
| 3 | `pytest tests/codemie/datasource/loader/` | existing git tests green |
| 4 | `pytest tests/codemie/datasource/faq/` | migration up/down/up |
| 5 | `pytest tests/codemie/rest_api/routers/test_index_git_faq.py` | OpenAPI renders; `/sanity` in MR |
| 6 | `pytest tests/codemie/triggers/` | no silent failures |
| 7 | `npm run test:unit && npm run typecheck` | `test-harness:fast` pre-MR |
| 8 | SDK suites | — |

Pre-MR (every task): `make verify` + e2e sanity (`make test-harness` / `npm run test-harness[:fast]`).
Stop at "branch ready" — no model auto-merge; human opens the PR.

---

## Historical Follow-up Inputs (from stakeholder demo 2026-09-01)

Both inputs were incorporated before the final feature commit: FU-1 selected Option A and FU-2 added
`files_filter`. The original proposals are retained below for chronology.

### FU-1: Hybrid retrieval for large articles
- **Source**: Uladzislau Svetlakou — "don't we lose any data when we just summarize this document"
- **Problem**: articles > ~4KB may exceed useful context when whole-doc retrieval is the only path; no chunk/embed fallback exists
- **Proposal**: enhance `BaseLLMRoutingDatasourceProcessor` to optionally chunk+embed large articles alongside the whole-doc index; routing LLM still selects the article, vector search finds the fragment within it; benefits both Google Docs and Git FAQ
- **Resolution**: completed by the selected Confluence-style chunked hybrid flow

### FU-2: Flexible file filtering for Git FAQ
- **Source**: Nikita Levyankov — "user is able to specify for Git filters to include different folders, files; in your case you just hard-coded one input field"
- **Problem**: single `faq_path` input is less flexible than Git's `files_filter` (include/exclude patterns)
- **Proposal**: support include/exclude patterns alongside `faq_path` (backwards-compatible: `faq_path` remains the default include); UI adds the filter textarea when FAQ type is selected
- **Resolution**: completed with `files_filter` support in backend, frontend, and SDK contracts

Both stakeholder inputs are closed for this story.
