# Datasource Slot Tracing and Request Timeouts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Identify datasource-indexing semaphore owners via logs and bound known-unbounded wiki and embedding network waits (EPMCDME-16237).

**Architecture:** Add slot acquire/release INFO logs to `DatasourceConcurrencyManager.run()`, pass a name from the code-datasource path, and add one central `DATASOURCE_REQUEST_TIMEOUT_SECONDS` consumed by the wiki loader and both embedding factories.

**Tech Stack:** Python, pydantic-settings, pytest, unittest.mock, requests, langchain-openai.

**Spec:** `docs/superpowers/tasks/2026-10-21-datasource-indexing-slot-hang/spec.md`

Commit per task using the repository's existing convention. No code comments in new code.

## Global Constraints

- `run(self, process_func, index_info=None, *, datasource_name: str | None = None)`; all 15 existing callers stay valid.
- Slot id is `uuid.uuid4().hex[:8]`; no duration fields, timing state or `time` import.
- Log format: `DatasourceConcurrencyManager. SlotAcquired. slot=3f9a1c2e datasource_id=unknown datasource=backend-repo operation=do_work`.
- One setting only: `DATASOURCE_REQUEST_TIMEOUT_SECONDS: int = 600`; no YAML, no `AzureDevOpsWikiConfig` change, no `EMBEDDINGS_MAX_RETRIES`, `max_retries=10` unchanged.

## Review Focus

- `index_info` is `None` or a `MagicMock`: id and name log `unknown`/mock repr, run still completes.
- Field extraction or acquire logging raises: semaphore is still released.
- `_try_clear_queued` hangs or raises while the slot is held: `SlotAcquired` is already logged and the slot is still released.
- `process_func` raises: `SlotReleased` still emitted and exception propagates.
- Limit disabled: no slot logs.

---

### Task 1: Config setting

**Files:**
- Modify: `src/codemie/configs/config.py:192` (add field next to `DATASOURCE_QUEUE_TIMEOUT`)
- Test: `tests/codemie/configs/` (existing config test module if present, else new `test_datasource_request_timeout.py`)

**Interfaces:**
- Produces: `config.DATASOURCE_REQUEST_TIMEOUT_SECONDS: int` (default 600), used by Tasks 4 and 5.

Test-first: yes — `Config()` has no such attribute, so default-600 and env-override (`monkeypatch.setenv("DATASOURCE_REQUEST_TIMEOUT_SECONDS", "42")` gives 42) tests fail.

- [ ] Write the two tests, confirm they fail, add `DATASOURCE_REQUEST_TIMEOUT_SECONDS: int = 600`, confirm they pass.

### Task 2: Slot tracing in the manager

**Files:**
- Modify: `src/codemie/datasource/datasource_concurrency_manager.py:86-125` (`run`; add `import uuid`)
- Test: `tests/codemie/datasource/test_datasource_concurrency_manager.py` (new)

**Interfaces:**
- Produces: `run(process_func, index_info=None, *, datasource_name=None)`, used by Task 3.

Test-first: yes — `caplog` assertions for `SlotAcquired`/`SlotReleased` fail because nothing is logged and `datasource_name` is an unexpected keyword.

Required ordering once the semaphore is held (the existing `try` block):

```
try:
    <extract slot, datasource_id, datasource, operation>
    <log SlotAcquired>
    if queued_set:
        self._try_clear_queued(index_info)
    process_func()
finally:
    self._semaphore.release()
    <log SlotReleased, guarded so missing context cannot raise>
```

`SlotAcquired` precedes `_try_clear_queued()` so a hang there still leaves an acquisition log. Extraction and the acquire log sit inside the `try` so any failure still reaches `finally`.

- [ ] Write tests on a fresh `DatasourceConcurrencyManager(max_concurrent=1, enabled=True, queue_timeout=...)`:
  - success path: one `SlotAcquired` and one `SlotReleased` INFO record sharing an 8-hex `slot`, with `datasource_id`, `datasource`, `operation` (`process_func.__name__`).
  - name resolution: `datasource_name` wins over `index_info.repo_name`; `index_info=None` logs `datasource_id=unknown datasource=unknown`.
  - ordering: with the semaphore pre-held by another thread so `run()` takes the queued path (`queued_set` true), patch `_try_clear_queued` with a side effect that asserts a `SlotAcquired` record already exists in `caplog`; it is called once and `process_func` runs.
  - `_try_clear_queued` raises (patched): the exception propagates, `SlotAcquired` and `SlotReleased` are both logged, semaphore reacquirable. A hang is modelled by the same raise since the guarantee is identical (release in `finally`, acquire log already emitted).
  - `process_func` raises: exception propagates, `SlotReleased` logged, semaphore reacquirable.
  - extraction failure (patch the `uuid.uuid4` used in the module to raise): `run()` raises, semaphore still released, so a subsequent `run()` acquires without blocking.
  - acquire-log failure (patch the module logger so the `SlotAcquired` call raises): semaphore still released.
  - queue timeout (`max_concurrent=1`, semaphore held, tiny `queue_timeout`): error log contains the resolved `datasource_name`, `process_func` not called, `index_info.set_error` called with `_QUEUE_TIMEOUT_ERROR`.
  - `enabled=False`: `process_func` runs, no `Slot` records.
- [ ] Run them, confirm failure.
- [ ] Implement in `run()`: add keyword-only `datasource_name`; resolve name once for the queue-timeout log (replacing the `getattr(index_info, 'repo_name', ...)` there; this path holds no slot so it stays outside the `try`). In the existing `try`, as the first statements and before `_try_clear_queued()`: compute slot, id, name, operation (`getattr(process_func, '__name__', ...)`) and log `SlotAcquired`. In `finally`: `self._semaphore.release()` first, then log `SlotReleased` using context captured so far (guard so a missing context cannot raise). Leave the disabled early return untouched.
- [ ] Run the new tests plus `tests/codemie/triggers/` datasource tests; expect PASS.

### Task 3: Code-datasource name

**Files:**
- Modify: `src/codemie/datasource/code/code_datasource_processor.py:163-177` (`task()` calls `run(do_work, datasource_name=git_repo_name)`)
- Test: `tests/codemie/datasource/code/test_background_processing.py`

**Interfaces:**
- Consumes: Task 2 `datasource_name` keyword.

Test-first: yes — patch `datasource_concurrency_manager.run` and assert it is called with `datasource_name=git_repo_name`; fails today (called without it). Update any existing assertion on the exact `run` call.

- [ ] Write/adjust the test, confirm failure, change the call (no `IndexInfo` creation), confirm pass.

### Task 4: Wiki loader timeouts

**Files:**
- Modify: `src/codemie/datasource/loader/azure_devops_wiki_loader.py:32` (import `config`), `:429`, `:525`, `:564` (`timeout=config.DATASOURCE_REQUEST_TIMEOUT_SECONDS` on each `self._session.get`)
- Test: `tests/codemie/datasource/loader/test_azure_devops_wiki_loader.py`

**Interfaces:**
- Consumes: Task 1 setting.

Test-first: yes — assert each of `_get_page_comments`, `_get_wiki_attachments_index`, `_download_wiki_attachment` calls `session.get` with `timeout=<patched config value>`; also, with `session.get` raising `requests.exceptions.Timeout`, they return `[]`, the (possibly empty) index, and `None` respectively. Both fail today on the timeout kwarg.

- [ ] Write tests, confirm failure, add the kwarg to the three calls only (SDK calls and work-item `timeout=60` untouched), update existing `session.get` call-arg assertions, confirm pass.

### Task 5: Embedding request timeouts

**Files:**
- Modify: `src/codemie/enterprise/litellm/llm_factory.py:357-376` (`_build_embedding_model_params` adds `"request_timeout": config.DATASOURCE_REQUEST_TIMEOUT_SECONDS`), `src/codemie/core/dependecies.py:94-105` (native `AzureOpenAIEmbeddings(... request_timeout=config.DATASOURCE_REQUEST_TIMEOUT_SECONDS)`)
- Test: `tests/enterprise/litellm/test_llm_factory.py`, `tests/codemie/core/test_dependecies.py`

**Interfaces:**
- Consumes: Task 1 setting.

Test-first: yes — `_build_embedding_model_params()["request_timeout"]` equals the configured value; native `AzureOpenAIEmbeddings` mock called with that `request_timeout`; and a built `LiteLLMAzureOpenAIEmbeddings` has its OpenAI client timeout equal to the configured value (inspect `embeddings.client`/`EmbeddingClientWrapper` underlying client; if `self.client` replacement drops the timeout, fix in `LiteLLMAzureOpenAIEmbeddings` so it reaches the client). Fails today: key absent.

- [ ] Write tests, confirm failure, implement, update exact-dict and exact-kwarg assertions in existing tests, confirm pass.
- [ ] Leave Bedrock and Vertex branches unchanged.

## Negative-constraint pass

Honored: no `IndexInfo` creation (Task 3), no duration/`time` (Task 2), no YAML/`AzureDevOpsWikiConfig`/extra settings (Tasks 1, 4), `max_retries=10` kept and Bedrock/Vertex untouched (Task 5), SDK calls and work-item timeout untouched (Task 4), disabled path emits no slot logs (Task 2).
