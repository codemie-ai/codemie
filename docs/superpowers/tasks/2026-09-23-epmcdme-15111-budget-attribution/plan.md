# EPMCDME-15111 Budget Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the user's default project (added by EPMCDME-15110) the billing-attribution fallback wherever the charged project is today arbitrary or personal, across the web/assistant path, the shared LLM resolver, and the CLI/IDE proxy path.

**Architecture:** Single choke-point fix (Approach A from spec.md). Add `default_project: str | None` to the `User` security model, populated at its two construction sites directly from the already-fetched `UserProject` rows (no new repository method needed — `is_default` is already on those rows from EPMCDME-15110). Rewrite `User.current_project` to consult it first with a deterministic fallback, which fixes 9 "unbound flow" call sites for free. Insert a default-project tier into the two other existing resolution surfaces (`_resolve_effective_project`'s `is_global` branch, and the CLI proxy's `_extract_request_info`).

**Tech Stack:** Python 3.12, FastAPI, Pydantic (`User` is a `pydantic.BaseModel`, not SQLModel), pytest 8.3 / pytest-asyncio 0.23 / pytest-mock.

**Spec:** `docs/superpowers/tasks/2026-09-23-epmcdme-15111-budget-attribution/spec.md`

## Global Constraints

- No new dependencies, no new DB migration (EPMCDME-15110 already added `is_default` + its partial unique index).
- `_probe_project_budget_scopes` (`src/codemie/enterprise/litellm/proxy_router.py:539`) and `_probe_direct_project_budget_scopes` (`src/codemie/enterprise/litellm/llm_factory.py:631`) are duplicated by standing project convention and must never be unified/refactored. This plan does not touch either function's body — listed here only so no task accidentally "cleans them up."
- Commits use the `EPMCDME-15111:` prefix and land on the current branch `EPMCDME-15110_default-project-foundation` (combined MR !4315 per prior decision — do not create a new branch).
- `User` is a Pydantic `BaseModel` (`src/codemie/rest_api/security/user.py`), not a SQLModel/DB table — adding a field here is a pure in-memory model change, no migration.

## Correction versus the approved plan (post-review, CR-002 fix-up)

Code review (`code-review-final.json`) found that Task 1's switch from `project_names` (unordered DB order) to `sorted(project_names)` as the no-default fallback silently reassigns `current_project` for every pre-existing multi-project user with no explicit default, at deploy time - since EPMCDME-15110's migration is additive-only with no backfill. This plan's "No new dependencies, no new DB migration" Global Constraint no longer holds: a follow-up migration `d2c276d4390e_backfill_default_project_for_existing_users.py` was added to persist exactly what the new fallback already computes (alphabetically-first project per multi-project user with no default) as a real `is_default=true` row, turning a silent computed value into an explicit, auditable one visible through EPMCDME-15110's own admin API - a no-behavior-change, idempotent, data-only backfill. Verified against the live dev Postgres (3 seeded users: multi-project/no-default gets backfilled correctly, multi-project/has-default is untouched, single-project/no-default is untouched; re-run confirmed idempotent), always rolled back. Also fixed in the same round: CR-001, a third `User(...)` construction site (`EnterpriseIdpWrapper.authenticate()`, enterprise Keycloak/OIDC path) that was missed by this plan's Task 4/5 - never populated `default_project`, silently breaking AC2/4/8 for that auth path.

## Correction versus the approved spec

The spec's Component 2/3 proposed a **new async repository method** `aget_default_for_user` because `UserProjectRepository.get_default_for_user` (from EPMCDME-15110) is sync-only. Reading the actual population-site code (`AuthenticationService.load_user_for_auth`, `LocalIdp.authenticate`) shows both already fetch the full list of `UserProject` rows via `aget_by_user_id` before building the `User` object — and those rows already carry `is_default` (EPMCDME-15110). **No new repository method is needed**: `default_project` is derived in-place from the list already in hand via `next((p.project_name for p in projects if p.is_default), None)`. This is a strict simplification (fewer files touched, no new DB round-trip) — the resulting behavior is identical to what the spec described.

---

### Task 1: `User.default_project` field and deterministic `current_project`

**Files:**
- Modify: `src/codemie/rest_api/security/user.py:43-44` (field block), `:112-115` (`current_project` property)
- Test: `tests/codemie/rest_api/security/test_user.py`

**Interfaces:**
- Produces: `User.default_project: str | None` (new field, default `None`). `User.current_project -> str` (existing property, behavior changes: consults `default_project` first, then a **sorted** `project_names` instead of insertion order, then `DEMO_PROJECT`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/rest_api/security/test_user.py` (same file/class style as the existing `test_current_project_returns_first_application` at line 148 — add these as new methods in that class):

```python
def test_current_project_returns_default_project_when_set(self):
    """default_project wins over project_names order when set."""
    user = User(id="test", username="test", project_names=["zebra", "app1"], default_project="app1")
    assert user.current_project == "app1"

def test_current_project_is_deterministic_regardless_of_project_names_order(self):
    """Without a default, current_project picks deterministically (sorted), not insertion order."""
    user_a = User(id="test", username="test", project_names=["zebra", "app1"])
    user_b = User(id="test", username="test", project_names=["app1", "zebra"])
    assert user_a.current_project == user_b.current_project == "app1"

def test_current_project_ignores_falsy_default_project(self):
    """An explicit None/empty default_project falls through to the deterministic pick."""
    user = User(id="test", username="test", project_names=["zebra", "app1"], default_project=None)
    assert user.current_project == "app1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/rest_api/security/test_user.py -k "default_project or deterministic" -v`
Expected: FAIL — `default_project` is not a recognized field on `User` (Pydantic validation error), and/or `current_project` still returns `"zebra"` (first by insertion order) instead of `"app1"`.

- [ ] **Step 3: Implement**

In `src/codemie/rest_api/security/user.py`, add the field next to `admin_project_names` (line 44):

```python
    admin_project_names: list[str] = Field(default_factory=list)
    default_project: str | None = Field(default=None)
```

Replace the `current_project` property (lines 112-115):

```python
    @property
    def current_project(self) -> str:
        if self.default_project:
            return self.default_project
        apps = sorted(self.project_names) if self.project_names else [DEMO_PROJECT]
        return apps[0]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/security/test_user.py -v`
Expected: PASS — all tests in the file, including the 2 pre-existing `current_project` tests (`test_current_project_returns_first_application`, `test_current_project_returns_demo_when_no_applications`) and the 3 new ones. The pre-existing tests still pass because `["app1", "app2"]` and `[]` are unaffected by sorting (`sorted(["app1","app2"]) == ["app1","app2"]`).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/security/user.py tests/codemie/rest_api/security/test_user.py
git commit -m "EPMCDME-15111: Add default_project field, make current_project default-aware and deterministic"
```

---

### Task 2: Default-project tier in `_resolve_effective_project`'s `is_global` branch

**Files:**
- Modify: `src/codemie/service/llm_service/utils.py:50-54`
- Test: `tests/codemie/service/llm_service/test_utils_litellm_context.py`

**Interfaces:**
- Consumes: `User.default_project: str | None` (Task 1).
- Produces: no new symbols — `_resolve_effective_project` return value now includes a default-project case; signature unchanged.

- [ ] **Step 1: Write the failing tests**

The existing `_make_user()` helper (`tests/codemie/service/llm_service/test_utils_litellm_context.py:25-32`) builds a bare `MagicMock` — an unset `.default_project` attribute access on a `MagicMock` auto-returns a truthy child `Mock`, which would make every existing test in this file silently take the new "has a default project" branch once it exists. Fix the helper first so it does not mask the real behavior:

```python
def _make_user(email: str = "user@test.com") -> MagicMock:
    user = MagicMock()
    user.email = email
    user.project_names = []
    user.admin_project_names = []
    user.default_project = None
    user.id = "user-123"
    user.username = email
    return user
```

Then add new tests in `TestResolveEffectiveProject`-equivalent section (same style as `test_shared_assistant_returns_project` at line 298 — add as new module-level or class methods near it):

```python
def test_global_asset_not_a_member_uses_default_project(self):
    """AC2: not a member, has default project -> charged to default project."""
    asset = MagicMock(spec=['project', 'is_global'])
    asset.project = "assistant-project"
    asset.is_global = True
    user = _make_user()
    user.default_project = "my-default-project"

    result = _resolve_effective_project(asset, None, user)

    assert result == "my-default-project"

def test_global_asset_not_a_member_no_default_falls_back_to_email(self):
    """AC3: not a member, no default -> personal fallback unchanged."""
    asset = MagicMock(spec=['project', 'is_global'])
    asset.project = "assistant-project"
    asset.is_global = True
    user = _make_user()
    user.default_project = None

    result = _resolve_effective_project(asset, None, user)

    assert result == user.email

def test_global_asset_membership_wins_over_different_default(self):
    """AC7: member of asset's project AND has a different default -> asset's project wins."""
    asset = MagicMock(spec=['project', 'is_global'])
    asset.project = "assistant-project"
    asset.is_global = True
    user = _make_user()
    user.project_names = ["assistant-project"]
    user.default_project = "some-other-project"

    result = _resolve_effective_project(asset, None, user)

    assert result == "assistant-project"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/llm_service/test_utils_litellm_context.py -k "default_project" -v`
Expected: FAIL on `test_global_asset_not_a_member_uses_default_project` — result is `user.email`, not `"my-default-project"` (no default-project tier exists yet). The other two should already pass (confirming no accidental regression from the `_make_user` fix alone) — if `test_global_asset_membership_wins_over_different_default` fails too, that is a bug in the test setup, not the production code; re-check before proceeding.

- [ ] **Step 3: Implement**

In `src/codemie/service/llm_service/utils.py`, replace lines 50-54:

```python
    if getattr(asset, 'is_global', False):
        user_projects = set(user.project_names or []) | set(user.admin_project_names or [])
        if project in user_projects or not user.email:
            return project
        if user.default_project:
            return user.default_project
        return user.email
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/llm_service/test_utils_litellm_context.py -v`
Expected: PASS — full file, all pre-existing tests plus the 3 new ones.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/llm_service/utils.py tests/codemie/service/llm_service/test_utils_litellm_context.py
git commit -m "EPMCDME-15111: Insert default-project fallback tier into _resolve_effective_project"
```

---

### Task 3: Default-project tier in the CLI/IDE proxy's `_extract_request_info`

**Files:**
- Modify: `src/codemie/enterprise/litellm/proxy_router.py:412-427`
- Test: `tests/enterprise/litellm/test_proxy_router.py`

**Interfaces:**
- Consumes: `User.default_project: str | None` (Task 1).
- Produces: no new symbols — `_extract_request_info` return dict's `PROJECT` key now has a third fallback tier; signature unchanged.

- [ ] **Step 1: Write the failing tests**

The existing test `test_project_falls_back_to_user_username_when_header_missing` (`tests/enterprise/litellm/test_proxy_router.py:287-295`) builds `user = MagicMock()` with only `.username` set — an unset `.default_project` on that `MagicMock` auto-returns a truthy child `Mock`, which would break this existing test's assertion once the new tier exists (it would return the mock object instead of `"user@example.com"`). Fix it to be explicit, and add the new default-project case, in `TestExtractRequestInfo`:

```python
    def test_project_falls_back_to_user_username_when_header_missing(self):
        """Missing project header falls back to user.username for legacy CLI clients."""
        headers = Headers({})
        user = MagicMock()
        user.username = "user@example.com"
        user.default_project = None

        result = _extract_request_info(headers, user)

        assert result[PROJECT] == "user@example.com"

    def test_project_falls_back_to_default_project_when_header_missing(self):
        """AC8: header missing, user has a default project -> charged to default project."""
        headers = Headers({})
        user = MagicMock()
        user.username = "user@example.com"
        user.default_project = "my-default-project"

        result = _extract_request_info(headers, user)

        assert result[PROJECT] == "my-default-project"

    def test_project_header_wins_over_default_project(self):
        """AC9: explicit header wins regardless of default, unchanged."""
        headers = Headers({HEADER_CODEMIE_CLI_PROJECT: "explicit-project"})
        user = MagicMock()
        user.username = "user@example.com"
        user.default_project = "my-default-project"

        result = _extract_request_info(headers, user)

        assert result[PROJECT] == "explicit-project"
```

(Also update `test_project_taken_from_header_when_present` at line 277-285 to set `user.default_project = None` explicitly for the same reason, even though its header value already wins before either fallback is reached — do this for clarity/future-proofing, not because it currently fails.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/enterprise/litellm/test_proxy_router.py -k "default_project or header_wins" -v`
Expected: FAIL on `test_project_falls_back_to_default_project_when_header_missing` — result is `"user@example.com"` (falls straight to username, no default tier). The other two should already pass.

- [ ] **Step 3: Implement**

In `src/codemie/enterprise/litellm/proxy_router.py`, replace line 414:

```python
def _extract_request_info(headers: Headers | httpx.Headers | dict, user: User | None = None) -> dict:
    """Extract request metadata from headers (uses codemie constants)."""
    project = (
        headers.get(HEADER_CODEMIE_CLI_PROJECT)
        or (user.default_project if user else None)
        or (user.username if user else "")
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/enterprise/litellm/test_proxy_router.py -k TestExtractRequestInfo -v`
Expected: PASS — all `TestExtractRequestInfo` tests, including the 2 updated and 2 new ones.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/enterprise/litellm/proxy_router.py tests/enterprise/litellm/test_proxy_router.py
git commit -m "EPMCDME-15111: Insert default-project fallback tier into CLI proxy's _extract_request_info"
```

---

### Task 4: Populate `default_project` in `AuthenticationService.load_user_for_auth`

**Files:**
- Modify: `src/codemie/service/user/authentication_service.py:154-174`
- Test: `tests/codemie/service/user/test_authentication_service.py`

**Interfaces:**
- Consumes: `User.default_project` field (Task 1); `UserProject.is_default` (existing, from EPMCDME-15110).
- Produces: no new symbols — `load_user_for_auth`'s returned `User` now has `default_project` populated; signature unchanged.

- [ ] **Step 1: Write the failing test**

Add to `TestLoadUserForAuth` in `tests/codemie/service/user/test_authentication_service.py` (same style as `test_load_user_for_auth_found` at line 284):

```python
    @pytest.mark.asyncio
    async def test_load_user_for_auth_populates_default_project(self):
        """default_project is derived from the UserProject row with is_default=True."""
        session = AsyncMock()
        user_id = str(uuid4())

        mock_user = UserDB(
            id=user_id,
            username="testuser",
            name="Test User",
            email="test@example.com",
            picture="",
            user_type="human",
            is_admin=False,
        )
        mock_projects = [
            UserProject(user_id=user_id, project_name="project1", is_project_admin=True, is_default=False),
            UserProject(user_id=user_id, project_name="project2", is_project_admin=False, is_default=True),
        ]

        with (
            patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo,
            patch("codemie.service.user.authentication_service.user_project_repository") as mock_proj_repo,
            patch("codemie.service.user.authentication_service.user_kb_repository") as mock_kb_repo,
        ):
            mock_user_repo.aget_active_by_id = AsyncMock(return_value=mock_user)
            mock_proj_repo.aget_by_user_id = AsyncMock(return_value=mock_projects)
            mock_kb_repo.aget_by_user_id = AsyncMock(return_value=[])

            result = await AuthenticationService.load_user_for_auth(session, user_id)

            assert result.default_project == "project2"

    @pytest.mark.asyncio
    async def test_load_user_for_auth_no_default_project_set(self):
        """default_project is None when no UserProject row has is_default=True."""
        session = AsyncMock()
        user_id = str(uuid4())

        mock_user = UserDB(
            id=user_id, username="testuser", name="Test User", email="test@example.com",
            picture="", user_type="human", is_admin=False,
        )
        mock_projects = [
            UserProject(user_id=user_id, project_name="project1", is_project_admin=True, is_default=False),
        ]

        with (
            patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo,
            patch("codemie.service.user.authentication_service.user_project_repository") as mock_proj_repo,
            patch("codemie.service.user.authentication_service.user_kb_repository") as mock_kb_repo,
        ):
            mock_user_repo.aget_active_by_id = AsyncMock(return_value=mock_user)
            mock_proj_repo.aget_by_user_id = AsyncMock(return_value=mock_projects)
            mock_kb_repo.aget_by_user_id = AsyncMock(return_value=[])

            result = await AuthenticationService.load_user_for_auth(session, user_id)

            assert result.default_project is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/user/test_authentication_service.py -k "default_project" -v`
Expected: FAIL — `result.default_project` does not exist as a populated value (it will be `None` for both cases since the field defaults to `None` and nothing sets it yet; the first test's assertion `== "project2"` fails).

- [ ] **Step 3: Implement**

In `src/codemie/service/user/authentication_service.py`, modify `load_user_for_auth` (around line 159-174) to add one line before the `return` and thread it into the constructor:

```python
        # Map to security.User
        default_project = next((p.project_name for p in projects if p.is_default), None)
        return security_user.User(
            id=db_user.id,
            username=db_user.username,
            name=db_user.name or "",
            email=db_user.email,
            picture=db_user.picture or "",
            user_type=db_user.user_type,
            roles=[],  # IDP roles ignored when flag ON
            project_names=[p.project_name for p in projects],
            admin_project_names=[p.project_name for p in projects if p.is_project_admin],
            default_project=default_project,
            knowledge_bases=[kb.kb_name for kb in kbs],
            is_admin=db_user.is_admin,
            is_maintainer=db_user.is_maintainer,
            is_auditor=db_user.is_auditor,
            project_limit=db_user.project_limit,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/user/test_authentication_service.py -v`
Expected: PASS — full file, including the pre-existing `test_load_user_for_auth_found` (unaffected — it doesn't assert on `default_project`, and neither mock row has `is_default=True`, so the new field is `None`, matching the field's own default).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/user/authentication_service.py tests/codemie/service/user/test_authentication_service.py
git commit -m "EPMCDME-15111: Populate User.default_project in load_user_for_auth"
```

---

### Task 5: Populate `default_project` in `LocalIdp.authenticate`

**Files:**
- Modify: `src/codemie/rest_api/security/idp/local.py:98-117`
- Test: `tests/codemie/rest_api/security/idp/test_local_idp.py`

**Interfaces:**
- Consumes: `User.default_project` field (Task 1); `UserProject.is_default` (existing).
- Produces: no new symbols — `LocalIdp.authenticate`'s DB-backed branch now populates `default_project`; signature unchanged.

- [ ] **Step 1: Write the failing test**

Add to `tests/codemie/rest_api/security/idp/test_local_idp.py` (same style as `test_authenticate_db_success_with_user_management_enabled` at line 66):

```python
@pytest.mark.asyncio
async def test_authenticate_db_success_populates_default_project(mocker):
    """default_project is derived from the UserProject row with is_default=True."""
    mocker.patch("codemie.rest_api.security.user.config.ENABLE_USER_MANAGEMENT", True)

    idp = LocalIdp()
    test_user_id = "db_user"

    mock_project = MagicMock(project_name="proj-1", is_project_admin=True, is_default=True)
    mock_kb = MagicMock(kb_name="kb-1")
    mock_db_user = MagicMock()
    mock_db_user.id = test_user_id
    mock_db_user.username = "db_username"
    mock_db_user.name = "DB Name"
    mock_db_user.email = "db@example.com"
    mock_db_user.picture = "http://pic"
    mock_db_user.user_type = None
    mock_db_user.is_admin = True
    mock_db_user.is_maintainer = False
    mock_db_user.is_auditor = False
    mock_db_user.project_limit = 10

    class _AsyncSessionCtx:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, *_):
            pass

    mocker.patch("codemie.clients.postgres.get_async_session", return_value=_AsyncSessionCtx())
    mocker.patch(
        "codemie.repository.user_repository.user_repository.aget_active_by_id",
        new=AsyncMock(return_value=mock_db_user),
    )
    mocker.patch(
        "codemie.repository.user_project_repository.user_project_repository.aget_by_user_id",
        new=AsyncMock(return_value=[mock_project]),
    )
    mocker.patch(
        "codemie.repository.user_kb_repository.user_kb_repository.aget_by_user_id",
        new=AsyncMock(return_value=[mock_kb]),
    )

    user = await idp.authenticate(_make_request(test_user_id))

    assert user.default_project == "proj-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/rest_api/security/idp/test_local_idp.py -k default_project -v`
Expected: FAIL — `user.default_project` is `None` (nothing populates it yet).

- [ ] **Step 3: Implement**

In `src/codemie/rest_api/security/idp/local.py`, modify `authenticate` (around lines 98-117) to add one line before the `return` and thread it into the constructor:

```python
                projects = await user_project_repository.aget_by_user_id(session, db_user.id)
                kbs = await user_kb_repository.aget_by_user_id(session, db_user.id)

            default_project = next((p.project_name for p in projects if p.is_default), None)
            return User(
                id=db_user.id,
                username=db_user.username,
                name=db_user.name or "",
                email=db_user.email,
                picture=db_user.picture or "",
                user_type=db_user.user_type,
                roles=[],
                project_names=[p.project_name for p in projects],
                admin_project_names=[p.project_name for p in projects if p.is_project_admin],
                default_project=default_project,
                knowledge_bases=[kb.kb_name for kb in kbs],
                is_admin=db_user.is_admin,
                is_maintainer=db_user.is_maintainer,
                is_auditor=db_user.is_auditor,
                project_limit=db_user.project_limit,
                auth_token=auth_token,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/security/idp/test_local_idp.py -v`
Expected: PASS — full file, including the pre-existing `test_authenticate_db_success_with_user_management_enabled` (its `mock_project` is a bare `MagicMock(project_name="proj-1", is_project_admin=True)` with no `is_default` set — attribute access auto-returns a truthy child `Mock`, so `default_project` becomes `"proj-1"` for that test too; it has no assertion on `default_project`, so this does not fail it, but is worth knowing when reading the diff).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/security/idp/local.py tests/codemie/rest_api/security/idp/test_local_idp.py
git commit -m "EPMCDME-15111: Populate User.default_project in LocalIdp.authenticate"
```

---

### Task 6: Regression sweep and verify-only ACs (6, 10, 11, 12)

No production code changes in this task — it closes out the plan by (a) running the full affected-surface regression sweep once all 5 code tasks have landed, and (b) recording the verification evidence for the 4 ACs the spec identified as already satisfied by existing code, so QA has a concrete trail instead of an assumption.

**Files:**
- No files modified.
- Test: re-run all files touched by Tasks 1-5, plus a targeted read of the verify-only code paths.

**Interfaces:**
- Consumes: all outputs of Tasks 1-5.
- Produces: nothing new — this task is a checkpoint, not a deliverable.

Test-first: no — this task runs and reads existing/newly-added tests and code, it does not add new behavior or new tests of its own.

- [ ] **Step 1: Run the full regression sweep across every file touched by Tasks 1-5**

Run:
```bash
poetry run pytest \
  tests/codemie/rest_api/security/test_user.py \
  tests/codemie/service/llm_service/test_utils_litellm_context.py \
  tests/enterprise/litellm/test_proxy_router.py \
  tests/codemie/service/user/test_authentication_service.py \
  tests/codemie/rest_api/security/idp/test_local_idp.py \
  -v
```
Expected: PASS, zero failures. This is the same set of files Tasks 1-5 already ran individually — this step is the combined confirmation that none of the 5 changes interact badly with each other (e.g. Task 1's `current_project` rewrite plus Task 2's `is_global` branch change, exercised together via any shared fixture).

- [ ] **Step 2: Verify AC6 (fallback visible in logs) by reading, not modifying**

Read `src/codemie/service/budget/budget_resolution_service.py` around the `_global_context` fallback (the INFO-level `budget_event=budget_resolution_global_fallback ... reason=project_budget_not_found` log identified in spec.md / technical-analysis.md). Confirm the log statement still includes `project_name` in its fields — since Tasks 1-4 now feed a default project name into this function where `None`/username used to flow, this log will now fire with the *default* project's name on the "no budget for this project" path, which is exactly AC6's requirement. No code change; if the log line is missing `project_name`, that is a new finding to raise, not something this task fixes silently.

- [ ] **Step 3: Verify AC10 (analytics matches charged project) by reading, not modifying**

Read one monitoring call site (e.g. `ConversationMonitoringService.send_conversation_metric`, `src/codemie/service/monitoring/conversation_monitoring_service.py`) and confirm it still reads `get_current_project(fallback=...)` — the same LiteLLM context `set_llm_context` populates via `_resolve_effective_project` (Task 2's surface). Since Task 2 makes `_resolve_effective_project` return the default project in the new tier, and `get_current_project` reads that same context, the two stay consistent by construction — no code change needed.

- [ ] **Step 4: Verify AC11 (cache respects default-project change within ~1min) by reading, not modifying**

Read `_resolution_cache`'s key definition in `src/codemie/service/budget/budget_resolution_service.py` (`(project_name, budget_category, user_id)`, 60s TTL). Confirm this is unchanged by Tasks 1-5. Changing a user's default project (via EPMCDME-15110's `set_default_project`) changes the effective `project_name` fed into this cache on the *next* request naturally — old entries under the old project name simply age out within the existing 60s TTL. No invalidation hook needed, no code change.

- [ ] **Step 5: Verify AC12 (removed-from-project clears default) by reading, not modifying**

Read `UserProjectRepository.remove_project`/`.aremove_project` (`src/codemie/repository/user_project_repository.py`) and confirm they still hard-delete the `UserProject` row (unchanged by this plan). Combined with Task 4/5's `next((p.project_name for p in projects if p.is_default), None)` deriving fresh from the DB-fetched list on every login/session-load, a user removed from their default project gets `default_project=None` on their next authenticated request — no stale caching of the field across the removal. No code change.

- [ ] **Step 6: Record verification evidence**

No commit needed for this task (nothing changed) — the QA report (Stage 7) will cite this task's steps 2-5 as the evidence trail for AC6/10/11/12 instead of new test files, consistent with spec.md's "verify-only, YAGNI" call for these four ACs.

## Post-review revision — backfill migration removed

Migration `d2c276d4390e_backfill_default_project_for_existing_users` (CR-002 fix-up) was removed.
It was described as "no-behavior-change", which only holds for `User.current_project`. It also
feeds `User.default_project`, which two other resolvers now consult *before* the personal
fallback:

- `_resolve_effective_project` (global/marketplace assistants, non-member) — main bills the
  personal budget (`user.email`); with the backfill it would bill the alphabetically-first project.
- `_extract_request_info` (CLI/IDE proxy and browser-extension completions without
  `X-CodeMie-Project`) — main bills `user.username`; with the backfill, the same arbitrary project.

That would silently move spend of every existing multi-project user to a project no admin chose,
contradicting the story ("default project" = an admin decision; EPMCDME-15110 lists backfilling
as out of scope). Without the migration `current_project` behaves identically (it computes the same
`sorted(project_names)[0]` on the fly), and the two resolvers keep main's personal fallback until
an admin sets an explicit default.
