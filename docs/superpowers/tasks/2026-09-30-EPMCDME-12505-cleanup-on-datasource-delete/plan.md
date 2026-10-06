# EPMCDME-12505 (B) — Detach a deleted datasource from assistants — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a datasource is deleted, remove it from the persisted `context` of every assistant in the same project, so no assistant keeps a dangling reference.

**Architecture:** One best-effort function `detach_datasource_from_assistants(datasource)` in a new module `codemie/service/assistant/datasource_cleanup.py`, called from `IndexInfo.delete()` right after the row is deleted. `IndexInfo.delete()` is the single funnel for all 7 delete paths (router `delete_index` normal + provider branch, Bedrock `delete_entities` / `unimport_entity` / `validate_remote_entity_exists_and_cleanup` / `invoke_knowledge_base`, preconfigured-assistants script) and already cascades ES / GitRepo / scheduler cleanup with local imports and try/except. Option A (tolerant init, commit `52b80281d`) stays as the runtime safety net for versions, rollbacks and anything this misses.

**Tech Stack:** Python 3.12, FastAPI, SQLModel/SQLAlchemy on Postgres (JSONB `assistants.context` with GIN index `ix_assistants_context`), pytest with mocked sessions.

**Spec:** no `spec.md` (sdlc-light). Requirements = task description in the run + Network thread decision A+B (29–30.09) + user choice 30.09: hook in `IndexInfo.delete()`. Codebase context: `technical-analysis.md` in this folder.

## Global Constraints

- Match on **both** `name == datasource.repo_name` and `context_type == Context.index_info_type(datasource)`; same project only (`Assistant.project == datasource.project_name`).
- Update the **master assistant row only**; no new `AssistantConfiguration` version (system delete paths have no `User`; precedent `SkillRepository.remove_skill_from_all_assistants`).
- **Never fail a datasource delete** because of the cleanup: exceptions are caught and logged as warnings in `IndexInfo.delete()`.
- **Same-key guard:** if another datasource with the same `(project_name, repo_name, context_type)` still exists after the delete (Bedrock names are not unique), detach nothing.
- No top-level import of the new module from `models/index.py` or the Bedrock service (circular import via `assistant_service` → `BedrockOrchestratorService`); use a local import inside `delete()`.
- Out of scope: user-facing chat notice (separate follow-up), workflow YAML `datasource_ids`, rewriting old assistant versions.
- Run tests with `LC_ALL=en_US.UTF-8` prefix.

## Review Focus

1. Two datasources with the same name but different type (e.g. code repo `x` and KB `x`) in one project — deleting one must keep the other in every assistant. → Task 1 test `test_keeps_same_name_other_type`.
2. Bedrock re-import leaves another row with the same `(project, repo_name, type)` — assistants must keep the reference. → Task 1 test `test_guard_skips_when_same_key_still_exists`.
3. Cleanup raises (DB hiccup, legacy row fails to load) — the datasource delete must still complete. → Task 2 test `test_delete_survives_cleanup_failure`.
4. An assistant with only this one datasource — context becomes `[]`, not `None`, and the assistant keeps working. → Task 1 test `test_last_datasource_leaves_empty_list`.
5. The query must be scoped to the project (JSONB containment alone would match other projects). → Task 1 test `test_query_scoped_to_project` + python-side `test_skips_assistant_from_other_project`.

---

### Task 1: `detach_datasource_from_assistants` service function

**Files:**
- Create: `src/codemie/service/assistant/datasource_cleanup.py`
- Test: `tests/codemie/service/assistant/test_datasource_cleanup.py`

**Interfaces:**
- Consumes: `Assistant`, `Context`, `ContextType` (`codemie.rest_api.models.assistant`), `IndexInfo` (`codemie.rest_api.models.index`).
- Produces: `detach_datasource_from_assistants(datasource: IndexInfo) -> int` (number of assistants updated); private `_same_key_still_exists(project: str, repo_name: str, context_type: ContextType) -> bool`.

Test-first: yes — `test_removes_datasource_keeps_others` fails with `ModuleNotFoundError: codemie.service.assistant.datasource_cleanup`.

- [ ] **Step 1: Write the failing tests**

```python
# (license header as in sibling test files)
from unittest.mock import MagicMock, patch

from codemie.rest_api.models.assistant import Assistant, Context, ContextType
from codemie.rest_api.models.index import IndexInfo
from codemie.service.assistant import datasource_cleanup
from codemie.service.assistant.datasource_cleanup import detach_datasource_from_assistants

MODULE = "codemie.service.assistant.datasource_cleanup"
DELETED = Context(context_type=ContextType.KNOWLEDGE_BASE, name="kb-x")
OTHER_KB = Context(context_type=ContextType.KNOWLEDGE_BASE, name="kb-other")
SAME_NAME_CODE = Context(context_type=ContextType.CODE, name="kb-x")


def _datasource():
    return IndexInfo(project_name="proj", repo_name="kb-x", index_type="knowledge_base_file", description="d")


def _assistant(context, project="proj"):
    assistant = MagicMock(spec=Assistant)
    assistant.project = project
    assistant.context = list(context)
    return assistant


def _run(assistants, still_exists=False):
    session = MagicMock()
    session.exec.return_value.all.return_value = assistants
    with (
        patch(f"{MODULE}._same_key_still_exists", return_value=still_exists),
        patch(f"{MODULE}.Session") as session_cls,
    ):
        session_cls.return_value.__enter__.return_value = session
        updated = detach_datasource_from_assistants(_datasource())
    return updated, session


def test_removes_datasource_keeps_others():
    assistant = _assistant([OTHER_KB, DELETED])
    updated, session = _run([assistant])
    assert updated == 1
    assert assistant.context == [OTHER_KB]
    session.add.assert_called_once_with(assistant)
    session.commit.assert_called_once()


def test_keeps_same_name_other_type():
    assistant = _assistant([SAME_NAME_CODE, DELETED])
    _run([assistant])
    assert assistant.context == [SAME_NAME_CODE]


def test_last_datasource_leaves_empty_list():
    assistant = _assistant([DELETED])
    _run([assistant])
    assert assistant.context == []


def test_skips_assistant_from_other_project():
    assistant = _assistant([DELETED], project="another")
    updated, session = _run([assistant])
    assert updated == 0
    assert assistant.context == [DELETED]
    session.add.assert_not_called()


def test_guard_skips_when_same_key_still_exists():
    assistant = _assistant([DELETED])
    with patch(f"{MODULE}._same_key_still_exists", return_value=True), patch(f"{MODULE}.Session") as session_cls:
        assert detach_datasource_from_assistants(_datasource()) == 0
    session_cls.assert_not_called()
    assert assistant.context == [DELETED]


def test_query_scoped_to_project():
    _, session = _run([])
    statement = session.exec.call_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": False}))
    assert "assistants.project" in sql
    assert "assistants.context" in sql


def test_same_key_still_exists_matches_type():
    session = MagicMock()
    session.exec.return_value.all.return_value = ["code"]
    with patch(f"{MODULE}.Session") as session_cls:
        session_cls.return_value.__enter__.return_value = session
        assert datasource_cleanup._same_key_still_exists("proj", "kb-x", ContextType.KNOWLEDGE_BASE) is False
        assert datasource_cleanup._same_key_still_exists("proj", "kb-x", ContextType.CODE) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `LC_ALL=en_US.UTF-8 poetry run pytest tests/codemie/service/assistant/test_datasource_cleanup.py -v`
Expected: collection ERROR `ModuleNotFoundError: No module named 'codemie.service.assistant.datasource_cleanup'`

- [ ] **Step 3: Write the implementation**

```python
# (license header as in sibling modules)
from datetime import datetime

from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Session, and_, select

from codemie.rest_api.models.assistant import Assistant, Context, ContextType
from codemie.rest_api.models.index import IndexInfo


def _same_key_still_exists(project: str, repo_name: str, context_type: ContextType) -> bool:
    with Session(IndexInfo.get_engine()) as session:
        index_types = session.exec(
            select(IndexInfo.index_type).where(
                and_(IndexInfo.project_name == project, IndexInfo.repo_name == repo_name)
            )
        ).all()
    return any(Context.index_info_type_from_index_type(index_type) == context_type for index_type in index_types)


def detach_datasource_from_assistants(datasource: IndexInfo) -> int:
    """Remove a deleted datasource from the context of every assistant in its project. Master row only."""
    context_type = Context.index_info_type(datasource)
    if _same_key_still_exists(datasource.project_name, datasource.repo_name, context_type):
        return 0

    target = Context(context_type=context_type, name=datasource.repo_name)
    updated = 0
    with Session(Assistant.get_engine()) as session:
        statement = select(Assistant).where(
            and_(
                Assistant.project == datasource.project_name,
                Assistant.context.cast(JSONB).contains(  # type: ignore[attr-defined]
                    [{"name": datasource.repo_name, "context_type": context_type}]
                ),
            )
        )
        for assistant in session.exec(statement).all():
            if assistant.project != datasource.project_name:
                continue
            kept = [ctx for ctx in assistant.context if ctx != target]
            if len(kept) == len(assistant.context):
                continue
            assistant.context = kept
            assistant.update_date = datetime.now()
            session.add(assistant)
            updated += 1
        session.commit()
    return updated
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `LC_ALL=en_US.UTF-8 poetry run pytest tests/codemie/service/assistant/test_datasource_cleanup.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/assistant/datasource_cleanup.py tests/codemie/service/assistant/test_datasource_cleanup.py
git commit -m "EPMCDME-12505: Detach deleted datasource from same-project assistants (plan task 1)"
```

### Task 2: Hook the cleanup into `IndexInfo.delete()`

**Files:**
- Modify: `src/codemie/rest_api/models/index.py:1298-1328` (`IndexInfo.delete`)
- Test: `tests/codemie/rest_api/models/test_index_info.py` (append)

**Interfaces:**
- Consumes: `detach_datasource_from_assistants(datasource: IndexInfo) -> int` from Task 1 (local import).
- Produces: `IndexInfo.delete()` behavior — after `super().delete()`, detaches the datasource from assistants; logs `info` with the count when > 0, logs `warning` and continues on any exception.

Test-first: yes — `test_delete_detaches_datasource_from_assistants` fails because `detach_datasource_from_assistants` is never called.

- [ ] **Step 1: Write the failing tests** (append to `tests/codemie/rest_api/models/test_index_info.py`)

```python
from unittest.mock import patch as _patch

CLEANUP = "codemie.service.assistant.datasource_cleanup.detach_datasource_from_assistants"


def _kb_index():
    return IndexInfo(project_name="proj", repo_name="kb-x", index_type="knowledge_base_file", description="d", id="ds-1")


def _delete_with_mocks(index, cleanup_side_effect=None):
    calls = []
    with (
        _patch("codemie.rest_api.models.index.ElasticSearchClient") as es,
        _patch("codemie.rest_api.models.index.SchedulerSettingsService"),
        _patch("codemie.rest_api.models.base.BaseModelWithSQLSupport.delete", side_effect=lambda *args: calls.append("row")),
        _patch(CLEANUP, side_effect=cleanup_side_effect or (lambda ds: calls.append("cleanup") or 1)) as cleanup,
    ):
        es.get_client.return_value.indices.exists.return_value = False
        index.delete()
    return calls, cleanup


def test_delete_detaches_datasource_from_assistants():
    index = _kb_index()
    calls, cleanup = _delete_with_mocks(index)
    cleanup.assert_called_once_with(index)
    assert calls == ["row", "cleanup"]


def test_delete_survives_cleanup_failure():
    index = _kb_index()
    calls, cleanup = _delete_with_mocks(index, cleanup_side_effect=RuntimeError("db down"))
    cleanup.assert_called_once_with(index)
    assert calls == ["row"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `LC_ALL=en_US.UTF-8 poetry run pytest tests/codemie/rest_api/models/test_index_info.py -k "detaches or survives" -v`
Expected: FAIL — `AssertionError: Expected 'detach_datasource_from_assistants' to be called once. Called 0 times.`

- [ ] **Step 3: Implement** — replace the final `super().delete()` line of `IndexInfo.delete()` with:

```python
        super().delete()

        # Detach from assistants after the row is gone, so the same-key guard sees the final state
        try:
            from codemie.service.assistant.datasource_cleanup import detach_datasource_from_assistants

            detached = detach_datasource_from_assistants(self)
            if detached:
                logger.info(f"Detached datasource {self.id} from {detached} assistant(s)")
        except Exception as e:
            logger.warning(f"Failed to detach datasource {self.id} from assistants: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `LC_ALL=en_US.UTF-8 poetry run pytest tests/codemie/rest_api/models/test_index_info.py tests/codemie/rest_api/routers/test_index.py tests/codemie/service/aws_bedrock/test_bedrock_knowledge_base_service.py tests/codemie/service/assistant/test_datasource_cleanup.py -v`
Expected: all pass (existing router / Bedrock tests mock `delete()` or tolerate the extra call; if any real `IndexInfo.delete()` now hits a DB session in a test, patch `CLEANUP` in that test rather than changing production code).

- [ ] **Step 5: Mutation check** — temporarily remove the `detach_datasource_from_assistants(self)` call, rerun Step 2's command, confirm `test_delete_detaches_datasource_from_assistants` FAILS; restore by reversing that exact edit (not `git checkout` of the file).

- [ ] **Step 6: Commit**

```bash
git add src/codemie/rest_api/models/index.py tests/codemie/rest_api/models/test_index_info.py
git commit -m "EPMCDME-12505: Clean assistant context on every datasource delete path (plan task 2)"
```

### Task 3: MR !4351 description — A+B

**Files:** none in repo (GitLab MR description, updated at handoff via `mr-creator` / by the user; push only after the user confirms VPN).

Test-first: no — documentation only.

- [ ] **Step 1:** Draft the new description section in chat: agreed A+B (link the Network thread), B lives in `IndexInfo.delete()` and covers 7 delete paths (list them — the thread said 3; research found 7), master row only / no new version, same-key guard for Bedrock, user-facing notice = follow-up. Keep `## Test harness` placeholder; MR stays Draft.
