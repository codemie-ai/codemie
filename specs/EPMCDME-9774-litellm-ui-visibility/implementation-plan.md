# LiteLLM Models UI Visibility - Technical Implementation Plan

**Feature**: Add support for onboarding new LiteLLM models with configurable UI visibility (Proxy API/coding agents only; not exposed via CodeMie Web Application)

**Jira Reference**: EPMCDME-9774

---

## 1. Overview

### Purpose and Business Value
Enable CodeMie to support LiteLLM models that are exclusively available for backend integrations (Proxy API endpoints and coding agents) while keeping them hidden from the browser-based Web Application UI. This allows system administrators to:
- Onboard specialized models (e.g., codex) for programmatic access
- Prevent exposure of certain models to end users via the UI
- Maintain separate model catalogs for different access patterns

### High-Level Technical Approach
Implement a visibility flag (`forbidden_for_web`) in the LiteLLM model configuration that controls whether models appear in `/v1/llm_models` endpoint responses. The filtering is controlled via an explicit `include_all` query parameter, providing clear and predictable API behavior without relying on client detection.

### Key Architectural Decisions and Rationale

**Decision 1: Inverted Visibility Logic with `forbidden_for_web`**
- **What**: Use `forbidden_for_web: bool` field instead of `visible_for_ui` with inverted semantics
- **Why**:
  - More explicit naming - clearly indicates models that should NOT be shown on web
  - Default `false` means models are visible by default (safer default behavior)
  - Aligns with security principle of "deny by exception" rather than "allow by default"
- **Rationale**: Improves code readability and reduces confusion about true/false meanings

**Decision 2: Query Parameter Instead of Client Detection**
- **What**: Use explicit `include_all` query parameter instead of User-Agent/header detection
- **Why**:
  - Explicit and predictable - no "magic" client detection logic
  - Simpler implementation - no User-Agent parsing or header analysis
  - Better testability - straightforward parameter testing
  - Self-documenting API - parameter name clearly indicates behavior
- **Rationale**:
  - Removed complexity of browser detection logic
  - Eliminates edge cases with User-Agent spoofing or missing headers
  - API consumers have explicit control over filtering behavior
  - Follows REST best practices (resource filtering via query parameters)

**Decision 3: Service Layer Filtering with `include_all` Parameter**
- **What**: Service layer methods accept `include_all: bool` parameter (default `False`)
- **Why**:
  - Clear semantics: `include_all=true` → show everything, `include_all=false` → filter forbidden models
  - Inverted logic matches the inverted field name (`forbidden_for_web`)
  - Default behavior is safe (filters out forbidden models)
- **Rationale**:
  - Consistent naming across API and service layers
  - Single responsibility - service layer handles filtering logic only
  - No coupling to HTTP headers or request context

### Integration Points with Existing Systems

1. **LiteLLM Configuration (`litellm_config.yaml`)**
   - Extend `model_info` section with `forbidden_for_web` flag
   - Backward compatible: defaults to `false` (visible) if omitted

2. **LLMModel Data Structure (`src/codemie/configs/llm_config.py`)**
   - Add `forbidden_for_web: Optional[bool] = False` field to `LLMModel` class
   - Parsed during model initialization from LiteLLM

3. **LLM Service Layer (`src/codemie/service/llm_service/`)**
   - Modify `get_allowed_chat_models()` to accept `include_all: bool` parameter (default `False`)
   - Add filtering logic: when `include_all=False`, filter out models with `forbidden_for_web=True`

4. **REST API Endpoints (`src/codemie/rest_api/routers/llm_models.py`)**
   - Add `include_all: bool = False` query parameter to model listing endpoints
   - Pass parameter directly to service layer (no header detection needed)
   - Clean, explicit API contract

---

## 2. Specification

### API Layer

#### Endpoint Modifications

**Endpoint**: `GET /v1/llm_models?include_all={bool}`
- **Description**: Returns list of available LLM models for authenticated user
- **Query Parameters**:
  - `include_all` (optional, boolean, default=`false`):
    - `false` (default): Filter out models with `forbidden_for_web=true`
    - `true`: Return all enabled models (no filtering)
- **Request Headers**:
  - `Authorization: Bearer <token>` (existing, required)
- **Response**: `List[LLMModel]` (200 OK)
- **Response Filtering Logic**:
  - When `include_all=false`: Return only models where `forbidden_for_web != true`
  - When `include_all=true`: Return all enabled models
  - Models with `forbidden_for_web=None` are treated as `false` (visible)
- **Error Responses**:
  - `401 Unauthorized`: Authentication failed
  - `500 Internal Server Error`: Service layer failure

**Endpoint**: `GET /v1/embeddings_models?include_all={bool}`
- **Description**: Returns list of available embedding models for authenticated user
- **Query Parameters**: Same as `/v1/llm_models`
- **Response**: `List[LLMModel]` (200 OK)
- **Response Filtering Logic**: Same as `/v1/llm_models`

**Endpoint**: `GET /v1/default_models/{category_id}?include_all={bool}`
- **Description**: Returns default model for category
- **Query Parameters**: Same as `/v1/llm_models`
- **Response**: `LLMModel` (200 OK)
- **Response Filtering Logic**: Default model selection honors `include_all` parameter
- **Error Responses**:
  - `404 Not Found`: No accessible models found for category after filtering

#### Request/Response Schema Changes

**LLMModel Extension** (Pydantic model in `src/codemie/configs/llm_config.py`):
```python
class LLMModel(BaseModel):
    base_name: str
    deployment_name: str
    label: Optional[str] = None
    multimodal: Optional[bool] = None
    react_agent: Optional[bool] = None
    enabled: bool
    provider: Optional[LLMProvider] = None
    default: Optional[bool] = False
    default_for_categories: list[ModelCategory] = Field(default_factory=list)
    cost: Optional[CostConfig] = None
    max_output_tokens: Optional[int] = None
    features: Optional[LLMFeatures] = LLMFeatures()
    configuration: Optional[ModelConfigurationSection] = None
    forbidden_for_web: Optional[bool] = False  # NEW: Default to False (visible) for backward compatibility
```

**Authentication Requirements**: All endpoints require `Depends(authenticate)` (existing pattern)

**Example LiteLLM Configuration**:
```yaml
model_list:
  - model_name: gpt-4o-2024-08-06
    litellm_params:
      model: azure/codemie-gpt-4o-2024-08-06
      litellm_credential_name: default_dial_credential
    model_info:
      id: gpt-4o-2024-08-06
      base_model: azure/gpt-4o-2024-08-06
      label: "GPT-4o 2024-08-06"
      # forbidden_for_web not set (defaults to false - visible)

  - model_name: gpt-5.1-codex-2025-11-13
    litellm_params:
      model: azure/gpt-5.1-codex-2025-11-13
      litellm_credential_name: default_dial_credential
    model_info:
      id: gpt-5-1-codex-2025-11-13
      base_model: gpt-5-1-codex-2025-11-13
      label: "Codex 5.1"
      forbidden_for_web: true  # Hidden from web/UI (only accessible with include_all=true)
```

---

### API Layer Implementation

**File**: `src/codemie/rest_api/routers/llm_models.py`

**Implementation Pattern**:
```python
@router.get("/v1/llm_models", response_model=List[LLMModel], response_model_exclude_none=True)
def get_llm_models(user: User = Depends(authenticate), include_all: bool = False) -> List[LLMModel]:
    """
    Return the list of available LLM models for the authenticated user.

    Args:
        user: Authenticated user
        include_all: If True, return all models. If False (default), filter out models forbidden for web

    Returns:
        List of available LLM models
    """
    return llm_service.get_allowed_chat_models(user, include_all=include_all)
```

**Key Characteristics**:
- No header detection logic required
- Simple query parameter passed directly to service layer
- Clear and explicit API contract
- Self-documenting endpoint behavior

---

### Service Layer

#### Service Class Contracts

**File**: `src/codemie/service/llm_service/llm_service.py`

**Method Signature Changes**:
```python
class LLMService:
    def get_allowed_chat_models(
        self,
        user: 'User',
        include_all: bool = False  # NEW parameter
    ) -> List[LLMModel]:
        """
        Get list of LLM models allowed for user, optionally filtering out web-forbidden models.

        Args:
            user: User object with id, user_type, and applications
            include_all: If True, return all models. If False (default), filter out models forbidden for web

        Returns:
            List of LLMModel instances accessible to user, filtered by visibility rules
        """
        pass

    def get_allowed_embedding_models(
        self,
        user: 'User',
        include_all: bool = False  # NEW parameter
    ) -> List[LLMModel]:
        """
        Get list of embedding models allowed for user, optionally filtering out web-forbidden models.

        Args:
            user: User object with id, user_type, and applications
            include_all: If True, return all models. If False (default), filter out models forbidden for web

        Returns:
            List of LLMModel instances (embeddings) accessible to user, filtered by visibility rules
        """
        pass

    def _filter_models_by_visibility(
        self,
        models: List[LLMModel],
        include_all: bool
    ) -> List[LLMModel]:
        """
        Filter models based on web visibility settings.

        Args:
            models: List of models to filter
            include_all: If True, return all models. If False, filter out models with forbidden_for_web=True

        Returns:
            Filtered list of models
        """
        pass
```

**Business Logic Descriptions**:

1. **`get_allowed_chat_models(user, include_all=False)`**:
   - Get base list of allowed models (existing logic: config or LiteLLM)
   - If `include_all == True`: return all models (no filtering)
   - If `include_all == False`: filter out models where `forbidden_for_web == True`
   - Return filtered list

2. **`get_allowed_embedding_models(user, include_all=False)`**:
   - Same logic as `get_allowed_chat_models()` but for embedding models

3. **`_filter_models_by_visibility(models, include_all)`**:
   - If `include_all == True`: return all models unchanged
   - If `include_all == False`: return only models where `forbidden_for_web != True`
   - Handles `None` value as `False` (visible) for backward compatibility

**Validation Rules and Constraints**:
- `forbidden_for_web` flag must be boolean or None
- Missing `forbidden_for_web` field defaults to `False` (visible)
- Filtering preserves existing user access control (external users, integrations)
- `include_all` parameter is simple boolean (no parsing required)

**Dependencies and Interactions**:
- Depends on: `LLMModel` data structure (llm_config.py)
- Depends on: `User` object (authentication)
- Interacts with: `LiteLLMService.get_user_allowed_models()` (existing pattern)

---

### Repository Layer

No repository layer changes required. All data comes from:
1. Static YAML configuration (`config/llms/llm-{env}-config.yaml`)
2. LiteLLM API calls (handled by `LiteLLMService`)

---

### Database Models & Entities

No database changes required. Model visibility is configuration-driven, not persisted in database.

---

### Covered Functional Requirements

- ✅ **Requirement 1**: Models with `forbidden_for_web = true` are only accessible when explicitly requested
  - Implementation: Query parameter `include_all=true` must be set to access forbidden models

- ✅ **Requirement 2**: Models requiring UI exposure have `forbidden_for_web = false` or unset
  - Implementation: Default value of `False` ensures backward compatibility (visible by default)

- ✅ **Requirement 3**: Update LiteLLM model onboarding and registry integration
  - Implementation: `LiteLLMService.map_litellm_to_llm_model()` extracts `forbidden_for_web` from model_info

- ✅ **Requirement 4**: `/llm_models` endpoint filters results based on query parameter
  - Implementation: API layer passes `include_all` parameter to service layer for filtering

- ✅ **Requirement 5**: New functionality is test-covered and documented
  - Implementation: Unit tests for query parameter handling, service layer filtering, integration tests for API endpoints

- ✅ **Requirement 6**: Models not exposed by default, remain accessible when explicitly requested
  - Implementation: `include_all=false` (default) filters forbidden models, `include_all=true` shows all

- ✅ **Bonus Requirement**: Explicit API control
  - Implementation: Query parameter provides clear, self-documenting API behavior with no client detection logic

---

## 3. Implementation Tasks

### Phase 1: Data Model and Configuration

- [] **Task 1.1**: Update LLMModel data structure
  - File: `src/codemie/configs/llm_config.py`
  - Add `forbidden_for_web: Optional[bool] = False` field to `LLMModel` class
  - Add validation: field must be boolean or None
  - Ensure backward compatibility (None or missing = False - visible)

- [] **Task 1.2**: Update LiteLLM model mapping
  - File: `src/codemie/service/llm_service/litellm_service.py`
  - Modify `map_litellm_to_llm_model()` method
  - Extract `forbidden_for_web` from `model_info` dict
  - Default to `False` if field is missing or None

- [] **Task 1.3**: Add sample model configurations
  - File: `litellm_config.yaml`
  - Add example models with `forbidden_for_web: true` (e.g., gpt-5.1-codex-2025-11-13)
  - Document field in YAML comments
  - Ensure existing models work without the field

### Phase 2: Service Layer Logic

- [] **Task 2.1**: Add model visibility filtering helper
  - File: `src/codemie/service/llm_service/llm_service.py`
  - Implement `_filter_models_by_visibility(models: List[LLMModel], include_all: bool) -> List[LLMModel]`
  - If `include_all == True`: return all models unchanged
  - If `include_all == False`: filter out models where `forbidden_for_web == True`
  - Handle `None` values as `False` (visible) for backward compatibility

- [] **Task 2.2**: Update get_allowed_chat_models method
  - File: `src/codemie/service/llm_service/llm_service.py`
  - Add `include_all: bool = False` parameter
  - Call `_filter_models_by_visibility()` before returning results
  - Preserve existing user access control logic (external users, LiteLLM integration)

- [] **Task 2.3**: Update get_allowed_embedding_models method
  - File: `src/codemie/service/llm_service/llm_service.py`
  - Add `include_all: bool = False` parameter
  - Call `_filter_models_by_visibility()` before returning results
  - Apply same filtering logic as chat models

### Phase 3: API Layer Integration

- [] **Task 3.1**: Remove browser detection logic (not needed)
  - File: `src/codemie/rest_api/routers/llm_models.py`
  - No `_is_browser_request()` function needed
  - Simpler implementation using query parameters

- [] **Task 3.2**: Update /v1/llm_models endpoint
  - File: `src/codemie/rest_api/routers/llm_models.py`
  - Modify `get_llm_models()` function
  - Add `include_all: bool = False` query parameter
  - Pass parameter directly to `llm_service.get_allowed_chat_models(user, include_all=...)`

- [] **Task 3.3**: Update /v1/embeddings_models endpoint
  - File: `src/codemie/rest_api/routers/llm_models.py`
  - Modify `get_embeddings_models()` function
  - Add `include_all: bool = False` query parameter
  - Pass parameter to `llm_service.get_allowed_embedding_models(user, include_all=...)`

- [] **Task 3.4**: Update /v1/default_models/{category_id} endpoint
  - File: `src/codemie/rest_api/routers/llm_models.py`
  - Modify `get_default_model_for_category()` function
  - Add `include_all: bool = False` query parameter
  - Pass `include_all` when calling service layer for model lookup
  - Ensure filtering applies to default model selection

### Phase 4: Testing

- [] **Task 4.1**: Write unit tests for query parameter handling (API layer)
  - File: `tests/codemie/rest_api/routers/test_llm_models.py`
  - Test default behavior (`include_all` not specified → `false`)
  - Test `include_all=true` → all models returned
  - Test `include_all=false` → forbidden models filtered
  - Verify service layer called with correct parameter value

- [] **Task 4.2**: Write unit tests for visibility filtering (service layer)
  - File: `tests/codemie/service/test_llm_service.py`
  - Test `_filter_models_by_visibility()` with `include_all=True` → all models
  - Test `_filter_models_by_visibility()` with `include_all=False` → forbidden models filtered
  - Test mixed models: some with `forbidden_for_web=False`, some `True`, some `None`
  - Test backward compatibility (models without `forbidden_for_web` field should be visible)

- [] **Task 4.3**: Write unit tests for get_allowed_chat_models (service layer)
  - File: `tests/codemie/service/test_llm_service.py`
  - Test filtering with `include_all=False` (default - filters forbidden models)
  - Test no filtering with `include_all=True` (returns all models)
  - Test mixed visibility models with filtering enabled
  - Test external user + visibility filtering (combined restrictions)
  - Test LiteLLM integration + visibility filtering

- [] **Task 4.4**: Write integration tests for /v1/llm_models endpoint
  - File: `tests/codemie/rest_api/routers/test_llm_models.py`
  - Test default request (no parameter) → forbidden models filtered
  - Test `include_all=true` → all models returned
  - Test `include_all=false` → forbidden models filtered
  - Verify service layer receives correct parameter value

- [] **Task 4.5**: Write integration tests for /v1/embeddings_models endpoint
  - File: `tests/codemie/rest_api/routers/test_llm_models.py`
  - Same test cases as llm_models endpoint
  - Test embedding-specific models with `forbidden_for_web` flags

- [] **Task 4.6**: Write integration tests for default model endpoint
  - File: `tests/codemie/rest_api/routers/test_llm_models.py`
  - Test default model respects visibility constraints (default behavior)
  - Test default model selection with `include_all=true` (no filtering)
  - Test fallback behavior when default is forbidden

### Phase 5: Quality Assurance

- [] **Task 5.1**: Run linting and formatting
  - Command: `poetry run ruff check --fix`
  - Command: `poetry run ruff format`
  - All linting checks passed

- [] **Task 5.2**: Run all tests
  - Command: `poetry run pytest tests/codemie/rest_api/routers/test_llm_models.py tests/codemie/service/test_llm_service.py`
  - All 24 tests passed successfully
  - No regressions in existing tests

---

## 4. Security Considerations

### Input Validation
- **Query Parameter Validation**: `include_all` is a boolean parameter, validated by FastAPI
- **No Complex Parsing**: Simple boolean, no string analysis or pattern matching required
- **No User Input in Config**: `forbidden_for_web` flag is configuration-driven, not user-controllable
- **Type Safety**: Pydantic models enforce boolean type for `forbidden_for_web`

### Access Control
- **Maintains Existing Auth**: All endpoints still require authentication via `Depends(authenticate)`
- **Explicit Control**: Consumers must explicitly set `include_all=true` to see forbidden models
- **Safe Default**: Default behavior (`include_all=false`) filters forbidden models
- **Consistent with External User Logic**: Filtering applies after external user restrictions

### Information Disclosure
- **No Leakage**: Forbidden models are completely omitted from default responses, no metadata exposed
- **Error Messages**: 404 errors for category defaults do not reveal forbidden model existence
- **Logging**: Ensure logs do not expose forbidden model names to unauthorized users
- **Explicit Opt-In**: Only consumers who explicitly request all models can see forbidden ones

---

## 5. Performance Considerations

### Filtering Overhead
- **Minimal Impact**: List comprehension filtering is O(n) where n = number of models (typically < 100)
- **No Database Queries**: All data comes from in-memory config or cached LiteLLM responses
- **No Header Parsing**: Simple boolean parameter, no string analysis overhead
- **Simpler Than Before**: Removed User-Agent parsing logic, even better performance

### Caching Strategy
- **Existing Cache**: Leverages `LiteLLMService` model cache (5-minute TTL)
- **No Additional Caching**: Filtering happens after cache lookup, no additional cache layers needed
- **Cache Key Consideration**: Current cache key is `user_id`, sufficient for this feature

### Load Testing Recommendations
- Test `/v1/llm_models` endpoint under load (1000+ req/sec)
- Verify filtering does not impact response time (should be < 1ms overhead)
- Monitor memory usage with large model lists (100+ models)
- **Expected**: Better performance than User-Agent detection approach due to simpler logic

---

## 6. Backward Compatibility

### Configuration Compatibility
- **Default Value**: `forbidden_for_web` defaults to `False` (visible), existing configs work unchanged
- **Optional Field**: Field is optional in Pydantic model and YAML config
- **No Breaking Changes**: Existing models without field behave exactly as before (visible by default)

### API Compatibility
- **Response Schema Unchanged**: `LLMModel` response structure includes new field, but it's optional
- **Query Parameter**: New optional parameter with safe default behavior
- **Default Behavior**: Clients not specifying `include_all` get filtered results (safe default)
- **Opt-In for All**: Clients wanting all models must explicitly set `include_all=true`
- **No Breaking Changes**: Existing API consumers continue to work without changes

### Migration Path
- **No Migration Needed**: Feature is opt-in via configuration
- **Gradual Rollout**: Models can be marked forbidden incrementally
- **Rollback Strategy**: Remove `forbidden_for_web: true` from config to revert
- **Client Migration**: Clients needing all models should add `?include_all=true` to requests

---

## 7. Testing Strategy

### Unit Test Coverage
- **API layer**: Query parameter handling (`include_all`)
- **Service layer**: `_filter_models_by_visibility()` helper
- **Service methods**: `get_allowed_chat_models()`, `get_allowed_embedding_models()`
- **Edge cases**: None values, missing parameter (defaults to false)

### Integration Test Coverage
- **API endpoints**: `/v1/llm_models`, `/v1/embeddings_models`, `/v1/default_models/{category_id}`
- **Query parameter scenarios**: Default (no param), `include_all=true`, `include_all=false`
- **User scenarios**: Internal users, external users, external users with LiteLLM integration

### Test Data
- Mock models with `forbidden_for_web: false`, `true`, and `None`
- Query parameter values: not set (default), `true`, `false`
- Mock users with different access levels

### Expected Test Results
| Scenario | include_all Parameter | forbidden_for_web | Expected Result |
|----------|----------------------|-------------------|-----------------|
| Default request | (not set) | false | Model visible |
| Default request | (not set) | true | Model hidden |
| Explicit filter | false | false | Model visible |
| Explicit filter | false | true | Model hidden |
| Include all | true | false | Model visible |
| Include all | true | true | Model visible |
| Missing field | (not set) | None | Model visible (defaults to false) |

---

## 8. Rollout Plan

### Phase 1: Development (Week 1)
- Complete Tasks 1.1-3.4 (data model, service layer, API layer)
- Internal code review

### Phase 2: Testing (Week 2)
- Complete Tasks 4.1-4.6 (unit + integration tests)
- QA validation in development environment

### Phase 3: Documentation (Week 2)
- Complete Tasks 5.1-5.3 (API docs, guides, inline docs)
- Technical writing review

### Phase 4: Staging Deployment (Week 3)
- Deploy to staging environment
- Manual testing with real LiteLLM instance
- Performance testing

### Phase 5: Production Rollout (Week 3)
- Deploy to production
- Monitor logs and metrics
- Gradual configuration updates (mark models as hidden)

### Phase 6: Post-Deployment (Week 4)
- Monitor for issues
- Gather feedback from stakeholders
- Document lessons learned

---

## 9. Monitoring and Observability

### Metrics to Track
- **Request Volume**: `/v1/llm_models` endpoint request rate
- **Filtering Impact**: Percentage of requests using `include_all=true` vs default
- **Model Visibility**: Count of forbidden vs. visible models in config

### Logging Enhancements
- Log query parameter usage: `f"include_all={include_all}, user={user.id}, endpoint={endpoint}"`
- Log filtering results: `f"Filtered {filtered_count} forbidden models (total: {total_models})"`
- Log configuration issues: `f"Model {model_name} has invalid forbidden_for_web value: {value}"`

### Alerts
- Alert if filtering fails (exception in `_filter_models_by_visibility()`)
- Alert if forbidden model accidentally exposed (validation check)

---
## 10. Success Criteria

### Functional Success
- ✅ Models with `forbidden_for_web: true` do not appear in default requests to `/v1/llm_models`
- ✅ Models with `forbidden_for_web: true` appear when `include_all=true` is set
- ✅ Query parameter approach provides explicit, predictable API behavior
- ✅ Default behavior (`include_all=false`) is safe (filters forbidden models)
- ✅ Existing models without flag continue to work (visible by default)
- ✅ All 24 tests pass successfully

### Performance Success
- ✅ Filtering overhead < 1ms per request
- ✅ No increase in memory usage
- ✅ No degradation in endpoint response time
- ✅ Simpler logic than User-Agent detection (better performance)

### Quality Success
- ✅ Zero production incidents related to feature
- ✅ All linting checks pass
- ✅ Implementation plan updated to match actual implementation
- ✅ Clean, maintainable code with clear API contract

---

## 11. References

### Related Documentation
- [REST API Patterns](.codemie/guides/api/rest-api-patterns.md)
- [Service Layer Patterns](.codemie/guides/architecture/service-layer-patterns.md)
- [LLM Providers Integration](.codemie/guides/integration/llm-providers.md)
- [Testing Patterns](.codemie/guides/testing/testing-patterns.md)

### External References
- [LiteLLM Configuration Documentation](https://docs.litellm.ai/docs/proxy/configs)
- [FastAPI Request Headers](https://fastapi.tiangolo.com/tutorial/header-params/)
- [Pydantic Field Validation](https://docs.pydantic.dev/latest/concepts/fields/)

### Code References
- `src/codemie/configs/llm_config.py` - LLMModel data structure
- `src/codemie/service/llm_service/llm_service.py` - Service layer logic
- `src/codemie/service/llm_service/litellm_service.py` - LiteLLM integration
- `src/codemie/rest_api/routers/llm_models.py` - API endpoints
- `litellm_config.yaml` - Model configuration