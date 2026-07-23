# Technical Research

**Task**: template search backend filters REST API
**Generated**: 2026-07-21T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

Extend `_apply_template_filters` in `src/codemie/rest_api/routers/assistant.py` to match templates by description and system_prompt (content), not just name. Currently filters match name only via case-insensitive substring. Templates are hardcoded in-memory (~35 items). Add unit tests covering name-only, description-only, system_prompt-only, no-match, empty search, case-insensitivity, and combined with categories/created_by filters.

---

## 2. Codebase Findings

### Existing Implementations

**Template search logic:**
- `/home/psyche/usr/codemie-repos/codemie/src/codemie/rest_api/routers/assistant.py` lines 171–191: `_apply_template_filters()` function
  - Current behavior: filters by `name` (case-insensitive substring only), then by `categories`, then by `created_by`
  - Filter parsing: JSON payload from query string; JSONDecodeError handled gracefully
  - Search pattern: `name_filter.lower() in t.name.lower()`

**Template data model:**
- `/home/psyche/usr/codemie-repos/codemie/src/codemie/rest_api/models/assistant.py` line 608+: `AssistantBase` class
  - Fields: `name` (str), `description` (str, required), `system_prompt` (str, required), `created_by` (Optional[CreatedByUser]), `categories` (list)
  - All three text fields exist; description and system_prompt are required but not currently used in search

**Template data source:**
- `/home/psyche/usr/codemie-repos/codemie/src/codemie/rest_api/models/prebuilt_assistants.py` line 241+: `PrebuiltAssistant` class and `prebuilt_assistants()` method
  - ~27 hardcoded templates for regular users, ~33 for admin (6 extra appended conditionally)
  - In-memory immutable collection; no database persistence
  - Returns list of `PrebuiltAssistant` instances

**Template endpoint:**
- `/home/psyche/usr/codemie-repos/codemie/src/codemie/rest_api/routers/assistant.py` lines 194–244: `index_assistants()` FastAPI route
  - Entry point for template listing when scope=TEMPLATES
  - Calls `_apply_template_filters()` internally
  - Returns JSON response with filtered template list

### Architecture and Layers Affected

**REST API Layer:**
- `src/codemie/rest_api/routers/assistant.py` — route handler; filter application
- Query parameter parsing: JSON filter object from query string

**Data Models Layer:**
- `src/codemie/rest_api/models/assistant.py` — `AssistantBase` validation; required fields include description and system_prompt
- `src/codemie/rest_api/models/prebuilt_assistants.py` — template instantiation and enumeration

**Service Layer (minimal):**
- No intermediate service; templates are in-memory collections accessed directly from router

### Integration Points

**Cross-asset precedent:**
- Template search is isolated to the assistant domain; no search infrastructure shared with datasources, workflows, or other assets
- Filter pattern (sequential name → categories → created_by) is local to this function
- Future asset-level search may benefit from a unified filter abstraction, but this task is template-specific

**External dependencies:**
- `fastapi` 0.133.0 — route handling
- `pydantic` v2 — model validation and serialization
- `python` 3.12+ — string operations

### Patterns and Conventions

**Case-insensitive substring matching:**
- Existing pattern for name field (line 178): `name_filter.lower() in t.name.lower()`
- Must be applied consistently to description and system_prompt fields

**Sequential filter chain:**
- Filter applied iteratively: name → categories → created_by
- Each step reduces the result set; no short-circuit optimization (acceptable for ~35 items)

**None-safe filtering:**
- `created_by` is Optional; current code checks presence before filtering
- description and system_prompt are required, so no None checks needed

**JSON filter parsing:**
- Filters come as JSON string in query parameter
- JSONDecodeError caught and logged; defaults to empty filter dict
- Pattern: `json.loads(request.query_params.get("filters", "{}"))`

---

## 3. Documentation Findings

### Guides and Architecture Docs

Guides found and relevant:
- `.ai-run/guides/api/rest-api-patterns.md` — FastAPI router patterns and conventions
- `.ai-run/guides/testing/testing-api-patterns.md` — API endpoint test patterns
- `.ai-run/guides/testing/testing-patterns.md` — pytest patterns and test structure

### Architectural Decisions

**In-memory template storage:**
- Templates are hardcoded in `prebuilt_assistants()` method; no database persistence
- Conditional append for admin users indicates role-based template visibility logic
- Decision to keep templates in memory reflects their static nature (changed via code, not API)

**Filter composition:**
- Sequential filtering allows independent filter dimensions to compose without interaction
- No boolean operators (AND/OR) between filter types; each filter is applied in sequence

### Derived Conventions

**Naming and field access:**
- Template objects use lowercase underscores for field names (name, description, system_prompt, created_by)
- Filter objects in JSON use consistent naming

**Error handling:**
- JSONDecodeError is logged but doesn't stop request; defaults to empty filter
- Graceful degradation: malformed filter → no filter applied

---

## 4. Testing Landscape

### Existing Coverage

**Template model tests:**
- `/home/psyche/usr/codemie-repos/codemie/tests/codemie/rest_api/models/test_prebuilt_assistants.py` — validates template counts (27 regular, 33 admin) and basic attributes
  - Does not cover filtering logic

**Assistant endpoint tests:**
- `/home/psyche/usr/codemie-repos/codemie/tests/codemie/rest_api/routers/test_assistant.py` — tests for guardrails, chat flow, and other assistant endpoints
  - Does not cover `_apply_template_filters()` function specifically
  - Does not cover `index_assistants()` endpoint with various filter combinations

### Testing Framework and Patterns

**Framework:**
- `pytest` 8.3.1 with `pytest-asyncio`, `pytest-mock`, `pytest-cov`
- Test discovery: `testpaths=tests, pythonpath=src` (pytest.ini)
- Python 3.12+

**Patterns observed:**
- Fixtures for common test data (templates, users, etc.)
- Async test functions with `@pytest.mark.asyncio` decorator
- Mock request objects for route testing (via `pytest-mock`)
- Assertions on response status codes, JSON payloads, and data structures

### Coverage Gaps

**Critical gap: No unit tests for `_apply_template_filters()` function**
- Function is tested indirectly via endpoint tests only
- No isolated unit tests covering:
  - Name-only filtering
  - Description-only filtering
  - system_prompt-only filtering
  - Combined field filtering (name + description, etc.)
  - Empty search string
  - Case-insensitivity edge cases
  - Interaction with categories and created_by filters

**Test file location:**
- New tests should be added to `/home/psyche/usr/codemie-repos/codemie/tests/codemie/rest_api/routers/test_assistant.py` (existing file) or a new dedicated test file for filter logic

---

## 5. Configuration and Environment

### Environment Variables

No environment variables control template search behavior. All configuration is code-level:
- Template list is hardcoded
- Filter logic is hardcoded
- No feature flags for search fields

### Configuration Files

**Relevant config files:**
- `pyproject.toml` — pytest configuration (testpaths, pythonpath)
- `pytest.ini` — pytest settings
- `.env.example` — not used for template search

### Feature Flags and Deployment Concerns

No feature flags currently guard template search. Deployment is straightforward:
- Code change in `_apply_template_filters()` is backward compatible (new filters are additive)
- No database migrations required (templates remain in-memory)
- No secrets or credentials involved
- Search is read-only; no state mutation

---

## 6. Risk Indicators

- **No unit test coverage for `_apply_template_filters()` function** — testing must include comprehensive unit tests for all field combinations, not just integration tests
- **description and system_prompt fields currently unused in search** — task will activate these; no risk, but confirms this is new functionality
- **system_prompt contains multi-line template code** — substring matching may return many results on common keywords (e.g., "You are"); consider documenting this behavior
- **Filter chain is sequential, not optimized** — acceptable for ~35 items; no performance risk
- **No docstring on `_apply_template_filters()` function** — should add documentation explaining filter behavior and field semantics
- **Conditional template append for admin users** — ensure filter logic respects role-based visibility (templates should already be filtered before `_apply_template_filters()` is called)
- **No semantic/ML-based search infrastructure** — substring matching only; no indexing or ranking

---

## 7. Summary for Complexity Assessment

The task extends an existing, well-scoped string filter function in the REST API layer. The change is isolated to a single function with minimal dependencies. **Architectural layer:** REST API and data models (no service, no database, no external integrations). **File change surface:** primary file is `src/codemie/rest_api/routers/assistant.py` (lines 171–191); secondary files are test files (`test_assistant.py` or new test file). **Technical novelty:** None — substring matching is already established pattern; task is to apply it to two additional fields. **Test coverage posture:** Existing template code is lightly tested (model-level only); extension must include comprehensive unit tests for the filter function to close coverage gap. **Key risks:** (1) No existing unit tests for the function; remediation is straightforward (write targeted unit tests covering all field combinations, empty search, case-insensitivity). (2) system_prompt field contains code; substring matching on keywords like "You are" may over-match; acceptable for in-memory ~35-item collection; document in test expectations.

---
