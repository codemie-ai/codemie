# Combined Integrations List (backend) Implementation Plan

**Goal:** `GET /v1/settings` returns a person's User and Project integrations as one paged, newest-first list with an optional `scope` filter, under today's visibility rules.

**Architecture:** Each scope's visibility is one SQL condition in `SettingsIndexService` that carries its own `setting_type` match. Every list is one `select(Settings)` whose WHERE ORs the conditions of an explicit scope set, then `SettingsFilter` once, `date DESC, id ASC`, one count, offset/limit. A thin route in the cross-scope router maps the `scope` enum to that set.

**Spec:** `docs/superpowers/tasks/2026-11-10-epmcdme-17085-combined-integrations-list/spec.md` (with `technical-analysis.md` in the same directory).

Commit per task using the repository's existing convention. Stage only by explicit path, never `git add -A` or `git add .`. The uncommitted `.gitignore` change is the user's.

## Global Constraints

- D11: each scope's rule is defined once, in `SettingsIndexService`. The combined list restates none.
- Scope All is one select ORing the per-scope conditions. It is never a UNION ALL, never two merged pages, never `settings_type=None`.
- Copy the internal-alias condition word for word, unescaped `_` included: `not_(Settings.alias.like(f"{SettingsService.INTERNAL_PREFIX}%"))`.
- Order is `Settings.date.desc(), Settings.id.asc()`. Add no NULLS FIRST/LAST, so rows with a missing date do not move.
- Leave these unchanged: `run(...)`'s signature, `get_users` (`settings_index_service.py:95-132`), the per-scope routes and their unvalidated paging, `/settings/user/available`, and the budget rule's Python predicate. Read-only, no migration.

## Review Focus

1. Platform admin, Scope All: another person's User rows → absent (Task 2).
2. A Project row the requester created in a project they do not administer → absent (Task 2).
3. Malformed or non-object `filters` → 422, not 500 (Task 3).
4. `search` at Scope All: project-name matches before alias-only matches (Task 2).
5. Equal dates across both scopes: each row on exactly one page (Task 2).

negative-constraints: everything the Non-goals list (UI, CRUD and permissions, Schedulers, pickers list, `get_users`, the budget predicate, NULL dates, per-scope paging validation) stays untouched under Global Constraints. `scope` inside `filters` stays ignored (Task 3 test). No UNION ALL and no `None` for All (Tasks 1-2).

---

### Task 1: Per-scope conditions carry their own scope match; the order gains `id`

**Files:** Modify `src/codemie/service/settings/settings_index_service.py:53-93`. Create `tests/codemie/service/settings/test_settings_visibility_real_engine.py`.

**Interfaces (produces):**
- `SettingsIndexService._scope_condition(cls, user: User, scope: SettingType) -> ColumnElement[bool]`
- `SettingsIndexService._query_postgres(cls, user: User, scopes: AbstractSet[SettingType], page=0, per_page=10, filters: dict | None = None) -> tuple[list[Settings], int]`
- Test helpers: fixture `engine` (empty table), fixture `seeded` (the rows below), and `make_user(*, is_admin: bool, admin_projects: list[str]) -> User` with `id=ME`.
- `run(...)` keeps its signature, because router tests assert `settings_type=`.

Test-first: yes. Rows with equal `date` return in ascending `id` order (fails: today's order is `date` only). `_scope_condition(user, None)` raises `ValueError` (fails: no such method).

- [ ] **Step 1: Write the fixture and tests.**

```python
@compiles(JSONB, "sqlite")
def _as_json(_type, compiler, **_kw):
    return "JSON"

@pytest.fixture
def engine(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.execute(CreateTable(Settings.__table__))  # not create_all: ix_settings_project_name is declared twice
    monkeypatch.setattr(Settings, "get_engine", staticmethod(lambda: engine))
    monkeypatch.setattr(config, "ENV", "dev")  # codemie.configs.config
    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", True)
    return engine
```

Build users only after the patch: `is_admin` is resolved when the user is created (`security/user.py:110-128`). ME administers project `A` only. Rows are JIRA unless stated. Each has `created_by.name` set to its owner's name and a date of its own.

| Row | Scope | Owner | Project | Notes |
|---|---|---|---|---|
| `u_mine` | USER | ME | A | alias `mine`, `is_global=True` |
| `u_internal` | USER | ME | A | alias `__internal__x` |
| `u_wild` | USER | ME | A | alias `myinternal-x` |
| `u_other` | USER | OTHER | A | |
| `p_a` | PROJECT | OTHER | A | |
| `p_b_mine` | PROJECT | ME | B | |
| `p_budget` | PROJECT | | A | LITE_LLM, alias `f"{SettingsService.PROJECT_BUDGET_ALIAS_PREFIX}A"` |
| `untyped` | `setting_type=None` | ME | A | |

Assert ids from `run(settings_type=...)`:

| Persona | USER | PROJECT |
|---|---|---|
| Project admin | `{u_mine}` | `{p_a}` |
| Platform admin | `{u_mine}` | `{p_a, p_b_mine, p_budget}` |
| Member, `admin_projects=[]` | | `[]`, total 0 |

Two more tests:
- `test_equal_dates_order_by_id`: three ME USER rows share one date, inserted as `c`, `a`, `b`. Expect `["a", "b", "c"]`.
- `test_unknown_scope_raises`: `_scope_condition(user, None)` raises `ValueError`.

- [ ] **Step 2:** `poetry run pytest tests/codemie/service/settings/test_settings_visibility_real_engine.py -v`. Expected: the two new-behaviour tests FAIL. The clause tests pass, since they pin today's rows.
- [ ] **Step 3: Implement.**
  - `_scope_condition` returns `and_(Settings.setting_type == scope, ...)` with the clauses moved verbatim from `:66-78`.
  - PROJECT: the admin-project clause unless `user.is_admin`, and the budget exclusion unless `user.is_admin_or_maintainer`.
  - `elif scope == SettingType.USER`: own `user_id` plus the internal-alias LIKE.
  - Any other scope raises `ValueError`.
  - `_query_postgres` raises `ValueError` on an empty set. Otherwise it builds `select(Settings).where(or_(*(cls._scope_condition(user, s) for s in scopes)))` and keeps the existing filter, count and offset/limit lines, with the order from Global Constraints.
  - `run` passes `scopes=frozenset({settings_type})`.
- [ ] **Step 4:** Run the new file, `tests/codemie/service/settings/test_settings_index_service.py` and `tests/codemie/rest_api/routers/test_user_settings_crud.py`. Expected: PASS.

### Task 2: `SettingsIndexService.list_scopes`, the combined path

**Files:** Modify `src/codemie/service/settings/settings_index_service.py:31-51`. Test file is Task 1's.

**Interfaces:**
- Consumes: Task 1's `_query_postgres`, fixtures and seed rows.
- Produces: `SettingsIndexService.list_scopes(cls, user: User, scopes: AbstractSet[SettingType], page=0, per_page=10, filters: dict | None = None) -> dict`. It returns `{data, pagination: {page, per_page, total, pages}}`, masked the way `run` masks today.

Test-first: yes. A project admin's `list_scopes(user, ALL)` returns exactly `{u_mine, p_a}` (fails: the method does not exist).

- [ ] **Step 1: Write the tests.** `ALL = frozenset({SettingType.USER, SettingType.PROJECT})`.
  - **Project admin, ALL:** `{u_mine, p_a}`, total 2. Absent: `u_other` (AC-13a), `p_b_mine` (AC-13b), `p_budget` (AC-8), and the internal, wildcard and untyped rows. Every returned row has `setting_type` set to `user` or `project`.
  - **Platform admin, ALL:** `{u_mine, p_a, p_b_mine, p_budget}`. `u_other`, `u_internal`, `u_wild` and `untyped` are absent.
  - **Parity,** for every persona and each single scope `s`: `list_scopes(scopes={s})` equals `run(settings_type=s)` in ids, order and total. At ALL, the id set is the union of the two.
  - **Filters,** as platform admin: `{"search": "mine"}`, `{"project": "A"}`, `{"type": CredentialTypes.LITE_LLM.value}`, `{"is_global": True}` and `{"created_by": <OTHER's name>}`. For each, ALL's ids are the union of the per-scope results under the same filter. If SQLite cannot render the `created_by` JSON path, drop that case and say so in the report.
  - **Search order,** as platform admin with `{"search": "alpha"}`: a newer USER row aliased `alpha-key` in `A` and an older PROJECT row in project `alpha`. The project-name match comes first.
  - **Paging,** on an empty `engine`: 12 USER and 13 PROJECT rows share one date, `per_page=10`. Pages 0-2 hold every id exactly once, `total == 25`, `pages == 3`.
  - **Masking:** a row whose `credential_values` key is in `LIST_OF_SENSITIVE_FIELDS` returns the same masked values from `list_scopes` as from `run`.
  - **Empty set:** `list_scopes(user, frozenset())` raises `ValueError`.
- [ ] **Step 2:** Run the file. Expected: FAIL with `AttributeError: list_scopes`.
- [ ] **Step 3:** Move `run`'s body (`:40-51`) into `list_scopes`. `run` then returns `cls.list_scopes(user=user, scopes=frozenset({settings_type}), page=page, per_page=per_page, filters=filters)`.
- [ ] **Step 4:** `poetry run pytest tests/codemie/service/settings/ -v`. Expected: PASS.

### Task 3: `GET /v1/settings` with `scope`, validated paging and filters

**Files:**
- Modify `src/codemie/rest_api/models/settings.py`: the enum goes after `SettingType`, `:252-254`.
- Modify `src/codemie/rest_api/routers/settings.py`.
- Create `tests/codemie/rest_api/routers/test_settings_list.py`.
- Regenerate `tests/codemie/rest_api/router_guards/baselines/{openapi,route_precedence}.json`.

**Interfaces:**
- Consumes: Task 2's `list_scopes`.
- Produces: `SettingsScope(str, Enum)` with `ALL="all"`, `USER="user"` and `PROJECT="project"`. Its property `setting_types -> frozenset[SettingType]` returns both scopes for ALL.

Test-first: yes. `GET /v1/settings` with no params calls `list_scopes` with `scopes=frozenset({USER, PROJECT})` (fails: 404).

- [ ] **Step 1: Write the router tests.** Patch `list_scopes`, and set up the client and auth as `tests/codemie/rest_api/routers/test_settings_transfer.py` does.
  - **No `scope`:** both scopes, `page=0`, `per_page=10`, `filters={}`. The body is the mock's return value.
  - **Each value:** `scope=user` gives `{USER}`, `project` gives `{PROJECT}`, `all` gives both.
  - **`scope` inside `filters`:** `filters={"scope":"user"}` with no `scope` param still gives both scopes, and the dict passes through untouched.
  - **Unknown scope:** `scope=bogus` returns 422, and the body contains `'all'`, `'user'` and `'project'`.
  - **Bad paging:** `page=-1` returns 422, and so does `per_page=0`.
  - **Bad filters:** `filters={bad` and `filters=[1]` each return 422, and neither calls `list_scopes`.
- [ ] **Step 2:** `poetry run pytest tests/codemie/rest_api/routers/test_settings_list.py -v`. Expected: FAIL (404).
- [ ] **Step 3: Implement.**
  - Add `SettingsScope`.
  - Add `index_settings(user: User = Depends(authenticate), scope: SettingsScope = Query(SettingsScope.ALL), filters: Optional[str] = None, page: int = Query(0, ge=0), per_page: int = Query(10, ge=1))` at `GET /settings`, status 200. No `response_model`: the per-scope envelope stays. No `le` cap.
  - Add `_parse_filters(raw: str | None) -> dict`. Empty input gives `{}`. A `json.JSONDecodeError` or a non-dict result raises `ExtendedHTTPException(code=422, ...)`, shaped like `user_settings.py:87-92`.
  - The docstring names the scope values and says `scope` inside `filters` is ignored.
- [ ] **Step 4:** `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`. Check that the baseline diff adds only `GET /v1/settings`.
- [ ] **Step 5:** Run the new file and `tests/codemie/rest_api/router_guards/`. Expected: PASS.
