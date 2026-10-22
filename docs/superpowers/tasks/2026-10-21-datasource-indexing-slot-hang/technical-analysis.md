# Technical Research

**Task**: datasource indexing slot hang
**Generated**: 2026-10-21
**Research path**: codegraph

---

## 1. Original Context

Review Jira EPMCDME-16237 and implement it. The detailed requirements/details document is at the task input document (read it first; it is an input document, not the pipeline plan; do not commit or link it, it will be removed later). The Jira ticket body may also appear at ticket.local.md in the same directory if already written. Treat these as the verbatim requirements.

(feature_area: `datasource indexing slot hang`. `ticket.local.md` was not present in the run dir; only the task input document and `state.local.json`.)

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/datasource/datasource_concurrency_manager.py` — `DatasourceConcurrencyManager` (threading.Semaphore). Module singleton `datasource_concurrency_manager = _create_manager()` reads `config.MAX_CONCURRENT_DATASOURCE_INDEXING`, `DATASOURCE_CONCURRENCY_LIMIT_ENABLED`, `DATASOURCE_QUEUE_TIMEOUT`. Module logger is stdlib `logging.getLogger(__name__)`; imports only `logging`, `threading`, `typing`; no `uuid`/`time` import.
  - `run(self, process_func, index_info=None)` at ~L86: if disabled -> `process_func(); return`. Otherwise `acquire(blocking=False)`; if not acquired -> `_try_set_queued`, blocking `acquire(timeout=self._queue_timeout)`; on timeout logs error (uses `getattr(index_info, 'repo_name', 'unknown')` and `id`), `_try_set_error(_QUEUE_TIMEOUT_ERROR)`, returns. Then `try: [clear queued]; process_func() finally: self._semaphore.release()`. No slot-acquire/release logging exists today.
  - Helpers `_try_set_queued/_try_clear_queued/_try_set_error` swallow and log exceptions.
  - `_try_clear_queued` runs inside the protected `try` (so a hang there holds the slot, as the plan notes).
- `src/codemie/datasource/code/code_datasource_processor.py` L163-177 — `run_in_background(process_func, git_repo_name, background_tasks)` defines `do_work()` (multiprocessing via `_run_in_subprocess` or direct call) and `task()` which calls `datasource_concurrency_manager.run(do_work)` with a comment that `index_info=None` because code processors create `IndexInfo` lazily. `git_repo_name` is already in scope. Callers: `index_code_datasource_in_background`, `update_code_datasource_in_background`, `src/codemie/datasource/svn/svn_index_service.py` (create/update), `src/codemie/rest_api/routers/index/code_repositories.py`.
- Other `datasource_concurrency_manager.run(...)` callers (15 total; signature must stay compatible): `src/codemie/triggers/actors/datasource.py` (e.g. `reindex_svn` -> `run(processor.reprocess, processor.index)`; reindex_code/jira/confluence/google/git_faq/xwiki...), `src/codemie/rest_api/routers/index/providers.py`, `src/codemie/rest_api/routers/index/queries.py`. `operation` names come from callable `__name__` (`process`, `reprocess`, `incremental_reindex`, `resume`, `do_work`).
- `src/codemie/datasource/loader/azure_devops_wiki_loader.py` — `AzureDevOpsWikiLoader`. `self._session` is a `requests.Session` created in `_init_session`. Three untimed direct calls, all `self._session.get(url, params=params)` inside `try/except Exception`:
  - `_get_page_comments` (~L429) -> on error logs warning, returns `[]`.
  - `_get_wiki_attachments_index` (~L525) -> on error logs warning, caches and returns (possibly empty) index.
  - `_download_wiki_attachment` (~L564) -> on error returns `None` (handler after L571).
  - Imports: `from codemie.configs import logger` (L32); `config` is not imported yet. `from __future__ import annotations` present.
  - Work-item attachment downloads already use `timeout=60` (not touched).
- `src/codemie/enterprise/litellm/llm_factory.py` — `_build_embedding_model_params()` (L357-376) returns dict with `"max_retries": 10`, no `request_timeout`; imports `config` lazily inside the function via `from codemie.configs import config`. Consumed by `create_litellm_embedding_model()` (L928) which builds `LiteLLMAzureOpenAIEmbeddings` (L97, subclass of `AzureOpenAIEmbeddings`, wraps `self.client` with `EmbeddingClientWrapper`).
- `src/codemie/core/dependecies.py` — `get_embeddings_model()`: tries `get_litellm_embedding_model(...)` first; then Vertex (`max_retries=10`), Bedrock (no change), else native `AzureOpenAIEmbeddings(...)` at L94-105 with `max_retries=10`, `show_progress_bar=True`, `check_embedding_ctx_length=False` and no timeout. `config` already imported (`from codemie.configs import config, logger`).
- `src/codemie/configs/config.py` — central `class Config(BaseSettings)` (pydantic-settings, starts ~L45, fields run past L970; singleton `config = Config()` at file end). `DATASOURCE_QUEUE_TIMEOUT`, `MAX_CONCURRENT_DATASOURCE_INDEXING`, `DATASOURCE_CONCURRENCY_LIMIT_ENABLED` are fields here (exact lines not surfaced by codegraph; locate when editing). Env var override is automatic via BaseSettings.

### Architecture and Layers Affected

- Datasource orchestration/concurrency (`codemie.datasource.datasource_concurrency_manager`), code-datasource entry (`codemie.datasource.code`).
- Datasource loader layer (`codemie.datasource.loader.azure_devops_wiki_loader`).
- Embeddings factory / LLM integration layer (`codemie.core.dependecies`, `codemie.enterprise.litellm.llm_factory`).
- Central configuration (`codemie.configs.config`).

### Integration Points

- Semaphore callers: FastAPI BackgroundTasks (anyio threadpool), cron/trigger actors (`triggers/actors/datasource.py` with `@with_datasource_job_lock`), routers.
- `get_embeddings_model` is used by indexing, `get_elasticsearch` (retrieval/query embeddings) and others, so the timeout also affects query-time embeddings.
- Azure DevOps REST (direct `requests`) vs. Azure DevOps SDK (`WikiClient`, unchanged).

### Patterns and Conventions

- Central config via `from codemie.configs import config, logger`; guide `.ai-run/guides/development/configuration-patterns.md` says add/reuse central config values, no direct env reads in feature code.
- Manager logs use f-strings with prefix `DatasourceConcurrencyManager. ...` and stdlib logger.
- Project memory: user does not want explanatory code comments added in code changes.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/development/configuration-patterns.md` — central Config pattern; no datasource-timeout specifics.
- No guide specific to the datasource concurrency manager found; other relevant guides listed in AGENTS.md (logging-patterns, error-handling, testing-patterns, quality-gates) were not read in this pass.

### Architectural Decisions

None recorded for the concurrency manager. Docstring on the class describes queue/timeout semantics.

### Derived Conventions

- Timeout config as a typed field on `Config` with env override; XWiki loader has its own `request_timeout_seconds` in datasources YAML (`XWikiDatasourceConfig`) — the plan explicitly forbids following that pattern here (no YAML, no `AzureDevOpsWikiConfig` change).

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/datasource/loader/test_azure_devops_wiki_loader.py` — wiki loader (covers `_get_wiki_attachments_index`).
- `tests/enterprise/litellm/test_llm_factory.py` — LiteLLM factory (covers `_build_embedding_model_params` via callers); `tests/enterprise/litellm/test_embedding_wrapper.py` covers `LiteLLMAzureOpenAIEmbeddings`.
- `tests/codemie/core/test_dependecies.py` — `get_embeddings_model` area.
- `tests/codemie/datasource/code/test_background_processing.py` — `run_in_background`/`do_work`.
- `tests/codemie/triggers/actors/test_actor_datasource.py`, `tests/codemie/triggers/bindings/test_cron.py`, `tests/codemie/triggers/test_xwiki_reindex.py`, `tests/codemie/triggers/test_git_faq_trigger_wiring.py` — exercise `datasource_concurrency_manager` indirectly and reference `_QUEUE_TIMEOUT_ERROR`.

### Testing Framework and Patterns

pytest (with `pytest.mark.asyncio` elsewhere); `unittest.mock` (`MagicMock`, `patch`, `patch.object`) is the dominant mock style. Version not determined.

### Coverage Gaps

- No dedicated unit test file for `DatasourceConcurrencyManager` found (codegraph reports no tests within 3 caller hops; Glob `tests/**/*concurrency*` only matched an unrelated `script_tool_calls` test).
- Whether existing wiki/factory tests assert on `session.get` call args or the exact embedding param dict was not inspected; such assertions (e.g. `assert_called_with(url, params=...)`, exact-dict equality) would need updating if present.

---

## 5. Configuration and Environment

### Environment Variables

- `DATASOURCE_CONCURRENCY_LIMIT_ENABLED` (reported prod: true), `MAX_CONCURRENT_DATASOURCE_INDEXING` (prod 20), `DATASOURCE_QUEUE_TIMEOUT` (prod 1h) — fields on `Config`.
- `DATASOURCE_REQUEST_TIMEOUT_SECONDS` — does not exist yet (plan names it; default 600 stated in the input document).

### Configuration Files

- `src/codemie/configs/config.py` (central `Config`).
- `config/datasources/datasources-config.yaml` (loaded by `src/codemie/datasource/datasources_config.py` as `datasources_config`; `AzureDevOpsWikiConfig` has chunk_size/chunk_overlap/loader_batch_size only; `CODE_CONFIG` has `enable_multiprocessing`, `processing_timeout=-1`, `max_subprocesses=1`). Path is derived from `config.DATASOURCES_CONFIG_DIR`.

### Feature Flags and Deployment Concerns

Static startup config only; changing needs pod restart. Embedding clients are built per `get_embeddings_model` call but read the config singleton at startup.

---

## 6. Risk Indicators

- `datasource_concurrency_manager.py` `run()` is on the hot path of every datasource index/reindex (15 callers); the release-in-`finally` guarantee must survive new logging (plan requires field extraction/logging inside the protected `try`). No dedicated tests exist for it, so regressions are only caught indirectly.
- `index_info` is typed `Optional[object]`; callers pass real `IndexInfo` or `MagicMock` in tests, and code datasources pass `None` — `getattr(..., 'unknown')` fallback needed for `id` and `repo_name`; `MagicMock` attributes will be non-strings in log output.
- Existing tests may assert exact kwargs of `self._session.get(...)` in the wiki loader or exact dict contents of `_build_embedding_model_params` / `AzureOpenAIEmbeddings(...)` kwargs (not verified) -> test updates likely required.
- `LiteLLMAzureOpenAIEmbeddings` replaces `self.client` after `super().__init__`; the request timeout is set on the parent init kwargs (`request_timeout`), so verify it still reaches the OpenAI client (installed `langchain-openai` 1.2.1 per the plan).
- Embedding timeout also applies to retrieval/query embeddings (shared factory), and with `max_retries=10` total stall time remains very large; this is accepted by the plan but means no hard duration bound.
- The actual production blocking operation is unestablished; the change reduces specific waits only. Other unbounded I/O (SDK wiki calls, Bedrock/Vertex, summary/chat, subprocess join with `processing_timeout=-1`) remain.
- `_run_in_subprocess` in `code_datasource_processor.py` joins with no timeout when `CODE_CONFIG.processing_timeout == -1` (out of scope but a candidate slot-holder).
- Speculative: log-format assertions in tests (e.g. `SlotAcquired. slot=... datasource_id=... datasource=... operation=...`) will need `caplog`/logger patching; the manager uses stdlib `logging.getLogger(__name__)`, not `codemie.configs.logger`.
- Speculative: `Config` may carry validators or `to_safe_dict` handling; adding an int field is expected to be low risk.

---

## 7. Summary for Complexity Assessment

The change touches four small, well-localised places across distinct layers: the datasource concurrency manager (add keyword-only `datasource_name`, `uuid` slot id, two log lines inside/after the `try/finally`, name in the queue-timeout log), the code-datasource `run_in_background` wrapper (pass `git_repo_name`), the Azure DevOps Wiki loader (add `timeout=` to three `session.get` calls and import `config`), the two embedding factories (add `request_timeout`), and one new `Config` field. Estimated production surface is about 6 files with small diffs; no new modules, schema, DB, or API changes.

Technical novelty is low: it follows existing central-config and f-string logging conventions. The main care points are preserving the release-in-`finally` guarantee and signature compatibility with 15 existing `run()` callers, and confirming `request_timeout` propagates through `LiteLLMAzureOpenAIEmbeddings`.

Test posture: the manager has no dedicated tests (only indirect coverage through trigger/cron tests), so new unit tests for slot logging, release-on-exception, and queue-timeout naming are needed; wiki loader, llm_factory, dependecies and code background-processing tests exist and may need assertion updates. The principal risk is not implementation difficulty but that the production root cause is unconfirmed, so the fix is mitigation and observability only.

---

## 8. External References

None named by the task as an external source of truth beyond the in-repo input document `the task input document` (resolved and read; it is an uncommitted input, not to be linked). Key facts from it:

- Manager: `run(self, process_func, index_info=None, *, datasource_name: str | None = None)`; name = `datasource_name` else `getattr(index_info, 'repo_name', 'unknown')`; logging context only.
- Per acquisition: `slot = uuid.uuid4().hex[:8]`; INFO `SlotAcquired` with `slot`, `datasource_id`, `datasource`, `operation` (callable name); field extraction and acquire log inside the protected `try`; `SlotReleased` after `semaphore.release()` in `finally`; no duration fields, timing state or `time` import; disabled-limit early return emits no slot logs; queue-timeout log uses resolved name. Format: `DatasourceConcurrencyManager. SlotAcquired. slot=3f9a1c2e datasource_id=unknown datasource=backend-repo operation=do_work`.
- Code datasources: `datasource_concurrency_manager.run(do_work, datasource_name=git_repo_name)` in `run_in_background()`; do not force early IndexInfo creation.
- Config: `DATASOURCE_REQUEST_TIMEOUT_SECONDS: int = 600` in central `Config`; single setting used by wiki and embeddings; no YAML duplication or separate settings.
- Wiki: pass `timeout=config.DATASOURCE_REQUEST_TIMEOUT_SECONDS` to the three `self._session.get(url, params=params)` calls; keep existing failure handling; no `AzureDevOpsWikiConfig`/YAML changes; SDK calls and work-item `timeout=60` unchanged.
- Embeddings: `request_timeout=config.DATASOURCE_REQUEST_TIMEOUT_SECONDS` in `llm_factory._build_embedding_model_params()` and native `AzureOpenAIEmbeddings` in `core/dependecies.py`; keep `max_retries=10` and document-level retries; no `EMBEDDINGS_MAX_RETRIES`.
- Out of scope: Bedrock/Vertex embeddings, summary/chat timeouts, PostgreSQL, retry policy, max indexing deadline, cooperative cancellation, batch-future timeouts, moving waiters out of the threadpool, watchdog.
