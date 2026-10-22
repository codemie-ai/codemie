# Spec: Datasource indexing slot tracing and request timeouts (EPMCDME-16237)

## Problem

On the main pod, datasource indexing occasionally never finishes until the pod is recreated. With the concurrency limit enabled (prod: 20 slots), each stuck run holds a semaphore slot; once all are held, new runs wait `DATASOURCE_QUEUE_TIMEOUT` (1h) and fail with "Datasource indexing queue timeout exceeded". `DatasourceConcurrencyManager.run()` already releases in `finally`, so a slot stays held only while work inside the protected section does not return. The blocking operation in production is not established; unbounded network I/O is a suspect.

This iteration is mitigation and observability: identify slot owners, and bound specific known-unbounded network waits. It does not guarantee a maximum run duration.

## Requirements

### R1. Slot tracing (`src/codemie/datasource/datasource_concurrency_manager.py`)

- `run()` gains an optional keyword-only `datasource_name: str | None = None`. Existing positional signature and all 15 callers stay valid. The parameter is logging context only: it must not create an `IndexInfo`, change queue-state persistence, or alter semaphore behavior.
- Resolved name = `datasource_name`, else `getattr(index_info, 'repo_name', 'unknown')`. Datasource id falls back to `unknown` the same way (code datasources create `IndexInfo` lazily).
- Per successful acquisition, generate `slot = uuid.uuid4().hex[:8]` (identifies the acquisition, not a numbered slot). Emit INFO `SlotAcquired` with `slot`, `datasource_id`, `datasource`, `operation` (callable `__name__`).
- Field extraction and the acquire log live inside the protected `try`. After `semaphore.release()` in `finally`, emit INFO `SlotReleased` with the same context. Release must not depend on logging or extraction succeeding.
- Format (existing manager prefix and f-string style):
  `DatasourceConcurrencyManager. SlotAcquired. slot=3f9a1c2e datasource_id=unknown datasource=backend-repo operation=do_work`
- The existing queue-timeout error log uses the resolved name.
- Limit disabled: existing early return, no slot logs.
- No duration fields, timing state or `time` import; elapsed time is read from log timestamps.

### R2. Repository name for code datasources (`src/codemie/datasource/code/code_datasource_processor.py`)

In `run_in_background()`, call `datasource_concurrency_manager.run(do_work, datasource_name=git_repo_name)`. Do not force early `IndexInfo` creation.

### R3. One shared timeout setting (`src/codemie/configs/config.py`)

Add `DATASOURCE_REQUEST_TIMEOUT_SECONDS: int = 600` to central `Config` (env-overridable, startup config, needs pod restart). Wiki and embeddings both read it. No separate settings, no datasource YAML entry, no `AzureDevOpsWikiConfig` change. It is a network-inactivity threshold, not a total-duration bound.

### R4. Azure DevOps Wiki (`src/codemie/datasource/loader/azure_devops_wiki_loader.py`)

Pass `timeout=config.DATASOURCE_REQUEST_TIMEOUT_SECONDS` to the three direct `self._session.get(url, params=params)` calls in `_get_page_comments`, `_get_wiki_attachments_index`, `_download_wiki_attachment` (import `config` via `from codemie.configs import config, logger`). Timeouts follow existing failure paths (empty comments, empty/partial index, `None` attachment); no new failed-page accounting. SDK calls and work-item `timeout=60` unchanged.

### R5. Embeddings

Pass `request_timeout=config.DATASOURCE_REQUEST_TIMEOUT_SECONDS` in `llm_factory._build_embedding_model_params()` (`src/codemie/enterprise/litellm/llm_factory.py`) and in the native `AzureOpenAIEmbeddings` construction in `src/codemie/core/dependecies.py`. Keep `max_retries=10` and document-level retries. The timeout must reach the OpenAI client for `LiteLLMAzureOpenAIEmbeddings` (which replaces `self.client` after init). Retrieval/query embeddings share these factories and are affected too. Worst-case stall remains large (about 110 min per fully stalled batch with retries); accepted.

## Acceptance criteria

1. With the limit enabled, each run emits one `SlotAcquired` and one `SlotReleased` INFO line sharing the same 8-hex `slot`, with `datasource_id`, `datasource`, `operation`.
2. The slot is released and `SlotReleased` emitted when `process_func` raises; a failure in field extraction or acquire logging still releases the semaphore.
3. Code-datasource runs log the repository name; `IndexInfo`-based runs log `index_info.repo_name`; missing values log `unknown`.
4. Queue-timeout log contains the resolved datasource name; queue-state behavior is unchanged.
5. Limit disabled: no slot logs; `process_func` still runs.
6. `Config.DATASOURCE_REQUEST_TIMEOUT_SECONDS` defaults to 600 and is overridable by env var.
7. All three wiki `session.get` calls receive the configured timeout; a timeout exception yields `[]`, the (possibly empty) index, or `None` respectively.
8. Both embedding factories set `request_timeout` to the configured value, and the built LiteLLM embeddings client carries it.
9. Existing tests pass (updated where they assert exact call kwargs/param dicts); new unit tests cover the manager (logging, release-on-exception, queue-timeout naming, disabled path) and the timeouts.

## Non-goals

- Bedrock and Vertex embeddings, summary/chat client timeouts.
- Retry-policy changes, `EMBEDDINGS_MAX_RETRIES`, document-level retry changes.
- A maximum indexing-duration deadline, cooperative cancellation, batch-future timeouts, subprocess join timeout (`processing_timeout=-1`).
- Moving semaphore waiters out of the FastAPI/anyio threadpool; watchdog changes.
- PostgreSQL changes; broad per-request logging; duration fields in slot logs.
- Datasource YAML or `AzureDevOpsWikiConfig` changes; timeouts for other loaders; Azure DevOps SDK calls; work-item attachment timeout.
- Live/dynamic timeout toggling.

## Diagnostic limits

An acquisition without a release marks a potentially long-running owner; pod termination or missing logs can also explain it. Repo name identifies the datasource, not the blocking operation. If needed after deployment, capture a Python thread stack before restart or add targeted operation logs.
