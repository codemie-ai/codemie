# No Orphan Skill When Companion Files Are Refused Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `SkillService.create_skill` never leaves an orphan skill row behind when companion files are refused or a later step fails.

**Architecture:** Extract the validation half of `_upload_companion_files` into a static `_validate_companion_payloads` helper. `create_skill` calls it before `SkillRepository.create` (primary fix), and wraps the post-create upload + update in `try/except Exception` that deletes the new row via `SkillRepository.delete` (safety net), logs cleanup failures, and re-raises the original error. Blobs are content-addressed and are deliberately not cleaned up.

**Tech Stack:** Python, FastAPI `ExtendedHTTPException`, SQLModel repositories, pytest + `unittest.mock`.

**Spec:** `docs/superpowers/tasks/2026-10-14-epmcdme-15965-orphan-skill-on-refused-bundle/spec.md` (research: `technical-analysis.md` in the same directory)

## Global Constraints

- Error codes and messages for invalid paths stay identical to today (400 `ExtendedHTTPException`; "Invalid companion file path", "Duplicate companion file path", "Invalid companion file payload").
- `MAX_SKILL_BUNDLE_PATH_LENGTH` is 943; a 944-char path is refused with 400.
- No blob deletion on failure; no change to `skill_bundle_seed_policy.py`; no frontend changes.
- Success path and the 409 duplicate-name path unchanged; `SkillMonitoringService.send_skill_management_metric` is sent only on success.
- `update_skill` behavior unchanged; existing `TestUploadCompanionFiles` and `TestCreateSkillWithCompanionFiles` tests pass unchanged.
- Commit messages: `EPMCDME-15965: <Description>`.
- Only run the narrow test scope needed (`tests/codemie/service/test_skill_service.py`); run `make ruff` per `.ai-run/guides/quality-gates.md` before finishing.

## Review Focus

- Path of exactly 943 chars still accepted, 944 refused with no `SkillRepository.create` call.
- Invalid base64 / unsupported encoding payload on create returns the same 400 and creates no record.
- `SkillRepository.update` returning/raising after create (not just upload failing) triggers cleanup.
- `SkillRepository.delete` itself raising must not mask the original exception.
- Two files where the second is invalid: no blob is written for the first (validation runs for the whole list before any write).

---

### Task 1: Extract `_validate_companion_payloads`

**Files:**
- Modify: `src/codemie/service/skill_service.py` (`_upload_companion_files`, ~L166-225; new helper placed directly above it)
- Test: `tests/codemie/service/test_skill_service.py` (new class `TestValidateCompanionPayloads`, placed before `TestUploadCompanionFiles`)

**Interfaces:**
- Produces: `SkillService._validate_companion_payloads(payloads: list[SkillCompanionFilePayload] | None) -> list[tuple[str, bytes]]` — static; returns `(normalized_path, raw_content)` per payload in input order; raises the same 400 `ExtendedHTTPException`s as today (path normalization, reserved `SKILL.md`, duplicate path, payload decode), checked per file in the same order as today. Empty/`None` input returns `[]`.
- Consumes: `SkillFileStorage._normalize_companion_file_path`, `SkillFileStorage._decode_companion_file_payload`, `SkillService.PRIMARY_BUNDLE_FILE_NAME`.

Test-first: yes — `SkillService._validate_companion_payloads` does not exist, so every new test fails with `AttributeError`.

- [ ] **Step 1: Write the failing tests**

Add before `class TestUploadCompanionFiles`:

```python
class TestValidateCompanionPayloads:
    """Tests for SkillService._validate_companion_payloads"""

    def test_returns_normalized_path_and_decoded_bytes_in_order(self):
        payloads = [
            SkillCompanionFilePayload(path="refs/a.md", content="hello", encoding="text"),
            SkillCompanionFilePayload(
                path="bin/b.dat", content=base64.b64encode(b"\x00\x01").decode(), encoding="base64"
            ),
        ]

        result = SkillService._validate_companion_payloads(payloads)

        assert result == [("refs/a.md", b"hello"), ("bin/b.dat", b"\x00\x01")]

    def test_none_and_empty_return_empty_list(self):
        assert SkillService._validate_companion_payloads(None) == []
        assert SkillService._validate_companion_payloads([]) == []

    def test_path_at_limit_is_accepted(self):
        path = "a" * 943
        payloads = [SkillCompanionFilePayload(path=path, content="x", encoding="text")]

        assert SkillService._validate_companion_payloads(payloads) == [(path, b"x")]

    def test_path_over_limit_raises_400(self):
        payloads = [SkillCompanionFilePayload(path="a" * 944, content="x", encoding="text")]

        with pytest.raises(ExtendedHTTPException) as exc_info:
            SkillService._validate_companion_payloads(payloads)

        assert exc_info.value.code == status.HTTP_400_BAD_REQUEST

    def test_reserved_skill_md_path_raises_400(self):
        payloads = [SkillCompanionFilePayload(path="skill.md", content="x", encoding="text")]

        with pytest.raises(ExtendedHTTPException) as exc_info:
            SkillService._validate_companion_payloads(payloads)

        assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
        assert exc_info.value.message == "Invalid companion file path"

    def test_duplicate_path_raises_400(self):
        payloads = [
            SkillCompanionFilePayload(path="refs/a.md", content="1", encoding="text"),
            SkillCompanionFilePayload(path="refs/a.md", content="2", encoding="text"),
        ]

        with pytest.raises(ExtendedHTTPException) as exc_info:
            SkillService._validate_companion_payloads(payloads)

        assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
        assert exc_info.value.message == "Duplicate companion file path"

    def test_invalid_base64_raises_400(self):
        payloads = [SkillCompanionFilePayload(path="refs/a.bin", content="!!not-base64!!", encoding="base64")]

        with pytest.raises(ExtendedHTTPException) as exc_info:
            SkillService._validate_companion_payloads(payloads)

        assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
        assert exc_info.value.message == "Invalid companion file payload"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/test_skill_service.py::TestValidateCompanionPayloads -v`
Expected: FAIL with `AttributeError: type object 'SkillService' has no attribute '_validate_companion_payloads'`

- [ ] **Step 3: Implement the helper and make `_upload_companion_files` use it**

In `src/codemie/service/skill_service.py`, add above `_upload_companion_files`:

```python
    @staticmethod
    def _validate_companion_payloads(
        payloads: list[SkillCompanionFilePayload] | None,
    ) -> list[tuple[str, bytes]]:
        """Validate companion file payloads without touching storage.

        Returns ``(normalized_path, raw_content)`` per payload, in input order. Raises a 400
        ``ExtendedHTTPException`` for an invalid path, the reserved ``SKILL.md`` path, a duplicate path
        or an undecodable payload, so callers can refuse a bad bundle before persisting anything.
        """
        validated: list[tuple[str, bytes]] = []
        seen_paths: set[str] = set()

        for file_payload in payloads or []:
            normalized_path = SkillFileStorage._normalize_companion_file_path(file_payload.path)
            if normalized_path.lower() == SkillService.PRIMARY_BUNDLE_FILE_NAME:
                raise ExtendedHTTPException(
                    code=status.HTTP_400_BAD_REQUEST,
                    message="Invalid companion file path",
                    details="Companion files cannot use the reserved path 'SKILL.md'",
                    help=(
                        "Keep the main skill instructions in the skill content field"
                        " and store only supporting files here"
                    ),
                )

            if normalized_path in seen_paths:
                raise ExtendedHTTPException(
                    code=status.HTTP_400_BAD_REQUEST,
                    message="Duplicate companion file path",
                    details=f"Companion file path '{normalized_path}' was provided more than once",
                    help="Ensure each bundled file path is unique within the skill",
                )

            seen_paths.add(normalized_path)
            validated.append((normalized_path, SkillFileStorage._decode_companion_file_payload(file_payload)))

        return validated
```

Then change the body of `_upload_companion_files` so the validation block disappears and the loop consumes the helper result (keep the docstring, `storage`, `stored_by_key`, `records`; drop `seen_paths`):

```python
        storage = SkillFileStorage(skill_id, file_repo)
        stored_by_key = {
            record["storage_key"]: record for record in existing_records or [] if record.get("storage_key")
        }
        records: list[dict[str, str | int]] = []

        validated = SkillService._validate_companion_payloads(payloads)

        for file_payload, (normalized_path, raw_content) in zip(payloads or [], validated, strict=True):
            storage_key, checksum = content_addressed_key(normalized_path, raw_content)
            stored = stored_by_key.get(storage_key)
            if stored and stored.get("checksum") == checksum:
                records.append(dict(stored))
                continue
            mime_type = mimetypes.guess_type(normalized_path)[0] or "application/octet-stream"
            storage.write_file(name=storage_key, mime_type=mime_type, content=raw_content)
            records.append(
                {
                    "path": normalized_path,
                    "mime_type": mime_type,
                    "encoding": file_payload.encoding,
                    "size_bytes": len(raw_content),
                    "storage_key": storage_key,
                    "checksum": checksum,
                }
            )

        return records
```

Note: validation now runs for the whole list before the first blob write, so a bad later file no longer leaves earlier blobs written. Error codes, messages and per-file check order are unchanged.

- [ ] **Step 4: Run tests to verify they pass (new + regression guard)**

Run: `poetry run pytest tests/codemie/service/test_skill_service.py -k "ValidateCompanionPayloads or UploadCompanionFiles or UpdateSkillCompanionFiles or CreateSkillWithCompanionFiles" -v`
Expected: all PASS, existing tests unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/skill_service.py tests/codemie/service/test_skill_service.py
git commit -m "EPMCDME-15965: Extract companion payload validation into helper"
```

---

### Task 2: Validate before create and roll back on later failure

**Files:**
- Modify: `src/codemie/service/skill_service.py` (`create_skill`, ~L496-565)
- Test: `tests/codemie/service/test_skill_service.py` (new class `TestCreateSkillNoOrphan`, placed after `TestCreateSkillWithCompanionFiles`; reuses its `_make_create_request` / `_make_skill` by subclassing)

**Interfaces:**
- Consumes: `SkillService._validate_companion_payloads` (Task 1), `SkillRepository.delete(skill_id) -> bool`, module `logger`.
- Produces: `create_skill` behavior — (a) invalid companion files raise the 400 before `SkillRepository.create`; (b) any `Exception` from upload/update after create triggers `SkillRepository.delete(skill.id)` then re-raises the original; a failing delete is logged and swallowed.

Test-first: yes — today `create_skill` calls `SkillRepository.create` before validating (the "create not called" tests fail) and never calls `SkillRepository.delete` (the rollback tests fail).

- [ ] **Step 1: Write the failing tests**

```python
class TestCreateSkillNoOrphan(TestCreateSkillWithCompanionFiles):
    """create_skill must not leave a skill row behind when companion files are refused or a later step fails."""

    def _run_create(self, request, owner_user, created_skill, *, upload_side_effect=None, update_side_effect=None):
        """Run create_skill with repository/storage mocked; return the mocks for assertions."""
        mocks = {}
        with patch.object(SkillRepository, "get_by_name_author_project", return_value=None):
            with patch.object(SkillRepository, "create", return_value=created_skill) as mocks["create"]:
                with patch.object(SkillRepository, "update", side_effect=update_side_effect) as mocks["update"]:
                    with patch.object(SkillRepository, "delete") as mocks["delete"]:
                        with patch("codemie.service.skill_service.FileRepositoryFactory") as mock_factory:
                            with patch("codemie.service.skill_service.SkillMonitoringService") as mocks["metric"]:
                                with patch("codemie.service.skill_service.logger") as mocks["logger"]:
                                    mock_factory.get_current_repository.return_value = MagicMock()
                                    if upload_side_effect is not None:
                                        with patch.object(
                                            SkillService, "_upload_companion_files", side_effect=upload_side_effect
                                        ):
                                            self._invoke(request, owner_user, mocks)
                                    else:
                                        self._invoke(request, owner_user, mocks)
        return mocks

    def _invoke(self, request, owner_user, mocks):
        try:
            SkillService.create_skill(request, owner_user)
        except Exception as exc:  # captured for assertions
            mocks["raised"] = exc

    @pytest.mark.parametrize(
        "bad_payload",
        [
            SkillCompanionFilePayload(path="a" * 944, content="x", encoding="text"),
            SkillCompanionFilePayload(path="SKILL.md", content="x", encoding="text"),
            SkillCompanionFilePayload(path="refs/a.bin", content="!!not-base64!!", encoding="base64"),
        ],
        ids=["path-too-long", "reserved-skill-md", "invalid-base64"],
    )
    def test_invalid_companion_file_returns_400_and_does_not_create(self, owner_user, bad_payload):
        request = self._make_create_request(companion_files=[bad_payload])

        mocks = self._run_create(request, owner_user, self._make_skill("skill-x", owner_user))

        assert isinstance(mocks["raised"], ExtendedHTTPException)
        assert mocks["raised"].code == status.HTTP_400_BAD_REQUEST
        mocks["create"].assert_not_called()
        mocks["delete"].assert_not_called()
        mocks["metric"].send_skill_management_metric.assert_not_called()

    def test_duplicate_companion_paths_return_400_and_do_not_create(self, owner_user):
        request = self._make_create_request(
            companion_files=[
                SkillCompanionFilePayload(path="refs/a.md", content="1", encoding="text"),
                SkillCompanionFilePayload(path="refs/a.md", content="2", encoding="text"),
            ]
        )

        mocks = self._run_create(request, owner_user, self._make_skill("skill-x", owner_user))

        assert mocks["raised"].code == status.HTTP_400_BAD_REQUEST
        assert mocks["raised"].message == "Duplicate companion file path"
        mocks["create"].assert_not_called()

    def test_valid_second_file_after_invalid_first_writes_no_blob(self, owner_user):
        request = self._make_create_request(
            companion_files=[
                SkillCompanionFilePayload(path="refs/ok.md", content="ok", encoding="text"),
                SkillCompanionFilePayload(path="a" * 944, content="x", encoding="text"),
            ]
        )
        file_repo = MagicMock()

        with patch.object(SkillRepository, "get_by_name_author_project", return_value=None):
            with patch.object(SkillRepository, "create") as mock_create:
                with patch("codemie.service.skill_service.FileRepositoryFactory") as mock_factory:
                    mock_factory.get_current_repository.return_value = file_repo
                    with pytest.raises(ExtendedHTTPException):
                        SkillService.create_skill(request, owner_user)

        mock_create.assert_not_called()
        file_repo.write_file.assert_not_called()

    def test_upload_failure_deletes_new_skill_and_reraises_original(self, owner_user):
        skill = self._make_skill("skill-up", owner_user)
        request = self._make_create_request(
            companion_files=[SkillCompanionFilePayload(path="guide.md", content="x", encoding="text")]
        )
        boom = RuntimeError("blob storage down")

        mocks = self._run_create(request, owner_user, skill, upload_side_effect=boom)

        assert mocks["raised"] is boom
        mocks["delete"].assert_called_once_with("skill-up")
        mocks["metric"].send_skill_management_metric.assert_not_called()

    def test_update_failure_deletes_new_skill_and_reraises_original(self, owner_user):
        skill = self._make_skill("skill-upd", owner_user)
        request = self._make_create_request(
            companion_files=[SkillCompanionFilePayload(path="guide.md", content="x", encoding="text")]
        )
        boom = RuntimeError("db update failed")

        mocks = self._run_create(request, owner_user, skill, update_side_effect=boom)

        assert mocks["raised"] is boom
        mocks["delete"].assert_called_once_with("skill-upd")
        mocks["metric"].send_skill_management_metric.assert_not_called()

    def test_cleanup_failure_is_logged_and_does_not_mask_original_error(self, owner_user):
        skill = self._make_skill("skill-cf", owner_user)
        request = self._make_create_request(
            companion_files=[SkillCompanionFilePayload(path="guide.md", content="x", encoding="text")]
        )
        boom = RuntimeError("blob storage down")

        with patch.object(SkillRepository, "delete", side_effect=RuntimeError("delete failed")):
            mocks = self._run_create_with_real_delete(request, owner_user, skill, boom)

        assert mocks["raised"] is boom
        mocks["logger"].error.assert_called_once()
        assert "skill-cf" in mocks["logger"].error.call_args[0][0]

    def _run_create_with_real_delete(self, request, owner_user, skill, upload_error):
        mocks = {}
        with patch.object(SkillRepository, "get_by_name_author_project", return_value=None):
            with patch.object(SkillRepository, "create", return_value=skill):
                with patch("codemie.service.skill_service.FileRepositoryFactory"):
                    with patch.object(SkillService, "_upload_companion_files", side_effect=upload_error):
                        with patch("codemie.service.skill_service.logger") as mocks["logger"]:
                            self._invoke(request, owner_user, mocks)
        return mocks

    def test_success_path_does_not_delete_and_sends_metric(self, owner_user):
        created = self._make_skill("skill-ok", owner_user)
        request = self._make_create_request(
            companion_files=[SkillCompanionFilePayload(path="guide.md", content="x", encoding="text")]
        )

        mocks = self._run_create(request, owner_user, created, update_side_effect=lambda *_a, **_k: created)

        assert "raised" not in mocks
        mocks["delete"].assert_not_called()
        mocks["metric"].send_skill_management_metric.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/test_skill_service.py::TestCreateSkillNoOrphan -v`
Expected: the "does not create" tests FAIL (`create` was called once) and the rollback tests FAIL (`delete` not called). The success-path test may already pass.

- [ ] **Step 3: Implement validation-first and rollback in `create_skill`**

In `create_skill`, directly after the 409 duplicate-name check and before building `skill_data`, add:

```python
        # Refuse an invalid bundle before anything is persisted, so a rejected request leaves no skill behind
        SkillService._validate_companion_payloads(request.companion_files)
```

Replace the block from `skill = SkillRepository.create(skill_data)` through the end of `if request.companion_files:` with:

```python
        skill = SkillRepository.create(skill_data)
        logger.info(f"Created skill '{skill.name}' (ID: {skill.id}) by user '{user.id}'")

        if request.companion_files:
            try:
                file_repo = FileRepositoryFactory.get_current_repository()
                companion_file_records = SkillService._upload_companion_files(
                    skill.id, request.companion_files, file_repo
                )
                skill = SkillRepository.update(skill.id, {"companion_files": companion_file_records})
            except Exception:
                SkillService._rollback_created_skill(skill.id)
                raise
```

Add the helper next to `create_skill` (static, same class):

```python
    @staticmethod
    def _rollback_created_skill(skill_id: str) -> None:
        """Delete a just-created skill whose companion files could not be stored.

        Uploaded blobs are intentionally left in place (content-addressed, may be referenced elsewhere).
        A cleanup failure is logged and never masks the error that triggered the rollback.
        """
        try:
            SkillRepository.delete(skill_id)
            logger.info(f"Rolled back skill (ID: {skill_id}) after companion file failure")
        except Exception:
            logger.error(f"Failed to roll back skill (ID: {skill_id}) after companion file failure", exc_info=True)
```

Note: `skill` is only reassigned on a successful `update`, so `skill.id` in the `except` is still the created record's id.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/test_skill_service.py -v`
Expected: all PASS (including pre-existing create/update/companion tests unchanged).

- [ ] **Step 5: Lint**

Run the Ruff gate from `.ai-run/guides/quality-gates.md` (`make ruff`) and fix any findings.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/skill_service.py tests/codemie/service/test_skill_service.py
git commit -m "EPMCDME-15965: Prevent orphan skill when companion files are refused"
```

---

## Self-Review

- **Spec coverage:** Req 1 (400, no record) → Task 2 validation-first tests. Req 2 (delete + re-raise, cleanup failure logged) → Task 2 rollback tests and `_rollback_created_skill`. Req 3 (no blob cleanup) → no delete code; documented in helper docstring. Req 4 (success/409/metric unchanged) → success-path test, 409 check untouched, metric after the try block. Req 5 (identical errors) → helper copies messages verbatim; Task 1 tests assert codes/messages; existing upload tests are the regression guard.
- **Placeholders:** none.
- **Type consistency:** `_validate_companion_payloads` returns `list[tuple[str, bytes]]` in Task 1 and is consumed with `zip(...)` there; Task 2 only calls it for its side effect. `_rollback_created_skill(skill_id: str)` matches its call.
- **Deviation from spec wording (compatible):** the helper also returns decoded content so `_upload_companion_files` does not decode twice on the update path; on the create path the payloads are still decoded twice (validate + upload), as the research noted, which is acceptable.
