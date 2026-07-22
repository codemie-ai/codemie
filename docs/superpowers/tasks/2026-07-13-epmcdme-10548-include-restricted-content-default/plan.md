# Fix include_restricted_content Default and Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the bug where `include_restricted_content` is silently dropped on save, and change the default value to `True` for new Confluence datasource requests.

**Architecture:** Fix the serialization methods `to_confluence_index_info()` and `from_confluence_index_info()` to preserve `include_restricted_content`, and update default values in 3 schema locations (API request, persisted model, runtime config).

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLModel, pytest

---

## Requirements Summary

**From EPMCDME-10548:**
- After Confluence API update, access to restricted spaces on kb.epam.com is broken
- `include_restricted_content=true` correctly indexes restricted content when set
- Bug: field is not being persisted to DB correctly
- Goal: Make new datasource requests default `include_restricted_content` to `True`

**From Technical Analysis:**
- Primary bug: `to_confluence_index_info()` and `from_confluence_index_info()` only serialize `cql`, dropping `include_restricted_content`
- Three locations need default changes: `ConfluenceIndexInfo` (line 127), `IndexKnowledgeBaseConfluenceRequest` (line 1239), `IndexKnowledgeBaseConfluenceConfig` (line 47)

---

## File Structure

**Modified files:**
- `src/codemie/datasource/confluence_datasource_processor.py:47,57-66` - Runtime config and serialization methods
- `src/codemie/rest_api/models/index.py:127,1239` - Schema models (persisted, POST request)
- `tests/codemie/datasource/test_confluence_datasource_processor.py:36-62` - Update existing tests for new defaults

---

### Task 1: Fix serialization to preserve include_restricted_content

**Files:**
- Modify: `src/codemie/datasource/confluence_datasource_processor.py:57-66`
- Test-first: yes - Write failing test for field preservation first

- [ ] **Step 1: Write failing test for include_restricted_content round-trip**

Add to `tests/codemie/datasource/test_confluence_datasource_processor.py` after line 62:

```python
def test_to_confluence_index_info_preserves_include_restricted_content(self):
    config = IndexKnowledgeBaseConfluenceConfig(
        cql='test cql',
        include_restricted_content=True,
    )
    
    result = config.to_confluence_index_info()
    
    self.assertEqual(result.cql, 'test cql')
    self.assertTrue(result.include_restricted_content, "Expected include_restricted_content to be preserved")

def test_from_confluence_index_info_preserves_include_restricted_content(self):
    index_info = ConfluenceIndexInfo(
        cql='test cql',
        include_restricted_content=True,
    )
    
    result = IndexKnowledgeBaseConfluenceConfig.from_confluence_index_info(index_info)
    
    self.assertEqual(result.cql, 'test cql')
    self.assertTrue(result.include_restricted_content, "Expected include_restricted_content to be preserved")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/codemie/datasource/test_confluence_datasource_processor.py::TestIndexKnowledgeBaseConfluenceConfig::test_to_confluence_index_info_preserves_include_restricted_content -v
pytest tests/codemie/datasource/test_confluence_datasource_processor.py::TestIndexKnowledgeBaseConfluenceConfig::test_from_confluence_index_info_preserves_include_restricted_content -v
```

Expected: Both tests FAIL - assertions fail because current implementation only preserves `cql`

- [ ] **Step 3: Fix to_confluence_index_info() to preserve include_restricted_content**

In `src/codemie/datasource/confluence_datasource_processor.py`, replace lines 57-60:

```python
def to_confluence_index_info(self) -> ConfluenceIndexInfo:
    return ConfluenceIndexInfo(
        cql=self.cql,
        include_restricted_content=self.include_restricted_content,
    )
```

- [ ] **Step 4: Fix from_confluence_index_info() to preserve include_restricted_content**

In `src/codemie/datasource/confluence_datasource_processor.py`, replace lines 63-66:

```python
@classmethod
def from_confluence_index_info(cls, index_info: ConfluenceIndexInfo):
    return IndexKnowledgeBaseConfluenceConfig(
        cql=index_info.cql,
        include_restricted_content=index_info.include_restricted_content,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/codemie/datasource/test_confluence_datasource_processor.py::TestIndexKnowledgeBaseConfluenceConfig::test_to_confluence_index_info_preserves_include_restricted_content -v
pytest tests/codemie/datasource/test_confluence_datasource_processor.py::TestIndexKnowledgeBaseConfluenceConfig::test_from_confluence_index_info_preserves_include_restricted_content -v
```

Expected: Both tests PASS

- [ ] **Step 6: Commit serialization fix**

```bash
git add src/codemie/datasource/confluence_datasource_processor.py tests/codemie/datasource/test_confluence_datasource_processor.py
git commit -m "EPMCDME-10548: Fix serialization to preserve include_restricted_content"
```

---

### Task 2: Change include_restricted_content default to True

**Files:**
- Modify: `src/codemie/datasource/confluence_datasource_processor.py:47`
- Modify: `src/codemie/rest_api/models/index.py:127`
- Modify: `src/codemie/rest_api/models/index.py:1239`
- Test-first: yes - Update existing test assertions first

- [ ] **Step 1: Write failing test for new default value**

Update existing test assertions in `tests/codemie/datasource/test_confluence_datasource_processor.py`. Replace line 45:

```python
# Line 45 - in test_to_confluence_index_info
self.assertTrue(result.include_restricted_content, "Expected include_restricted_content to be True by default")
```

And replace line 59:

```python
# Line 59 - in test_from_confluence_index_info  
self.assertTrue(result.include_restricted_content, "Expected include_restricted_content to be True by default")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/codemie/datasource/test_confluence_datasource_processor.py::TestIndexKnowledgeBaseConfluenceConfig::test_to_confluence_index_info -v
pytest tests/codemie/datasource/test_confluence_datasource_processor.py::TestIndexKnowledgeBaseConfluenceConfig::test_from_confluence_index_info -v
```

Expected: Both tests FAIL - `include_restricted_content` is still `False` by default

- [ ] **Step 3: Update IndexKnowledgeBaseConfluenceConfig default**

In `src/codemie/datasource/confluence_datasource_processor.py`, line 47:

```python
include_restricted_content: Optional[bool] = True
```

- [ ] **Step 4: Update ConfluenceIndexInfo default**

In `src/codemie/rest_api/models/index.py`, line 127:

```python
include_restricted_content: Optional[bool] = True
```

- [ ] **Step 5: Update IndexKnowledgeBaseConfluenceRequest default**

In `src/codemie/rest_api/models/index.py`, line 1239:

```python
include_restricted_content: Optional[bool] = True
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/codemie/datasource/test_confluence_datasource_processor.py::TestIndexKnowledgeBaseConfluenceConfig -v
```

Expected: All tests in TestIndexKnowledgeBaseConfluenceConfig PASS

- [ ] **Step 7: Run full datasource processor test suite**

```bash
pytest tests/codemie/datasource/test_confluence_datasource_processor.py -v
```

Expected: All tests PASS

- [ ] **Step 8: Commit default value changes**

```bash
git add src/codemie/datasource/confluence_datasource_processor.py src/codemie/rest_api/models/index.py tests/codemie/datasource/test_confluence_datasource_processor.py
git commit -m "EPMCDME-10548: Change include_restricted_content default to True"
```

---

### Task 3: Integration verification

**Files:**
- Test-first: no - This is end-to-end verification after all changes

- [ ] **Step 1: Run full test suite for affected modules**

```bash
pytest tests/codemie/datasource/test_confluence_datasource_processor.py -v
pytest tests/codemie/datasource/loader/test_confluence_loader.py -v
```

Expected: All tests PASS

- [ ] **Step 2: Verify Pydantic model consistency**

```bash
python -c "
from codemie.datasource.confluence_datasource_processor import IndexKnowledgeBaseConfluenceConfig
from codemie.rest_api.models.index import ConfluenceIndexInfo, IndexKnowledgeBaseConfluenceRequest

# Check defaults match across all 3 models
config = IndexKnowledgeBaseConfluenceConfig(cql='test')
info = ConfluenceIndexInfo(cql='test')
request = IndexKnowledgeBaseConfluenceRequest(datasource_name='test', project_name='test', description='test', cql='test')

assert config.include_restricted_content == True, 'Config default should be True'
assert info.include_restricted_content == True, 'Info default should be True'
assert request.include_restricted_content == True, 'Request default should be True'

print('✓ All defaults correctly set to True')

# Check round-trip preserves the value
config_with_true = IndexKnowledgeBaseConfluenceConfig(cql='test', include_restricted_content=True)
serialized = config_with_true.to_confluence_index_info()
deserialized = IndexKnowledgeBaseConfluenceConfig.from_confluence_index_info(serialized)

assert deserialized.include_restricted_content == True, 'Round-trip should preserve True'

print('✓ Round-trip preserves include_restricted_content=True')

# Check that False can still be explicitly set
config_with_false = IndexKnowledgeBaseConfluenceConfig(cql='test', include_restricted_content=False)
serialized_false = config_with_false.to_confluence_index_info()
deserialized_false = IndexKnowledgeBaseConfluenceConfig.from_confluence_index_info(serialized_false)

assert deserialized_false.include_restricted_content == False, 'Round-trip should preserve False when explicitly set'

print('✓ Round-trip preserves include_restricted_content=False when explicit')
print('✓ All verification checks passed')
"
```

Expected: All assertions pass, prints "✓ All verification checks passed"

- [ ] **Step 3: Run linter and formatter**

```bash
ruff check src/codemie/datasource/confluence_datasource_processor.py src/codemie/rest_api/models/index.py
ruff format src/codemie/datasource/confluence_datasource_processor.py src/codemie/rest_api/models/index.py tests/codemie/datasource/test_confluence_datasource_processor.py
```

Expected: No errors, files formatted correctly

- [ ] **Step 4: Final commit - verification and any formatting fixes**

```bash
git add -u
git commit -m "EPMCDME-10548: Final verification and formatting"
```

---

## Testing Summary

**Coverage provided:**
- Unit tests for `to_confluence_index_info()` preserving `include_restricted_content` (new)
- Unit tests for `from_confluence_index_info()` preserving `include_restricted_content` (new)
- Updated existing tests for new default value (modified)
- Integration verification via Python assertions (Task 3)

**Test commands:**
```bash
# Run all affected tests
pytest tests/codemie/datasource/test_confluence_datasource_processor.py -v

# Run with coverage
pytest tests/codemie/datasource/test_confluence_datasource_processor.py --cov=codemie.datasource.confluence_datasource_processor --cov-report=term-missing
```

---

## Deployment Notes

**Pre-deployment checklist:**
1. All tests passing in CI/CD pipeline
2. Code review approved
3. QA verified on staging environment with real Confluence integration

**Post-deployment actions:**
1. Monitor logs for any Confluence indexing errors
2. Verify new datasources created after deployment have `include_restricted_content=true` in DB
3. Test kb.epam.com access to restricted spaces

**Rollback plan:**
If issues arise, revert the defaults back to `False` in all 3 locations. The serialization fix is safe to keep - it doesn't change behavior, only prevents data loss.

**Note on other flags:**
The technical analysis revealed that 5 other boolean flags (`include_archived_content`, `include_attachments`, `include_comments`, `keep_markdown_format`, `keep_newlines`) also suffer from the same serialization bug. This plan only fixes `include_restricted_content` as requested. If needed, the same fix can be applied to other flags in a follow-up ticket.

---

## Acceptance Criteria from EPMCDME-10548

1. ✅ Integration tool updated to support latest Confluence API - serialization fix preserves field
2. ✅ kb.epam.com correctly displays restricted spaces - default now True
3. ✅ Access verified for affected use cases - manual testing required post-deployment
4. ✅ No regressions for public spaces - False can still be explicitly set
5. ✅ Documentation updated if needed - noted in deployment section
