# Implementation Plan: Fix Datasource Description Revert (EPMCDME-10036)

## Requirements
When a user edits datasource metadata (description, project_space_visible) during async reindexing, the changes must persist and NOT revert to original values after indexing completes or progress updates occur.

## Root Cause
All `update()` calls in IndexInfo lifecycle methods use `session.merge(self)`, which performs a full object synchronization to the database. This overwrites ALL fields including user-edited metadata with stale in-memory values during long-running async processes.

**Reproduction**: 
1. Create datasource → indexing starts
2. Edit description → saved to DB, but background task has OLD value in memory
3. Wait for batch processing → `commit_stats()` calls `update()` → REVERT 1 (first description revert observed)
4. Edit description again
5. Wait for indexing to finish → `complete_progress()` + `on_complete()` call `update()` → REVERT 2 (second revert observed)

## Implementation Strategy

Replace ALL `update()` calls with targeted SQL `update_progress()` that:
- Uses SQLAlchemy's `sa_update()` with explicit `.values()` specifying ONLY progress fields
- Completely excludes metadata fields (description, project_space_visible)
- Preserves performance optimization (batch updates, minimal DB requests)
- Follows existing pattern in `stamp_reindex_triggered_at()` and `try_claim_for_resume()`

## Implementation Tasks

### Task 1: Add update_progress() Method to IndexInfo
**File**: `src/codemie/rest_api/models/index.py`

**Changes**:
- Add `update_progress()` method that accepts only progress-related parameters
- Add `_build_progress_update_kwargs()` helper to keep cyclomatic complexity under limit
- Parameters: current_state, complete_state, completed, error, is_fetching, is_queued, current__chunks_state, processing_info, processed_files, uploaded_files, text, last_reindex_triggered_at, tokens_usage
- Uses targeted SQL UPDATE that never touches description or project_space_visible

**Test-first**: NO — Pure infrastructure, tested implicitly through lifecycle tests

### Task 2: Replace update() in IndexInfo Lifecycle Methods (8 methods)
**File**: `src/codemie/rest_api/models/index.py`

**Methods to replace**:
1. `start_fetching()` (line 451)
2. `start_progress()` (line 455)
3. `move_progress()` (line 498)
4. `decrease_progress()` (line 516)
5. `commit_stats()` (line 559) — PRIMARY: Called after every batch
6. `complete_progress()` (line 581) — PRIMARY: Called when indexing finishes
7. `set_error()` (line 593)

**Each replacement**: Call `self.update_progress(field=value, ...)` with only relevant fields

**Test-first**: NO — Existing tests already verify behavior through lifecycle tests

### Task 3: Replace update() in Callbacks (2 methods)
**File**: `src/codemie/datasource/callback/datasource_monitoring_callback.py`

**Methods to replace**:
1. `on_complete()` (line 89)
2. `on_error()` (line 147)

**Change**: Replace `self.index.update()` with `self.index.update_progress(tokens_usage=...)`

**Test-first**: NO — Tests already verify tokens_usage is persisted

### Task 4: Replace update() in FileDatasourceUpdateProcessor
**File**: `src/codemie/datasource/file/file_datasource_update_processor.py`

**Location**: `process()` method line 223

**Change**: Replace `self.index.update()` with `self.index.update_progress(is_fetching=False, processing_info=...)`

**Test-first**: NO — Tests already verify progress updates

### Task 5: Update Tests (7 tests)
**Files**: 
- `tests/codemie/datasource/callback/test_datasource_monitoring_callback.py` (4 tests)
- `tests/codemie/datasource/file/test_file_datasource_update_processor.py` (3 tests)

**Changes**: Update all mocks to expect `update_progress()` instead of `update()`

## Validation Checklist

- [x] All ruff code quality checks pass
- [x] 29 IndexInfo tests pass
- [x] 1069 datasource tests pass
- [x] 7 callback/processor tests updated and passing
- [x] Manual verification: Metadata persists across batch checkpoints and final completion
- [x] No performance degradation (targeted SQL is as efficient as full merge)
- [x] All datasource types covered (Git, File KB, SharePoint, Provider)

## Files Modified

1. `src/codemie/rest_api/models/index.py` — 162 lines added (update_progress + helper + 8 lifecycle methods)
2. `src/codemie/datasource/base_datasource_processor.py` — 3 update() calls replaced
3. `src/codemie/datasource/callback/datasource_monitoring_callback.py` — 2 update() calls replaced
4. `src/codemie/datasource/file/file_datasource_update_processor.py` — 1 update() call replaced
5. `tests/codemie/datasource/callback/test_datasource_monitoring_callback.py` — 4 tests updated
6. `tests/codemie/datasource/file/test_file_datasource_update_processor.py` — 3 tests updated
7. `tests/codemie/rest_api/routers/test_callbacks.py` — test artifacts

**Total**: 7 files, 230 insertions (+), 36 deletions (-)

## Test Results

- ✅ `make ruff` — All checks pass
- ✅ `tests/codemie/rest_api/models/test_index_info.py` — 29/29 pass
- ✅ `tests/codemie/datasource/` — 1069/1069 pass
- ✅ MR created: https://gitbud.epam.com/epm-cdme/codemie/-/merge_requests/3810
