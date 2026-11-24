# MR 4461 review fixes (items 1-3) Implementation Plan

**Goal:** Address three MR 4461 review threads in `src/codemie/service/headroom/`: no module-level `global` state in the SDK compressor, no unused Protocol, compressors in a subpackage. Behavior-preserving; no new features.

**Conventions:** Commit per task using the repository's existing convention. Stage explicit paths only. Never stash, revert, modify or stage the user's uncommitted `config/customer/customer-config.yaml` and `docker-compose.yml`. Do not touch the Alembic migration or its tests, `README.md` or `.env.example` (see Deferred). Context: `technical-analysis.md` in this directory.

## Acceptance criteria

- `sdk_compressor.py` has no `global` statements and no mutable module-level state; the SDK state and worker pool live in a singleton class with a test reset hook.
- Importing `codemie.service.headroom.sdk_compressor` still does not import `headroom`, and a failed SDK import still disables the SDK for the process.
- `HeadroomCompressor` Protocol and `compressor.py` are gone; `factory.get_compressor` is annotated `SidecarCompressor | SDKCompressor`.
- Sidecar and SDK compressors, `strategies.py` and the shared types live in `src/codemie/service/headroom/compressors/`; models, resolver, scope, hook and factory stay at the top level; no re-export shims.
- All headroom tests pass at their new import and patch paths; the guide shows the new module paths.

### Task 1: Move compressors into a subpackage and drop the Protocol

**Files:** `src/codemie/service/headroom/{compressor,sidecar_compressor,sdk_compressor,strategies,factory}.py`; `tests/codemie/service/headroom/*`; `.ai-run/guides/integration/headroom-integration.md`.

Test-first: no - pure move and annotation change; the existing headroom tests, re-pointed to the new paths, are the check (they fail with ImportError until the move is done).

- [ ] `git mv` `sidecar_compressor.py`, `sdk_compressor.py` and `strategies.py` into new `compressors/` (add a license-header-plus-docstring `__init__.py` that imports nothing, so `headroom` is never pulled in).
- [ ] Replace `compressor.py` with `compressors/base.py` holding `CompressOutcome` and `HeadroomCompressionError` verbatim; delete the `HeadroomCompressor` Protocol (only user: `factory.py:22` and the return annotation at `factory.py:72`).
- [ ] `factory.py`: import the error from `compressors.base`, keep the lazy imports at `:76` and `:80` but point them at `compressors.*`, and annotate `get_compressor` as `SidecarCompressor | SDKCompressor` (under `TYPE_CHECKING`, so no runtime import).
- [ ] Fix intra-package imports in the moved files (`sdk_compressor.py:32-34`, `sidecar_compressor.py:24-26`).
- [ ] Tests: `git mv` `test_sdk_compressor.py`, `test_sidecar_compressor.py` and `test_strategies.py` to `tests/codemie/service/headroom/compressors/` (with an `__init__.py` like the parent's). Update every import and patch string, including `LOGGER` (`test_sdk_compressor.py:37`), `POST` (`test_sidecar_compressor.py:29`), `:490-502`, and the two subprocess code strings at `test_sdk_compressor.py:158-170`. Also update `test_factory.py:22-25` and `test_hook.py:25`. Grep `headroom\.(compressor|sidecar_compressor|sdk_compressor|strategies)` across `src/` and `tests/` to confirm nothing is left.
- [ ] Guide: update paths at `headroom-integration.md:140` (`strategies.py`) and `:264` (moved test files), and add one line on the `compressors/` layout near `:29`. Edit only the lines concerned (the file has user content).

### Task 2: Replace `global` state in the SDK compressor with a singleton

**Files:** `src/codemie/service/headroom/compressors/sdk_compressor.py`; `tests/codemie/service/headroom/compressors/test_sdk_compressor.py`.

Test-first: yes - failing tests: (a) `_SDKRuntime()` returns the same instance on repeated calls; (b) `_SDKRuntime.reset_for_tests()` yields a fresh instance with no SDK and `unavailable` false; (c) no module attribute `_executor`/`_sdk`/`_unavailable` exists and importing `sdk_compressor` creates no executor and leaves `headroom` out of `sys.modules` (extend the existing subprocess test at `:170`).

Idiom: copy `RequestSummaryManager` (`src/codemie/service/request_summary_manager.py:86-96`), class-level `_instance` and `_lock`, double-checked `__new__`. New symbol:

```python
class _SDKRuntime:
    """Process-wide SDK state: lazy lock-guarded load, sticky failure flag, timeout worker pool."""

    _instance: ClassVar[_SDKRuntime | None] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __new__(cls) -> _SDKRuntime: ...  # double-checked; calls _initialize() once

    def load(self) -> _SDK: ...  # body of today's _load_sdk, on self.sdk / self.unavailable / self._load_lock

    @classmethod
    def reset_for_tests(cls) -> None: ...  # under cls._lock drop the instance, executor.shutdown(wait=False, cancel_futures=True)
```

- [ ] `_initialize` sets `sdk=None`, `unavailable=False`, a per-instance `_load_lock` and `executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="headroom-sdk")`. Because the instance is built on first use, the pool is no longer created at import. Keep the pool-bound comment (`sdk_compressor.py:55-56`).
- [ ] Delete `_executor`, `_lock`, `_sdk`, `_unavailable`, `_reset_for_tests` and `_load_sdk` (`:55-92`). `SDKCompressor.compress` (`:157-161`) gets `runtime = _SDKRuntime()`, then `runtime.load()` and `runtime.executor.submit(...)`. Keep the `_apply_*` / `_warn_*` helpers as module functions and the exception handling and `config.HEADROOM_TIMEOUT_SECONDS` semantics unchanged.
- [ ] Tests: the `isolated_state` fixture (`:109-111`) and `:233` call `_SDKRuntime.reset_for_tests()`; `:439` and `:547` call `_SDKRuntime().load()`. Grep the test file for any other `_executor|_sdk|_unavailable` use and adapt it.

### Task 3: Remove `global` flags from `factory.py`

**Files:** `src/codemie/service/headroom/factory.py`; `tests/codemie/service/headroom/test_factory.py`.

Test-first: yes - failing test: after `factory._warn_legacy_mode_once.cache_clear()` and `factory._report_missing_sdk_once.cache_clear()` the legacy deprecation warning and the missing-SDK error each fire exactly once per reset (fails today, the helpers do not exist).

- [ ] Move the two one-shot log blocks (`factory.py:50-54`, `:60-68`) into `@lru_cache(maxsize=1)` helpers, `_warn_legacy_mode_once(mode)` and `_report_missing_sdk_once()`, the repo's lazy-state idiom. Delete `_legacy_warned`, `_sdk_missing_reported` and both `global` statements. Messages and the `importlib.util.find_spec("headroom")` check stay as they are.
- [ ] `test_factory.py:34-39`: the autouse fixture clears both helper caches in place of assigning the module flags. If a test shows the single-slot cache changes the once-only behavior, keep the flags, revert this task and report it in the task digest instead of widening scope.

## Negative-constraint pass

Constraints in the requirements: no new features and no behavior change (all three tasks only relocate or re-house state); importing the SDK module must not import `headroom` (Task 2 asserts it; Task 1's `__init__` imports nothing); no re-export shims (Task 1); do not touch Alembic, its tests, `docker-compose.yml`, `README.md`, `.env.example`, `customer-config.yaml` (no task lists them); no stash, revert, push, MR or Jira (none planned). No task violates any of these.

## Deferred to a follow-up run (needs `origin/main` merged or rebased into the branch first, which the user does)

Do not start before `y1z2a3b4c5d6` exists in `src/external/alembic/versions/`.

4. Alembic re-parent (thread `461b7cab`):
   - `src/external/alembic/versions/f8bd0b700a64_add_headroom_config_to_assistants.py`: `down_revision` (file line 31) `"5189d4d2afaf"` -> `"y1z2a3b4c5d6"`, and the `Revises:` docstring line 3 likewise. Leave the Create Date and `e091153f77dd` (`down_revision = "f8bd0b700a64"`) unchanged.
   - `tests/codemie/migrations/test_add_headroom_config_to_assistants.py:27`: `EXPECTED_DOWN_REVISION = "y1z2a3b4c5d6"`; refresh the comment on line 26.
   - In the `docker-compose.yml` / `tests/external/alembic/test_add_ms_exchange_credential_type.py` merge conflicts take main's version of the alembic test.
   - Verify one head: `...5189d4d2afaf <- a8f23cf09c37 <- y1z2a3b4c5d6 <- f8bd0b700a64 <- e091153f77dd`. Run `tests/codemie/migrations/` (including `test_head_enum_matches_credential_types_model`) and `tests/external/alembic/`.
5. Compose profile (summary note `9d120fb9`):
   - `docker-compose.yml`: add `profiles: [headroom, full]` directly after `container_name` on the `headroom` service. In the header comment block add a `headroom:   headroom` row (keep `full: everything above`) and an example `COMPOSE_PROFILES=app,headroom`. Preserve the user's uncommitted edits (litellm AWS `${...:-}` passthrough, `ports: - "8787:8787"`) and the committed `HEADROOM_OFFLINE`, `mem_limit`, `cpus`. Revisit the stale "no host port is published" comment (about `:155-156`).
   - `README.md` "Launch variants (`COMPOSE_PROFILES`)" table (about lines 104-115): add `app,headroom`. `.env.example` profile list (about lines 62-76): add `headroom`, optionally a `HEADROOM_PROXY_TOKEN` hint (`tests/codemie/configs/test_env_example.py` only checks required keys).
   - `headroom-integration.md` Deployment step 2: replace "Run the `headroom` sidecar service from `docker-compose.yml`" with "enable the `headroom` compose profile (`COMPOSE_PROFILES=app,headroom` or `full`)", and note that sidecar mode needs `app,headroom` (with `app` alone `http://headroom:8787` does not resolve and every call falls back after `HEADROOM_TIMEOUT_SECONDS`).
