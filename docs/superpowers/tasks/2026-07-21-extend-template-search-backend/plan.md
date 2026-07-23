# EPMCDME-13583: Extend Template Search Backend

> **For agentic workers:** Use `superpowers:test-driven-development` to implement this plan inline, task by task. Steps use checkbox syntax for tracking.

**Goal:** Extend template search filtering to match on `description` and `system_prompt` fields in addition to existing `name` matching, enabling users to find templates by content.

**Architecture:** Modify the `_apply_template_filters()` function in the assistant router to apply case-insensitive substring matching to three fields (`name`, `description`, `system_prompt`) sequentially, then apply categorical and author filters. All changes are to the filter logic in one function; templates remain in-memory hardcoded list.

**Tech Stack:** Python 3.12+, FastAPI, pytest, case-insensitive string matching

## Global Constraints

- Change must be backward compatible (additive filters, no breaking changes)
- All text fields (`name`, `description`, `system_prompt`) are required on `AssistantBase` — no None checks needed for search terms
- Templates remain in-memory hardcoded list (~35 items); no database changes
- Substring matching is case-insensitive, consistent with existing `name` filter
- No feature flags or configuration needed
- Must add comprehensive unit tests with no placeholders

---

## Task 1: Write and run unit tests for current name-only search

**Files:**
- Modify: `tests/codemie/rest_api/routers/test_assistant.py` — add test class and fixtures for `_apply_template_filters()`
- Test: same file

**Interfaces:**
- Consumes: existing test utilities in `test_assistant.py` (fixtures, pytest patterns)
- Produces: test fixtures for template objects and test cases validating name-only search (baseline before implementing description/system_prompt search)

**Test-first:** YES — write failing tests for name search first to establish baseline behavior before extending to new fields.

- [ ] **Step 1: Read existing test file structure and determine test location**

Read the test file to understand existing patterns and find best location for new test class:

```bash
grep -n "^class\|^def test_" tests/codemie/rest_api/routers/test_assistant.py | head -20
```

- [ ] **Step 2: Create template test fixtures**

Add these fixtures to `tests/codemie/rest_api/routers/test_assistant.py` (after existing fixtures):

```python
@pytest.fixture
def template_with_content():
    """Template fixture for filter testing."""
    from codemie.rest_api.models.assistant import AssistantBase
    from codemie.rest_api.models.assistant import CreatedByUser
    
    return AssistantBase(
        id="template-1",
        name="Python Code Review Assistant",
        description="Reviews Python code for best practices",
        system_prompt="You are a Python expert. Review the code and provide feedback.",
        categories=["coding", "review"],
        created_by=CreatedByUser(id="user-1", name="Alice", username="alice"),
    )

@pytest.fixture
def template_data_processing():
    """Template for data processing workflows."""
    from codemie.rest_api.models.assistant import AssistantBase
    from codemie.rest_api.models.assistant import CreatedByUser
    
    return AssistantBase(
        id="template-2",
        name="Data Processing Pipeline",
        description="Helps design ETL and data transformation workflows",
        system_prompt="You are a data engineer. Help optimize data pipelines.",
        categories=["data", "etl"],
        created_by=CreatedByUser(id="user-2", name="Bob", username="bob"),
    )

@pytest.fixture
def template_documentation():
    """Template for documentation generation."""
    from codemie.rest_api.models.assistant import AssistantBase
    
    return AssistantBase(
        id="template-3",
        name="Tech Writer",
        description="Generates technical documentation from code",
        system_prompt="You help write clear, accurate technical documentation.",
        categories=["docs"],
        created_by=None,
    )
```

- [ ] **Step 3: Create test class for `_apply_template_filters()` with name-only tests**

Add to `tests/codemie/rest_api/routers/test_assistant.py`:

```python
class TestApplyTemplateFilters:
    """Unit tests for _apply_template_filters function."""
    
    def test_empty_filters_returns_all_templates(self, template_with_content, template_data_processing, template_documentation):
        """Empty filter dict returns all templates."""
        from codemie.rest_api.routers.assistant import _apply_template_filters
        
        templates = [template_with_content, template_data_processing, template_documentation]
        result = _apply_template_filters(templates, {})
        assert result == templates
    
    def test_none_filters_returns_all_templates(self, template_with_content, template_data_processing, template_documentation):
        """None filters returns all templates."""
        from codemie.rest_api.routers.assistant import _apply_template_filters
        
        templates = [template_with_content, template_data_processing, template_documentation]
        result = _apply_template_filters(templates, None)
        assert result == templates
    
    def test_name_filter_substring_match(self, template_with_content, template_data_processing, template_documentation):
        """Search filter matches substring in name (case-insensitive)."""
        from codemie.rest_api.routers.assistant import _apply_template_filters
        
        templates = [template_with_content, template_data_processing, template_documentation]
        result = _apply_template_filters(templates, {"search": "Python"})
        assert result == [template_with_content]
    
    def test_name_filter_case_insensitive(self, template_with_content, template_data_processing, template_documentation):
        """Name filter is case-insensitive."""
        from codemie.rest_api.routers.assistant import _apply_template_filters
        
        templates = [template_with_content, template_data_processing, template_documentation]
        result = _apply_template_filters(templates, {"search": "python code review"})
        assert result == [template_with_content]
    
    def test_name_filter_no_match(self, template_with_content, template_data_processing, template_documentation):
        """Name filter returns empty list when no match."""
        from codemie.rest_api.routers.assistant import _apply_template_filters
        
        templates = [template_with_content, template_data_processing, template_documentation]
        result = _apply_template_filters(templates, {"search": "Nonexistent"})
        assert result == []
    
    def test_name_filter_using_name_key(self, template_with_content, template_data_processing, template_documentation):
        """Name filter works with 'name' key as alias for 'search'."""
        from codemie.rest_api.routers.assistant import _apply_template_filters
        
        templates = [template_with_content, template_data_processing, template_documentation]
        result = _apply_template_filters(templates, {"name": "Pipeline"})
        assert result == [template_data_processing]
```

- [ ] **Step 4: Run tests to verify name-only search works (RED phase)**

```bash
pytest tests/codemie/rest_api/routers/test_assistant.py::TestApplyTemplateFilters -v
```

Expected: All tests PASS (current code already implements name search correctly).

- [ ] **Step 5: Commit baseline tests**

```bash
git add tests/codemie/rest_api/routers/test_assistant.py
git commit -m "test: add unit tests for template name filter (baseline)"
```

---

## Task 2: Write failing tests for description and system_prompt search

**Files:**
- Modify: `tests/codemie/rest_api/routers/test_assistant.py` — add tests for description and system_prompt filters

**Interfaces:**
- Consumes: test fixtures from Task 1 (template objects with all three text fields)
- Produces: failing tests defining expected behavior for new search fields

**Test-first:** YES — write tests that describe the desired behavior (description + system_prompt search) and let them fail before implementing.

- [ ] **Step 1: Add failing tests for description search**

Add to the `TestApplyTemplateFilters` class in `tests/codemie/rest_api/routers/test_assistant.py`:

```python
    def test_description_filter_currently_not_matching(self, template_with_content, template_data_processing, template_documentation):
        """Description filter should match but currently does not (failing test for new feature)."""
        from codemie.rest_api.routers.assistant import _apply_template_filters
        
        templates = [template_with_content, template_data_processing, template_documentation]
        # Search for "Python" should match template_with_content by description
        result = _apply_template_filters(templates, {"search": "best practices"})
        assert len(result) == 1
        assert result[0].id == "template-1"
    
    def test_description_filter_case_insensitive(self, template_with_content, template_data_processing, template_documentation):
        """Description filter is case-insensitive."""
        from codemie.rest_api.routers.assistant import _apply_template_filters
        
        templates = [template_with_content, template_data_processing, template_documentation]
        result = _apply_template_filters(templates, {"search": "BEST PRACTICES"})
        assert len(result) == 1
        assert result[0].id == "template-1"
    
    def test_description_filter_no_match(self, template_with_content, template_data_processing, template_documentation):
        """Description filter returns empty when no match in description."""
        from codemie.rest_api.routers.assistant import _apply_template_filters
        
        templates = [template_with_content, template_data_processing, template_documentation]
        result = _apply_template_filters(templates, {"search": "nonexistent phrase"})
        assert result == []
```

- [ ] **Step 2: Add failing tests for system_prompt search**

Add to the `TestApplyTemplateFilters` class:

```python
    def test_system_prompt_filter_currently_not_matching(self, template_with_content, template_data_processing, template_documentation):
        """System_prompt filter should match but currently does not (failing test for new feature)."""
        from codemie.rest_api.routers.assistant import _apply_template_filters
        
        templates = [template_with_content, template_data_processing, template_documentation]
        result = _apply_template_filters(templates, {"search": "Python expert"})
        assert len(result) == 1
        assert result[0].id == "template-1"
    
    def test_system_prompt_filter_case_insensitive(self, template_with_content, template_data_processing, template_documentation):
        """System_prompt filter is case-insensitive."""
        from codemie.rest_api.routers.assistant import _apply_template_filters
        
        templates = [template_with_content, template_data_processing, template_documentation]
        result = _apply_template_filters(templates, {"search": "data engineer"})
        assert len(result) == 1
        assert result[0].id == "template-2"
    
    def test_system_prompt_filter_no_match(self, template_with_content, template_data_processing, template_documentation):
        """System_prompt filter returns empty when no match."""
        from codemie.rest_api.routers.assistant import _apply_template_filters
        
        templates = [template_with_content, template_data_processing, template_documentation]
        result = _apply_template_filters(templates, {"search": "nonexistent system prompt phrase"})
        assert result == []
```

- [ ] **Step 3: Add tests for combined field searches**

Add to the `TestApplyTemplateFilters` class:

```python
    def test_search_matches_across_all_three_fields(self, template_with_content, template_data_processing, template_documentation):
        """Search should match if term appears in name, description, or system_prompt."""
        from codemie.rest_api.routers.assistant import _apply_template_filters
        
        templates = [template_with_content, template_data_processing, template_documentation]
        
        # "Code" appears in template_with_content name
        result = _apply_template_filters(templates, {"search": "Code"})
        assert template_with_content in result
        
        # "Engineer" appears in template_data_processing system_prompt
        result = _apply_template_filters(templates, {"search": "Engineer"})
        assert template_data_processing in result
    
    def test_search_no_match_across_all_three_fields(self, template_with_content, template_data_processing, template_documentation):
        """Search returns empty when no match in any of the three fields."""
        from codemie.rest_api.routers.assistant import _apply_template_filters
        
        templates = [template_with_content, template_data_processing, template_documentation]
        result = _apply_template_filters(templates, {"search": "completely_unique_phrase_not_in_any_field"})
        assert result == []
```

- [ ] **Step 4: Add tests for interaction with other filters**

Add to the `TestApplyTemplateFilters` class:

```python
    def test_description_search_combined_with_categories_filter(self, template_with_content, template_data_processing, template_documentation):
        """Description search works in combination with categories filter."""
        from codemie.rest_api.routers.assistant import _apply_template_filters
        
        templates = [template_with_content, template_data_processing, template_documentation]
        # Search for "Helps" should match template_data_processing by description, which has "data" category
        result = _apply_template_filters(templates, {"search": "Helps", "categories": ["data"]})
        assert result == [template_data_processing]
    
    def test_description_search_combined_with_created_by_filter(self, template_with_content, template_data_processing, template_documentation):
        """Description search works in combination with created_by filter."""
        from codemie.rest_api.routers.assistant import _apply_template_filters
        
        templates = [template_with_content, template_data_processing, template_documentation]
        # Search matches template_data_processing by description, filter by created_by="bob"
        result = _apply_template_filters(templates, {"search": "ETL", "created_by": "bob"})
        assert result == [template_data_processing]
    
    def test_combined_filters_no_match_when_search_matches_but_categories_dont(self, template_with_content, template_data_processing, template_documentation):
        """No result when search matches but other filters exclude it."""
        from codemie.rest_api.routers.assistant import _apply_template_filters
        
        templates = [template_with_content, template_data_processing, template_documentation]
        # Search matches template_data_processing but categories filter excludes it
        result = _apply_template_filters(templates, {"search": "Pipeline", "categories": ["docs"]})
        assert result == []
```

- [ ] **Step 5: Run tests to verify they fail (RED phase)**

```bash
pytest tests/codemie/rest_api/routers/test_assistant.py::TestApplyTemplateFilters::test_description_filter_currently_not_matching -v
```

Expected: FAIL with "AssertionError: assert [] == [...]" (description search not yet implemented).

- [ ] **Step 6: Run all new tests to confirm RED phase**

```bash
pytest tests/codemie/rest_api/routers/test_assistant.py::TestApplyTemplateFilters -v | grep -E "PASSED|FAILED"
```

Expected: Name filter tests PASS, new description/system_prompt tests FAIL.

- [ ] **Step 7: Commit failing tests**

```bash
git add tests/codemie/rest_api/routers/test_assistant.py
git commit -m "test: add failing tests for description and system_prompt search"
```

---

## Task 3: Implement description and system_prompt search

**Files:**
- Modify: `src/codemie/rest_api/routers/assistant.py` — extend `_apply_template_filters()` function (lines 171–191)

**Interfaces:**
- Consumes: `parsed_filters` dict with "search" key (or "name" alias)
- Produces: filtered list of templates matching search term in name, description, or system_prompt (case-insensitive substring match)

**Test-first:** NO — implementation follows from failing tests in Task 2.

- [ ] **Step 1: Understand current filter implementation**

Read lines 171–191 to understand the current logic:

```bash
sed -n '171,191p' src/codemie/rest_api/routers/assistant.py
```

Expected output shows name-only filtering with `name_filter.lower() in t.name.lower()`.

- [ ] **Step 2: Extend `_apply_template_filters()` to search all three fields**

Replace the name-filter section (lines 177–178) to check all three fields. Edit `src/codemie/rest_api/routers/assistant.py`:

```python
def _apply_template_filters(templates, parsed_filters):
    if not parsed_filters:
        return templates
    name_filter = parsed_filters.get("search") or parsed_filters.get("name")
    categories_filter = parsed_filters.get("categories")
    created_by_filter = parsed_filters.get("created_by")
    if name_filter:
        name_filter_lower = name_filter.lower()
        templates = [
            t
            for t in templates
            if (
                name_filter_lower in t.name.lower()
                or name_filter_lower in t.description.lower()
                or name_filter_lower in t.system_prompt.lower()
            )
        ]
    if categories_filter:
        templates = [t for t in templates if any(c in (t.categories or []) for c in categories_filter)]
    if created_by_filter:
        templates = [
            t
            for t in templates
            if t.created_by
            and (
                getattr(t.created_by, "name", None) == created_by_filter
                or getattr(t.created_by, "username", None) == created_by_filter
            )
        ]
    return templates
```

- [ ] **Step 3: Run all filter tests to verify they now pass (GREEN phase)**

```bash
pytest tests/codemie/rest_api/routers/test_assistant.py::TestApplyTemplateFilters -v
```

Expected: All tests PASS.

- [ ] **Step 4: Run full test suite to check for regressions**

```bash
pytest tests/codemie/rest_api/routers/test_assistant.py -v
```

Expected: No new failures in existing tests.

- [ ] **Step 5: Commit implementation**

```bash
git add src/codemie/rest_api/routers/assistant.py
git commit -m "feat(EPMCDME-13583): extend template search to description and system_prompt fields"
```

---

## Task 4: Add docstring to `_apply_template_filters()` function

**Files:**
- Modify: `src/codemie/rest_api/routers/assistant.py` — add docstring to function (line 171)

**Interfaces:**
- Consumes: function signature and behavior (already implemented)
- Produces: documented function explaining filter behavior, field coverage, and parameter semantics

**Test-first:** NO — documentation follows implementation.

- [ ] **Step 1: Add comprehensive docstring**

Read the current function to understand its behavior:

```bash
sed -n '171,191p' src/codemie/rest_api/routers/assistant.py
```

- [ ] **Step 2: Add docstring before function definition**

Edit `src/codemie/rest_api/routers/assistant.py`, replacing line 171 and adding:

```python
def _apply_template_filters(templates, parsed_filters):
    """Apply search and categorical filters to template list.
    
    Filters templates by search term and optional metadata. Search is performed
    as case-insensitive substring matching across three fields: name, description,
    and system_prompt. Categorical and author filters are applied sequentially
    after search, each reducing the result set.
    
    Args:
        templates: List of AssistantBase template objects to filter.
        parsed_filters: Dict with optional keys:
            - "search" or "name": substring to match in name, description, or system_prompt (case-insensitive)
            - "categories": list of category strings to match against template.categories
            - "created_by": username or name to match against template.created_by
    
    Returns:
        Filtered list of template objects matching all applied filters (AND semantics).
        Returns all templates if parsed_filters is None or empty dict.
    """
    if not parsed_filters:
        return templates
    # ... rest of function
```

- [ ] **Step 3: Commit docstring**

```bash
git add src/codemie/rest_api/routers/assistant.py
git commit -m "docs: add docstring to _apply_template_filters function"
```

---

## Verification

After completing all tasks:

1. Run the full test suite:
   ```bash
   pytest tests/codemie/rest_api/routers/test_assistant.py::TestApplyTemplateFilters -v
   ```
   Expected: All tests PASS.

2. Test manually via the endpoint (requires running local server):
   ```bash
   curl "http://localhost:8000/v1/assistants?scope=TEMPLATES&filters=%7B%22search%22:%22ETL%22%7D"
   ```
   Expected: Templates matching "ETL" in name, description, or system_prompt are returned.

3. Verify no regressions:
   ```bash
   pytest tests/codemie/rest_api/routers/ -v --tb=short
   ```
   Expected: No new failures.

---

## Summary

**Implementation surface:**
- 1 function modified: `_apply_template_filters()` in `src/codemie/rest_api/routers/assistant.py` (4 lines changed)
- ~60 lines of comprehensive unit tests added to `tests/codemie/rest_api/routers/test_assistant.py`
- 1 docstring added for documentation

**Key decisions:**
- Substring matching is case-insensitive (consistent with existing `name` filter pattern)
- All three text fields (`name`, `description`, `system_prompt`) are searched with OR semantics within search, AND semantics with other filters
- No new parameters or breaking changes; backward compatible

**Test coverage:**
- Name search baseline (existing functionality)
- Description search (new)
- System prompt search (new)
- Combined multi-field search
- Case-insensitivity
- No-match scenarios
- Interaction with categorical and author filters
