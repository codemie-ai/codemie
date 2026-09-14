# EPMCDME-13958: Fix ADO Duplicate Attachment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the same file from being re-uploaded and re-attached to an ADO work item when the same chat is used to update the work item multiple times.

**Architecture:** Add a pre-flight guard in `BaseAzureDevOpsFileWorkItemTool._process_attachments` that fetches the work item's existing `AttachedFile` relations before the upload loop, collects already-attached filenames, and skips any file whose name is already present. The `_get_attachment_relations` static helper and the `get_work_item(expand="Relations")` call pattern both already exist in the codebase.

**Tech Stack:** Python, pytest, `azure-devops` SDK (`WorkItemTrackingClient`), `unittest.mock`

---

## File Map

| Action | File |
|---|---|
| Modify | `src/codemie_tools/azure_devops/work_item/tools.py` — `_process_attachments` only |
| Modify | `tests/codemie_tools/azure_devops/work_item/test_tools.py` — add `TestUpdateWorkItemTool` cases and one `TestCreateWorkItemTool` case |

---

### Task 1: Add failing tests for attachment deduplication

**Files:**
- Modify: `tests/codemie_tools/azure_devops/work_item/test_tools.py`

- [ ] **Step 1: Add a `_make_file_object` helper after the existing `_make_relation` helper (line 217)**

Open `tests/codemie_tools/azure_devops/work_item/test_tools.py` and insert after line 217 (after `_make_relation`):

```python
def _make_file_object(name: str, content: bytes = b"file content", mime_type: str = "application/octet-stream"):
    """Build a mock FileObject for injection into config.input_files."""
    fo = Mock()
    fo.name = name
    fo.mime_type = mime_type
    fo.bytes_content.return_value = content
    return fo
```

- [ ] **Step 2: Add four failing test cases to `TestUpdateWorkItemTool`**

Add these four methods to the `TestUpdateWorkItemTool` class (after the existing `test_update_work_item_success`):

```python
    def test_update_does_not_reattach_file_already_in_relations(self, update_tool, mock_client):
        """Same file present in existing AttachedFile relations must not be re-uploaded."""
        work_item_id = 42
        mock_response = Mock()
        mock_response.id = work_item_id
        mock_client.update_work_item.return_value = mock_response

        existing_rel = _make_relation(
            "AttachedFile", "doc.pdf", "https://dev.azure.com/org/_apis/wit/attachments/aaa"
        )
        existing_wi = Mock()
        existing_wi.relations = [existing_rel]
        mock_client.get_work_item.return_value = existing_wi

        update_tool.config.input_files = [_make_file_object("doc.pdf")]

        with patch.object(update_tool, "_upload_attachment") as mock_upload:
            result = update_tool.execute(id=work_item_id, work_item_json='{"fields": {"System.Title": "T"}}')

        mock_upload.assert_not_called()
        assert "was updated" in result

    def test_update_attaches_new_file_not_yet_in_relations(self, update_tool, mock_client):
        """File with a name not in existing relations must still be uploaded and attached."""
        work_item_id = 42
        mock_response = Mock()
        mock_response.id = work_item_id
        mock_client.update_work_item.return_value = mock_response

        existing_rel = _make_relation(
            "AttachedFile", "old.pdf", "https://dev.azure.com/org/_apis/wit/attachments/bbb"
        )
        existing_wi = Mock()
        existing_wi.relations = [existing_rel]
        mock_client.get_work_item.return_value = existing_wi

        upload_url = "https://dev.azure.com/org/_apis/wit/attachments/ccc"
        update_tool.config.input_files = [_make_file_object("new.pdf", b"new content")]

        with patch.object(update_tool, "_upload_attachment", return_value=upload_url) as mock_upload:
            result = update_tool.execute(id=work_item_id, work_item_json='{"fields": {"System.Title": "T"}}')

        mock_upload.assert_called_once_with("new.pdf", b"new content")
        assert "new.pdf" in result

    def test_update_with_no_files_does_not_call_get_work_item(self, update_tool, mock_client):
        """When config.input_files is empty, get_work_item must not be called."""
        mock_response = Mock()
        mock_response.id = 1
        mock_client.update_work_item.return_value = mock_response
        update_tool.config.input_files = []

        update_tool.execute(id=1, work_item_json='{"fields": {"System.Title": "No files"}}')

        mock_client.get_work_item.assert_not_called()

    def test_update_dedup_is_case_insensitive(self, update_tool, mock_client):
        """Filename comparison must be case-insensitive (ADO stores names as provided)."""
        work_item_id = 5
        mock_response = Mock()
        mock_response.id = work_item_id
        mock_client.update_work_item.return_value = mock_response

        existing_rel = _make_relation(
            "AttachedFile", "REPORT.PDF", "https://dev.azure.com/org/_apis/wit/attachments/ddd"
        )
        existing_wi = Mock()
        existing_wi.relations = [existing_rel]
        mock_client.get_work_item.return_value = existing_wi

        update_tool.config.input_files = [_make_file_object("report.pdf")]

        with patch.object(update_tool, "_upload_attachment") as mock_upload:
            update_tool.execute(id=work_item_id, work_item_json='{"fields": {"System.Title": "T"}}')

        mock_upload.assert_not_called()
```

- [ ] **Step 3: Add one failing test to `TestCreateWorkItemTool`**

Add to `TestCreateWorkItemTool`:

```python
    def test_create_attaches_file_when_work_item_has_no_prior_relations(self, create_tool, mock_client):
        """Newly created work item has no existing relations — file must be uploaded normally."""
        mock_response = Mock()
        mock_response.id = 99
        mock_response.url = "http://test-url"
        mock_client.create_work_item.return_value = mock_response

        existing_wi = Mock()
        existing_wi.relations = None
        mock_client.get_work_item.return_value = existing_wi

        upload_url = "https://dev.azure.com/org/_apis/wit/attachments/eee"
        create_tool.config.input_files = [_make_file_object("spec.pdf", b"spec content")]

        with patch.object(create_tool, "_upload_attachment", return_value=upload_url) as mock_upload:
            result = create_tool.execute(work_item_json='{"fields": {"System.Title": "New"}}')

        mock_upload.assert_called_once_with("spec.pdf", b"spec content")
        assert "created successfully" in result
        assert "spec.pdf" in result
```

- [ ] **Step 4: Run the new tests to confirm they fail (RED)**

```bash
poetry run pytest tests/codemie_tools/azure_devops/work_item/test_tools.py::TestUpdateWorkItemTool::test_update_does_not_reattach_file_already_in_relations tests/codemie_tools/azure_devops/work_item/test_tools.py::TestUpdateWorkItemTool::test_update_attaches_new_file_not_yet_in_relations tests/codemie_tools/azure_devops/work_item/test_tools.py::TestUpdateWorkItemTool::test_update_with_no_files_does_not_call_get_work_item tests/codemie_tools/azure_devops/work_item/test_tools.py::TestUpdateWorkItemTool::test_update_dedup_is_case_insensitive tests/codemie_tools/azure_devops/work_item/test_tools.py::TestCreateWorkItemTool::test_create_attaches_file_when_work_item_has_no_prior_relations -v 2>&1 | tail -20
```

Expected: **5 FAILED** — `_upload_attachment` will be called when tests expect it not to be (or vice versa for the create test).

---

### Task 2: Implement the deduplication guard in `_process_attachments`

**Files:**
- Modify: `src/codemie_tools/azure_devops/work_item/tools.py` — `BaseAzureDevOpsFileWorkItemTool._process_attachments` (lines 157–199)

- [ ] **Step 1: Replace `_process_attachments` with the guarded version**

Replace the entire `_process_attachments` method (lines 157–199) with:

```python
    def _process_attachments(self, work_item_id: int) -> list[str]:
        """
        Process and attach files to the work item, skipping files already attached.

        Args:
            work_item_id: ID of the work item to attach files to

        Returns:
            List of attachment filenames that were successfully attached
        """
        files = self._resolve_files()

        if not files:
            return []

        existing_wi = self._client.get_work_item(
            id=work_item_id, project=self.config.project, expand="Relations"
        )
        existing_names = {
            rel.get("attributes", {}).get("name", "").lower()
            for rel in self._get_attachment_relations(existing_wi.relations)
            if rel.get("attributes", {}).get("name")
        }

        attached_files = []
        logger.info(f"Processing {len(files)} attachments for work item {work_item_id}...")

        for filename, (content, _) in files.items():
            if filename.lower() in existing_names:
                logger.info(f"Skipping '{filename}': already attached to work item {work_item_id}")
                continue
            try:
                attachment_url = self._upload_attachment(filename, content)

                patch_doc = [
                    {
                        "op": "add",
                        "path": "/relations/-",
                        "value": {
                            "rel": "AttachedFile",
                            "url": attachment_url,
                            "attributes": {"comment": f"Attached file: {filename}"},
                        },
                    }
                ]

                self._client.update_work_item(document=patch_doc, id=work_item_id, project=self.config.project)

                attached_files.append(filename)
                logger.info(f"Attached file '{filename}' to work item {work_item_id}")

            except Exception as e:
                logger.warning(f"Skipping attachment '{filename}' due to error: {str(e)}")

        return attached_files
```

- [ ] **Step 2: Run the new tests (GREEN)**

```bash
poetry run pytest tests/codemie_tools/azure_devops/work_item/test_tools.py::TestUpdateWorkItemTool::test_update_does_not_reattach_file_already_in_relations tests/codemie_tools/azure_devops/work_item/test_tools.py::TestUpdateWorkItemTool::test_update_attaches_new_file_not_yet_in_relations tests/codemie_tools/azure_devops/work_item/test_tools.py::TestUpdateWorkItemTool::test_update_with_no_files_does_not_call_get_work_item tests/codemie_tools/azure_devops/work_item/test_tools.py::TestUpdateWorkItemTool::test_update_dedup_is_case_insensitive tests/codemie_tools/azure_devops/work_item/test_tools.py::TestCreateWorkItemTool::test_create_attaches_file_when_work_item_has_no_prior_relations -v 2>&1 | tail -20
```

Expected: **5 PASSED**

- [ ] **Step 3: Run the full test class to verify no regressions**

```bash
poetry run pytest tests/codemie_tools/azure_devops/work_item/test_tools.py -v 2>&1 | tail -30
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/codemie_tools/azure_devops/work_item/tools.py tests/codemie_tools/azure_devops/work_item/test_tools.py
git commit -m "EPMCDME-13958: Prevent duplicate ADO work item attachment on repeated updates"
```

---

## Self-Review

**Spec coverage:**
- ✅ Duplicate attachment upload prevented on repeated updates → `_process_attachments` guard skips files already in `AttachedFile` relations
- ✅ New files still attached correctly → guard only skips filenames present in existing relations; new names pass through
- ✅ Dedup uses filename (explicitly listed as valid identifier in AC) → `rel.get("attributes", {}).get("name", "")` comparison
- ✅ ADO work item creation unaffected → newly created work item has no relations; guard finds no existing names; all files attached
- ✅ No regression for existing update functionality → existing test `test_update_work_item_success` uses no `input_files`; `_process_attachments` returns early; `get_work_item` not called; `assert_called_once` still holds
- ✅ Case-insensitive comparison → `.lower()` on both sides

**Placeholder scan:** No TBDs, no "similar to" references, no missing code blocks.

**Type consistency:** `_make_file_object` returns a `Mock` with `.name`, `.mime_type`, `.bytes_content()` matching what `_resolve_files` consumes via `file_obj.name`, `file_obj.mime_type`, `file_obj.bytes_content()`. `_get_attachment_relations` takes `work_item.relations` which may be `None` — handled (returns `[]`). `existing_names` set uses `.lower()` consistently.
