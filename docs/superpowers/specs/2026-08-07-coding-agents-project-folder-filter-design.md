# Coding Agents Analytics — Project Folder Filter — Design Spec

**Date:** 2026-08-07
**Scope:** Full-stack. Backend (`codemie`) adds a new field rename, a new endpoint, and filtering plumbing. Frontend (`codemie-ui`) adds a new always-visible dropdown on the Coding Agents analytics tab (`/analytics?tab=codingAgents`).

---

## Summary

The Coding Agents analytics tab has a "Cost" sub-tab with a Models dropdown that filters only that sub-tab. We're adding a second dropdown — **Project Folder** — that is visible on *every* sub-tab (Overview, Sessions, Cost, Tools, Users) and filters each one by the folder a session's `cwd` was started in.

This is a distinct concept from the existing "Project" filter already in the page-level sidebar (`ProjectSelector`), which filters by `codemie_project_name` — CodeMie's own project entity. To avoid confusion, the field currently named `project` on `SessionSummary` (which is really `_cwd_basename(cwd)`) is renamed to `project_folder` throughout.

---

## Terminology

| Name | What it is | Where it lives today |
|---|---|---|
| `codemie_project_name` / `projects` filter | CodeMie's own project entity | `SessionSummary.codemie_project_name`; page-level `ProjectSelector` dropdown; already wired into `/sessions`, `/tools` (buggy: `/cost` drops it, `/users` never had it) |
| `project` → renamed **`project_folder`** | Basename of the session's `cwd` | `SessionSummary.project` (`coding_agents_analytics.py:78`), computed in `_build_session_summary` (`coding_agents_handler.py:100`) via `_cwd_basename()` |

This spec is about `project_folder` only. As a side effect, it also fixes the `codemie_project_name`/`projects` filtering gap on `/cost` and `/users`, since the same CTE/join plumbing is needed for both.

---

## Backend design (`codemie`)

### 1. Field rename

- `SessionSummary.project` → `SessionSummary.project_folder` in `src/codemie/rest_api/models/coding_agents_analytics.py:78`.
- Dict key `"project"` → `"project_folder"` in `_build_session_summary()` (`coding_agents_handler.py:100`).
- Update `tests/codemie/rest_api/models/test_coding_agents_analytics.py`.

### 2. New endpoint — list available project folders

```
GET /v1/analytics/coding-agents/project-folders
→ { "project_folders": string[] }
```

- New response model `ProjectFoldersResponse` in `coding_agents_analytics.py` (models file).
- New repository method, e.g. `get_project_folders()`:
  ```sql
  SELECT DISTINCT cwd FROM codemie_analytics.coding_agent_hook_events WHERE cwd != ''
  ```
  All-time, unscoped by date/user — matches the existing pattern for the Models/Users lists on the root endpoint (`get_root_lists()`).
- Handler applies `_cwd_basename()` to each row and dedupes in Python (older un-normalized rows may still carry full paths; newer rows are already bare folder names per the existing ingest-time normalization noted in `_cwd_basename`'s docstring).
- New router handler in `coding_agents_analytics.py` (routers file), same shape as `get_root`/`get_cost`.

### 3. New query param

Add `project_folder: str | None = Query(None, max_length=256)` to the shared `CodingAgentsQueryParams` (single value, not comma-separated — matches the Models filter's single-select shape). Available to every endpoint using this params class.

### 4. Filtering per endpoint

- **Sessions** (`get_sessions`/`count_sessions`) and **Tools** (`get_tools`): both already query `coding_agent_hook_events`, which carries `cwd` directly. Add a `project_folder` condition matching `cwd` against the folder name (tolerating un-normalized full-path rows, e.g. `cwd = {pf} OR cwd LIKE concat('%/', {pf}) OR cwd LIKE concat('%\\', {pf})`).
- **Cost** (`get_cost`) and **Users** (`get_users_cost`, `get_users_lines`, `get_users_activity`, and the per-user detail queries in `get_user_lines_daily`/`get_user_active_time_daily`): none of `coding_agent_cost_daily`, `coding_agent_lines_daily`, `coding_agent_active_time_daily` carry `cwd`. Add a `session_cwd` CTE:
  ```sql
  session_cwd AS (
      SELECT session_id, anyLastIf(cwd, cwd != '') AS cwd
      FROM codemie_analytics.coding_agent_hook_events
      GROUP BY session_id
  )
  ```
  and join on `session_id`, filtering the joined `cwd` the same way as above. `get_user_repo_breakdown` already joins `hook_events` directly and gets `cwd` for free.
- **Fix while touching this code**: the existing `projects` (`codemie_project_name`) filter is silently dropped by `get_cost` and entirely absent from `get_users*`. Since the same CTEs/joins are being added for `project_folder`, extend them to also carry/filter `codemie_project_name` and wire `projects` through consistently.
- Handler methods (`get_cost`, `get_users`, `get_user_detail`) gain a `project_folder: str | None` parameter and pass it through to the repository calls; router endpoints read it off `params` (a new property, e.g. `params.project_folder`, alongside the existing `projects_list`).

### 5. Not in scope

- `AnalyticsRootResponse.projects` stays as-is (hardcoded `[]`) — this spec adds a dedicated endpoint instead of overloading the root response.

---

## Frontend design (`codemie-ui`)

### 1. Dropdown component

`CodingAgentsFilters.tsx` currently renders only a Models `Select`, gated behind `showModel` (returns `null` otherwise) — effectively Cost-only. Add a second `Select` for Project Folder, single-select, using the same `toOptions`/`ALL_OPTION` helpers, rendered **unconditionally** (no visibility gate — Project Folder is not sub-tab-scoped, unlike Model).

### 2. Options source

New store field `projectFolders: string[]` + method `fetchProjectFolders()` hitting the new `GET /v1/analytics/coding-agents/project-folders` endpoint. Fetched once when the Coding Agents tab mounts, alongside the existing `fetchSummary()` call.

### 3. State wiring — `CodingAgentsAnalyticsTab.tsx`

- New `localProjectFolder` state, parallel to the existing `localModel`.
- Included in `composedFilters` **unconditionally** (not run through `FILTER_VISIBILITY` — that map stays as-is for gating Model to Cost only).
- `handleFilterChange` extended to accept `project_folder` updates.
- The fetch `useEffect` extended so `project_folder` is destructured from `composedFilters` and passed to **all four** fetches: `fetchCost`, `fetchSessions`, `fetchTools`, `fetchUsers` (today `fetchUsers` doesn't receive `projects` either — both filters get wired through together).
- Persistence: plain component state, resets on full page reload — consistent with the existing Models filter. Not URL/localStorage-persisted.

### 4. Rename fallout

- `CodingAgentsSessionSummary.project` → `.project_folder` in `src/types/analytics.ts`.
- `CodingAgentsProjectHierarchy.tsx` reads `session.project` for its grouping — update to `session.project_folder`.
- Any other read sites of `.project` on session objects (grep after the type rename to catch all).

### 5. Types & store

- Add `project_folder?: string` to `CodingAgentsFilters` interface.
- Add `ProjectFoldersResponse { project_folders: string[] }` type.
- Thread `project_folder` through the relevant store fetch methods' `params` (`fetchCost`, `fetchSessions`, `fetchSessionsPage`, `fetchTools`, `fetchUsers`, `fetchUserDetail`).

---

## Data flow

```
User picks folder in dropdown
  → localProjectFolder (CodingAgentsAnalyticsTab state)
  → composedFilters.project_folder
  → useEffect fires
  → store.fetchCost/fetchSessions/fetchTools/fetchUsers(composedFilters)
  → api.get(..., { params: { ...composedFilters } })
  → backend filters via cwd (direct) or session_cwd join (cost/users)
  → all sub-tabs re-render filtered
```

---

## Error handling

- New endpoint failure: same pattern as existing store fetches — `loading`/`error` keyed state per call, non-blocking. Dropdown shows an empty option list if the fetch fails, same as Models does today when `fetchSummary` fails.
- No special-casing needed for empty filtered result sets — existing per-sub-tab empty states apply.

---

## Testing

- Backend: update `test_coding_agents_analytics.py` model test for the rename; add repository/handler tests for the new `project_folder` filter clauses (sessions, tools, cost, users) and the new endpoint; add regression coverage for the `codemie_project_name` fix on cost/users.
- Frontend: no existing dedicated tests for `CodingAgentsFilters.tsx`/`CodingAgentsAnalyticsTab.tsx` were found — add minimal coverage for the new dropdown rendering and filter propagation. Manually verify in the running dev server (dropdown appears on all sub-tabs, selecting a folder filters each one, resets correctly).

---

## Out of scope / follow-ups

- Persisting `project_folder` (or `model`) across page reloads via URL/localStorage.
- Multi-select for `project_folder`.
- Scoping the project-folders list endpoint to the current time period/user filters.
