# Technical Analysis: EPMCDME-10036 — Datasource Description Revert Bug

## Codebase Findings

### Root Cause: Full Object Merge During Async Reindexing

**Bug Location**: Multiple `update()` calls in IndexInfo lifecycle methods during async processing

**Core Issue**: 
`BaseModelWithSQLSupport.update()` uses `session.merge(self)` which performs a full object synchronization to the database. When called during long-running async tasks, this writes ALL fields from the stale in-memory object, including metadata fields that may have been edited by the user after the background task started.

**Call Chain**:
1. **User edits description via UI** → Persisted to DB immediately
2. **Background reindexing starts** → Loads IndexInfo object with OLD description into memory
3. **User edits description again** → Persisted to DB (but in-memory object still has old value)
4. **During reindexing, progress updates call `update()`** → `session.merge(self)` overwrites description with stale in-memory value
5. **Description reverted** ❌

### Affected Methods and Call Chain

**Primary culprits** (called during every indexing run):
1. `IndexInfo.start_fetching()` (line 451) — called at indexing start
2. `IndexInfo.start_progress()` (line 455) — called early in indexing
3. `IndexInfo.commit_stats()` (line 559) — **Called after every batch** → PRIMARY SOURCE OF FIRST REVERT
4. `IndexInfo.complete_progress()` (line 581) — **Called when indexing finishes** → PRIMARY SOURCE OF SECOND REVERT
5. `IndexInfo.set_error()` (line 593) — called on errors

**Secondary contributors**:
6. `IndexInfo.move_progress()` (line 498) — called during progress tracking
7. `IndexInfo.decrease_progress()` (line 516) — called when removing documents
8. `DatasourceMonitoringCallback.on_complete()` (line 89) — called after indexing completes
9. `DatasourceMonitoringCallback.on_error()` (line 147) — called on error
10. `FileDatasourceUpdateProcessor.process()` (line 223) — called during file processing
11. `FileDatasourceUpdateProcessor._on_process_end()` (line 341) — called at end of file processing

**All use pattern**: `self.update()` → `session.merge(self)` → writes ALL fields including stale metadata

### Why This Affects All Datasource Types

- **Code datasources** (Git): Use `CodeDatasourceProcessor` → inherits from `BaseDatasourceProcessor` → all 5 primary lifecycle methods affected
- **File datasources**: Use `FileDatasourceUpdateProcessor` → inherits through `FileDatasourceProcessor` → affected by all methods
- **SharePoint/Provider datasources**: Use callbacks with `update()` → affected by `on_complete()` and `on_error()`

### Evidence from Code

**The full object merge pattern** in `src/codemie/rest_api/models/base.py`:
```python
def update(self, refresh=False, validate=True):
    with Session(self.get_engine()) as session:
        session.merge(self)  # ← Writes ALL fields from stale object
        session.commit()
```

**Reproduction steps confirm both reverts**:
1. Create datasource → background task starts
2. Edit description via UI (persists to DB)
3. Wait for batch processing → `commit_stats()` calls `update()` → description reverts (FIRST REVERT, step 5)
4. Edit description again
5. Wait for indexing to finish → `complete_progress()` calls `update()` → description reverts (SECOND REVERT, step 8)

## Risk Indicators

| Risk | Severity | Evidence |
|------|----------|----------|
| **Affects all datasource types** | CRITICAL | All datasource processors inherit affected methods |
| **Multiple revert opportunities** | HIGH | 11 `update()` calls during processing lifecycle |
| **Full object overwrite** | HIGH | `session.merge(self)` has no field filtering |
| **Performance impact if "fixed" naively** | MEDIUM | Batch updates provide performance benefit |

## Affected Code Paths

1. **All datasource creation/reindexing**: Git, File KB, Provider, SharePoint
2. **Any metadata edit during indexing**: Triggering reverts at batch boundaries and completion
3. **Progress tracking**: Regular calls to `commit_stats()` cause periodic reverts

## Solution Approach

**Fix**: Replace all `update()` calls with `update_progress()` that uses targeted SQL UPDATE with explicit `.values()` specifying ONLY progress fields.

**Pattern** (following existing `stamp_reindex_triggered_at()` and `try_claim_for_resume()`):
```python
stmt = sa_update(IndexInfo).where(IndexInfo.id == self.id).values(
    current_state=value,
    complete_state=value,
    # ... other progress fields ...
    # Note: description, project_space_visible intentionally NOT included
)
with Session(self.get_engine()) as session:
    session.execute(stmt)
    session.commit()
```

This ensures metadata fields are NEVER touched during progress updates, completely isolating them from async reindexing side effects.
