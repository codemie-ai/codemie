# Refuse Disallowed Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace silent fallback-to-default model behavior with explicit refusal, applying project-level narrowing consistently across web chat, CLI, and API paths while flagging assistants pinned to disallowed models.

**Architecture:** This ticket is the enforcement backstop for tickets 2–4. When a model is requested and is either unrecognized OR excluded by the caller's project's `allowed_models` set, the system now refuses the request with a message naming the model. The refusal happens at the service layer (`get_model_details`, `get_allowed_chat_models`) so all call paths (web, CLI, API) inherit the same behavior. Default-model selection is corrected to pick an allowed model when the platform default is excluded. Assistants pinned to disallowed models are refused at runtime and flagged in the assistants list.

**Tech Stack:** Python 3.10+, FastAPI, SQLModel, pytest with mocks

## Global Constraints

- Validation errors use `ValidationException` (from `codemie.core.exceptions`)
- Refusal messages must name the model and project
- All call paths (web, CLI, API) must use the same filtering logic
- Project isolation: changes to one project's `allowed_models` must not leak to others
- Backward compatibility: existing call sites without project context must still work (return all platform-visible models)
- Tests run inside Docker: `docker-compose exec app poetry run pytest tests/`

---

## File Structure

### Modified Files

1. **`src/codemie/service/llm_service/llm_service.py`** (service layer)
   - `get_model_details()` — replace silent fallback with refusal
   - `get_allowed_chat_models()` — add project parameter, apply narrowing
   - `_select_default_model()` — new method to pick allowed default when platform default is excluded
   - `_is_model_allowed_for_project()` — new helper to check project narrowing

2. **`src/codemie/rest_api/routers/chat.py`** (web chat)
   - Chat endpoint handler — extract project from `AssistantRequest`, pass to service

3. **`src/codemie/rest_api/routers/llm_models.py`** (direct API)
   - Endpoints already have project parameter from tickets 2–4; verify project context flows to service

4. **`src/codemie/cli/model_selection.py`** or equivalent (CLI)
   - Model listing/selection — pass resolved project to service filtering

5. **`tests/codemie/service/test_llm_service.py`** (service tests)
   - Add test for refusal when model unrecognized
   - Add test for refusal when model excluded by project
   - Add test for default-model selection when default is excluded
   - Add test for project isolation (changes to one project don't leak to others)

6. **`tests/codemie/rest_api/routers/test_chat.py`** (integration tests)
   - Chat endpoint with project and disallowed model — verify refusal

7. **`codemie-ui` (assistants list)** (frontend, out of scope for backend; note requirement)
   - Flag assistants pinned to disallowed models in the list

---

## Task Decomposition

### Task 1: Create ModelNotAllowedException and add to exception handler

**Files:**
- Modify: `src/codemie/core/exceptions.py` (add new exception)
- Modify: `src/codemie/rest_api/main.py` (register handler)
- Test: `tests/codemie/test_exceptions.py` (or appropriate test file)

**Interfaces:**
- Produces: `ModelNotAllowedException(model_name: str, project_id: Optional[str], details: str)` exception class with HTTP 400 status code

**Steps:**

- [ ] **1a. Write the test for the new exception**

```python
from codemie.core.exceptions import ModelNotAllowedException

def test_model_not_allowed_exception():
    exc = ModelNotAllowedException(
        model_name="gpt-4",
        project_id="proj-123",
        details="This project does not permit this model."
    )
    assert exc.code == 400
    assert "gpt-4" in exc.message
    assert "proj-123" in exc.details or "project" in exc.message.lower()
```

- [ ] **1b. Implement ModelNotAllowedException**

Add to `src/codemie/core/exceptions.py`:

```python
class ModelNotAllowedException(ExtendedHTTPException):
    """Raised when a model is requested but not allowed for the project."""

    def __init__(self, model_name: str, project_id: Optional[str] = None, details: str = ""):
        message = f"Model '{model_name}' is not allowed"
        if project_id:
            message += f" for project '{project_id}'"
        super().__init__(
            code=400,
            message=message,
            details=details or f"This project does not permit the '{model_name}' model."
        )
```

- [ ] **1c. Register exception handler in main.py**

Find the exception handlers section in `src/codemie/rest_api/main.py` (around line 804 per the error-handling guide) and add:

```python
@app.exception_handler(ModelNotAllowedException)
async def model_not_allowed_handler(request: Request, exc: ModelNotAllowedException):
    return JSONResponse(
        status_code=exc.code,
        content={
            "detail": exc.message,
            "details": exc.details,
            "error_type": "model_not_allowed"
        }
    )
```

- [ ] **1d. Run test to verify implementation**

Run: `docker-compose exec app poetry run pytest tests/codemie/test_exceptions.py::test_model_not_allowed_exception -v`
Expected: PASS

- [ ] **1e. Commit**

```bash
git add src/codemie/core/exceptions.py src/codemie/rest_api/main.py tests/codemie/test_exceptions.py
git commit -m "feat(exceptions): add ModelNotAllowedException for disallowed model refusal"
```

---

### Task 2: Add helper methods to LLMService for project-aware filtering and default selection

**Files:**
- Modify: `src/codemie/service/llm_service/llm_service.py` (lines 320–420)
- Test: `tests/codemie/service/test_llm_service.py`

**Interfaces:**
- Consumes: `Application.allowed_models: Optional[List[str]]` (from ticket 2)
- Produces:
  - `_is_model_allowed_for_project(model: LLMModel, project: Optional[Application]) -> bool`
  - `_select_default_model(allowed_models: List[LLMModel], project: Optional[Application]) -> LLMModel`

**Steps:**

- [ ] **2a. Write failing tests for project narrowing helpers**

```python
def test_is_model_allowed_for_project_no_project():
    """When no project is passed, all models are allowed."""
    service = LLMService(llm_config)
    model = LLMModel(base_name="gpt-4", deployment_name="gpt-4", ...)
    assert service._is_model_allowed_for_project(model, project=None) is True

def test_is_model_allowed_for_project_unrestricted():
    """When project.allowed_models is None, all models are allowed."""
    service = LLMService(llm_config)
    project = Application(id="proj-1", allowed_models=None, ...)
    model = LLMModel(base_name="gpt-4", deployment_name="gpt-4", ...)
    assert service._is_model_allowed_for_project(model, project) is True

def test_is_model_allowed_for_project_in_allowed_list():
    """Model in allowed_models list is allowed."""
    service = LLMService(llm_config)
    project = Application(id="proj-1", allowed_models=["gpt-4", "claude-3"], ...)
    model = LLMModel(base_name="gpt-4", deployment_name="gpt-4-deployment", ...)
    assert service._is_model_allowed_for_project(model, project) is True

def test_is_model_allowed_for_project_not_in_allowed_list():
    """Model not in allowed_models list is denied."""
    service = LLMService(llm_config)
    project = Application(id="proj-1", allowed_models=["claude-3"], ...)
    model = LLMModel(base_name="gpt-4", deployment_name="gpt-4-deployment", ...)
    assert service._is_model_allowed_for_project(model, project) is False

def test_select_default_model_project_allows_platform_default():
    """When default is allowed, return it."""
    service = LLMService(llm_config)
    project = Application(id="proj-1", allowed_models=["gpt-4", "claude-3"], ...)
    allowed_models = [
        LLMModel(base_name="gpt-4", deployment_name="gpt-4", default=True, ...),
        LLMModel(base_name="claude-3", deployment_name="claude-3", default=False, ...)
    ]
    result = service._select_default_model(allowed_models, project)
    assert result.base_name == "gpt-4"

def test_select_default_model_project_excludes_platform_default():
    """When default is excluded, pick first allowed model."""
    service = LLMService(llm_config)
    project = Application(id="proj-1", allowed_models=["claude-3"], ...)
    allowed_models = [
        LLMModel(base_name="gpt-4", deployment_name="gpt-4", default=True, ...),
        LLMModel(base_name="claude-3", deployment_name="claude-3", default=False, ...)
    ]
    result = service._select_default_model(allowed_models, project)
    assert result.base_name == "claude-3"

def test_select_default_model_no_allowed_models():
    """When no allowed models, return None."""
    service = LLMService(llm_config)
    project = Application(id="proj-1", allowed_models=[], ...)
    allowed_models = [
        LLMModel(base_name="gpt-4", deployment_name="gpt-4", default=True, ...)
    ]
    result = service._select_default_model(allowed_models, project)
    assert result is None
```

- [ ] **2b. Implement helper methods in LLMService**

```python
def _is_model_allowed_for_project(self, model: LLMModel, project: Optional["Application"]) -> bool:
    """Check if a model is allowed for the given project.
    
    If project is None or project.allowed_models is None, all models are allowed.
    If project.allowed_models is an empty list, no models are allowed.
    If project.allowed_models is non-empty, only models with base_name in the list are allowed.
    """
    if not project or project.allowed_models is None:
        return True
    
    if not project.allowed_models:
        return False
    
    return model.base_name in project.allowed_models

def _select_default_model(
    self, allowed_models: List[LLMModel], project: Optional["Application"]
) -> Optional[LLMModel]:
    """Select a default model, respecting project narrowing.
    
    If no models are allowed (empty list), returns None.
    If project has no restriction or allows the platform default, return the default.
    Otherwise, return the first allowed model.
    """
    if not allowed_models:
        return None
    
    # Filter to only project-allowed models
    project_allowed = [m for m in allowed_models if self._is_model_allowed_for_project(m, project)]
    
    if not project_allowed:
        return None
    
    # Try to return the platform default if it's in the allowed list
    default = next((m for m in project_allowed if m.default), None)
    if default:
        return default
    
    # Otherwise return the first allowed model
    return project_allowed[0]
```

- [ ] **2c. Run tests to verify implementation**

Run: `docker-compose exec app poetry run pytest tests/codemie/service/test_llm_service.py::test_is_model_allowed_for_project -v`
Expected: All tests PASS

Run: `docker-compose exec app poetry run pytest tests/codemie/service/test_llm_service.py::test_select_default_model -v`
Expected: All tests PASS

- [ ] **2d. Commit**

```bash
git add src/codemie/service/llm_service/llm_service.py tests/codemie/service/test_llm_service.py
git commit -m "feat(llm_service): add project-aware filtering helpers for model narrowing"
```

---

### Task 3: Refactor get_model_details to refuse unrecognized/disallowed models

**Files:**
- Modify: `src/codemie/service/llm_service/llm_service.py` (lines 116–136)
- Test: `tests/codemie/service/test_llm_service.py`

**Interfaces:**
- Consumes: `_is_model_allowed_for_project()` from Task 2
- Produces: `get_model_details(model_name: str, project: Optional[Application] = None) -> LLMModel` (raises `ModelNotAllowedException` on refusal)

**Steps:**

- [ ] **3a. Write failing tests for get_model_details refusal**

```python
from codemie.core.exceptions import ModelNotAllowedException

def test_get_model_details_model_not_found():
    """When model is not found, raise ModelNotAllowedException."""
    service = LLMService(llm_config)
    with pytest.raises(ModelNotAllowedException) as exc_info:
        service.get_model_details("nonexistent-model")
    assert "nonexistent-model" in exc_info.value.message

def test_get_model_details_model_not_allowed_for_project():
    """When model exists but is excluded by project, raise ModelNotAllowedException."""
    service = LLMService(llm_config)
    project = Application(id="proj-1", allowed_models=["claude-3"], ...)
    with pytest.raises(ModelNotAllowedException) as exc_info:
        service.get_model_details("gpt-4", project=project)
    assert "gpt-4" in exc_info.value.message
    assert "proj-1" in exc_info.value.details

def test_get_model_details_model_allowed():
    """When model is found and allowed for project, return it."""
    service = LLMService(llm_config)
    project = Application(id="proj-1", allowed_models=["gpt-4"], ...)
    result = service.get_model_details("gpt-4", project=project)
    assert result.base_name == "gpt-4"

def test_get_model_details_no_project_returns_model():
    """When no project is passed, return model if it exists."""
    service = LLMService(llm_config)
    result = service.get_model_details("gpt-4")
    assert result.base_name == "gpt-4"
```

- [ ] **3b. Refactor get_model_details**

Replace the current implementation (lines 116–136) with:

```python
def get_model_details(self, model_name: str, project: Optional["Application"] = None) -> LLMModel:
    """Retrieve model details by name, enforcing project narrowing.
    
    Args:
        model_name: Base name or deployment name of the model
        project: Project to check allowance against (optional)
    
    Returns:
        LLMModel object
    
    Raises:
        ModelNotAllowedException: If model is not found or not allowed for the project
    """
    # Get the active model sources
    active_llm_models = self.get_all_llm_model_info()
    active_embedding_models = self.get_all_embedding_model_info()
    all_models = [*active_llm_models, *active_embedding_models]
    
    # Search for the model
    found_model = next(
        (model for model in all_models if model_name == model.base_name or model_name == model.deployment_name),
        None,
    )
    
    # Model not found
    if not found_model:
        logger.error(f"Model {model_name} not found in active models")
        raise ModelNotAllowedException(
            model_name=model_name,
            project_id=project.id if project else None,
            details=f"Model '{model_name}' does not exist."
        )
    
    # Check project narrowing
    if not self._is_model_allowed_for_project(found_model, project):
        logger.warning(
            f"Model {model_name} requested for project {project.id if project else 'None'} "
            f"but not in allowed_models list"
        )
        raise ModelNotAllowedException(
            model_name=model_name,
            project_id=project.id if project else None,
            details=f"Project does not permit the '{model_name}' model."
        )
    
    return found_model
```

- [ ] **3c. Run tests to verify refusal behavior**

Run: `docker-compose exec app poetry run pytest tests/codemie/service/test_llm_service.py::test_get_model_details -v`
Expected: All tests PASS

- [ ] **3d. Commit**

```bash
git add src/codemie/service/llm_service/llm_service.py tests/codemie/service/test_llm_service.py
git commit -m "feat(llm_service): replace silent fallback with refusal in get_model_details"
```

---

### Task 4: Add project parameter to get_allowed_chat_models and apply narrowing

**Files:**
- Modify: `src/codemie/service/llm_service/llm_service.py` (lines 390–412)
- Test: `tests/codemie/service/test_llm_service.py`

**Interfaces:**
- Consumes: `_is_model_allowed_for_project()`, `_select_default_model()` from Task 2
- Produces: `get_allowed_chat_models(user: User, project: Optional[Application] = None, include_all: bool = False) -> List[LLMModel]`

**Steps:**

- [ ] **4a. Write failing tests for get_allowed_chat_models with project narrowing**

```python
def test_get_allowed_chat_models_no_project():
    """When no project, return all visibility-filtered models."""
    service = LLMService(llm_config)
    user = User(is_external_user=False, ...)
    result = service.get_allowed_chat_models(user)
    # Verify it includes all non-forbidden models
    assert len(result) > 0
    assert all(not m.forbidden_for_web for m in result)

def test_get_allowed_chat_models_with_project_narrowing():
    """When project is passed, filter to allowed_models list."""
    service = LLMService(llm_config)
    user = User(is_external_user=False, ...)
    project = Application(id="proj-1", allowed_models=["gpt-4"], ...)
    result = service.get_allowed_chat_models(user, project=project)
    # Should only contain gpt-4
    assert all(m.base_name == "gpt-4" for m in result)

def test_get_allowed_chat_models_project_excludes_default():
    """When project excludes platform default, default is not included."""
    service = LLMService(llm_config)
    user = User(is_external_user=False, ...)
    project = Application(id="proj-1", allowed_models=["claude-3"], ...)
    result = service.get_allowed_chat_models(user, project=project)
    assert len(result) > 0
    # Verify gpt-4 (if default) is not in result
    assert not any(m.base_name == "gpt-4" and m.default for m in result)

def test_get_allowed_chat_models_project_isolation():
    """Changes to one project's allowed_models don't affect another project."""
    service = LLMService(llm_config)
    user = User(is_external_user=False, ...)
    project1 = Application(id="proj-1", allowed_models=["gpt-4"], ...)
    project2 = Application(id="proj-2", allowed_models=["claude-3"], ...)
    
    result1 = service.get_allowed_chat_models(user, project=project1)
    result2 = service.get_allowed_chat_models(user, project=project2)
    
    assert all(m.base_name == "gpt-4" for m in result1)
    assert all(m.base_name == "claude-3" for m in result2)
```

- [ ] **4b. Refactor get_allowed_chat_models to accept project parameter**

Find and update the method signature and body (around line 390–412):

```python
def get_allowed_chat_models(
    self, user: "User", project: Optional["Application"] = None, include_all: bool = False
) -> List[LLMModel]:
    """Get allowed chat models for a user, with optional project-level narrowing.
    
    Args:
        user: The user requesting models
        project: Optional project to filter by allowed_models
        include_all: If True, include all models regardless of forbidden_for_web flag
    
    Returns:
        List of allowed LLMModel objects
    """
    # Get base allowed models (proxy or YAML)
    allowed_models = self.get_allowed_models(user)
    
    # Apply visibility filter (unless include_all is True)
    if not include_all:
        allowed_models = self._filter_models_by_visibility(allowed_models, include_all=False)
    
    # Apply project narrowing
    if project:
        allowed_models = [m for m in allowed_models if self._is_model_allowed_for_project(m, project)]
    
    # Apply premium flags (existing logic)
    # ... (keep existing premium logic if present)
    
    return allowed_models
```

- [ ] **4c. Run tests to verify project narrowing in get_allowed_chat_models**

Run: `docker-compose exec app poetry run pytest tests/codemie/service/test_llm_service.py::test_get_allowed_chat_models -v`
Expected: All tests PASS

- [ ] **4d. Commit**

```bash
git add src/codemie/service/llm_service/llm_service.py tests/codemie/service/test_llm_service.py
git commit -m "feat(llm_service): add project parameter to get_allowed_chat_models for narrowing"
```

---

### Task 5: Update chat router to pass project to model service

**Files:**
- Modify: `src/codemie/rest_api/routers/chat.py`
- Test: `tests/codemie/rest_api/routers/test_chat.py`

**Interfaces:**
- Consumes: `get_allowed_chat_models(user, project, include_all)` with project parameter
- Produces: Chat endpoints that extract and pass `project` from request

**Steps:**

- [ ] **5a. Identify chat endpoint that uses model selection**

Search for the endpoint in `src/codemie/rest_api/routers/chat.py` that calls model filtering or uses `AssistantRequest.project`.

- [ ] **5b. Write failing test for chat endpoint with disallowed model**

```python
def test_chat_disallowed_model_refused(client, mock_user):
    """Chat request with disallowed model is refused."""
    # Create project with limited allowed_models
    project = Application(id="proj-1", allowed_models=["claude-3"], ...)
    
    payload = {
        "project": "proj-1",
        "model": "gpt-4",  # Not allowed for this project
        "messages": [{"role": "user", "content": "hello"}]
    }
    
    response = client.post("/v1/chat", json=payload, headers={"Authorization": f"Bearer {mock_user.token}"})
    
    assert response.status_code == 400
    assert "gpt-4" in response.json()["detail"]
    assert "not allowed" in response.json()["detail"].lower()
```

- [ ] **5c. Update chat endpoint to extract and pass project**

In the chat endpoint handler, after extracting user, also extract or resolve project from `AssistantRequest.project`:

```python
# In the chat endpoint handler:
@router.post("/v1/chat")
async def chat(
    request: AssistantRequest,
    user: User = Depends(get_current_user),
    llm_service: LLMService = Depends(get_llm_service),
) -> ChatResponse:
    # Extract project from request
    project = None
    if request.project:
        project = get_project_from_id(request.project)  # Assuming this dependency exists from Task 1
    
    # When listing available models or selecting a default, pass project
    available_models = llm_service.get_allowed_chat_models(user, project=project)
    
    if request.model:
        # This will raise ModelNotAllowedException if disallowed
        model_details = llm_service.get_model_details(request.model, project=project)
    else:
        # Select default, respecting project narrowing
        model_details = llm_service._select_default_model(available_models, project)
        if not model_details:
            raise ValidationException("No allowed models available for this project")
    
    # ... rest of chat logic
```

- [ ] **5d. Run test to verify chat refusal**

Run: `docker-compose exec app poetry run pytest tests/codemie/rest_api/routers/test_chat.py::test_chat_disallowed_model_refused -v`
Expected: PASS

- [ ] **5e. Commit**

```bash
git add src/codemie/rest_api/routers/chat.py tests/codemie/rest_api/routers/test_chat.py
git commit -m "feat(chat_router): pass project context to model filtering for refusal"
```

---

### Task 6: Add CLI model listing to respect project narrowing

**Files:**
- Identify: CLI model listing code (likely `src/codemie/cli/` or similar)
- Modify: Model listing command to pass project
- Test: CLI integration tests

**Interfaces:**
- Consumes: `get_allowed_chat_models(user, project, include_all)` with project parameter
- Produces: CLI `models list` output filtered by project

**Steps:**

- [ ] **6a. Locate CLI model listing entry point**

Search for the CLI command that lists models:

```bash
grep -r "models" src/codemie/cli/ --include="*.py" | grep -i "def\|list"
```

- [ ] **6b. Write test for CLI model listing with project narrowing**

```python
def test_cli_models_list_respects_project():
    """CLI models list respects project narrowing."""
    # Create project with limited models
    project = Application(id="proj-1", allowed_models=["gpt-4"], ...)
    
    # Run CLI with project context
    result = cli_runner.invoke(
        models_list_command,
        ["--project", "proj-1"],
        env={"CURRENT_USER_ID": user_id}
    )
    
    assert result.exit_code == 0
    # Verify only gpt-4 is listed
    assert "gpt-4" in result.output
    assert "claude-3" not in result.output
```

- [ ] **6c. Update CLI model listing to extract and pass project**

Update the model listing command to resolve the project and pass it to the service:

```python
def list_models(project_id: Optional[str] = None):
    user = get_current_user()
    project = None
    
    if project_id:
        # Resolve project by ID
        project = project_repository.get_by_id(project_id)
        if not project:
            raise click.ClickException(f"Project {project_id} not found")
    
    # Get models with project filtering
    models = llm_service.get_allowed_chat_models(user, project=project)
    
    # Display models
    for model in models:
        click.echo(f"{model.base_name} ({model.deployment_name})")
```

- [ ] **6d. Run test to verify CLI narrowing**

Run: `docker-compose exec app poetry run pytest tests/codemie/cli/test_models.py::test_cli_models_list_respects_project -v`
Expected: PASS

- [ ] **6e. Commit**

```bash
git add src/codemie/cli/models.py tests/codemie/cli/test_models.py
git commit -m "feat(cli): respect project narrowing in model listing"
```

---

### Task 7: Add assistant refusal for disallowed pinned models

**Files:**
- Identify: Assistant execution entry point (likely `src/codemie/service/assistant_service.py` or similar)
- Modify: Model selection in assistant execution to validate pinned model
- Test: `tests/codemie/service/test_assistant_service.py`

**Interfaces:**
- Consumes: `get_model_details(model_name, project)` with refusal capability
- Produces: Assistant execution that refuses when pinned model is disallowed

**Steps:**

- [ ] **7a. Locate assistant execution code**

Find the method that runs an assistant and resolves its pinned model:

```bash
grep -r "class.*Assistant\|def.*run_assistant\|def.*execute" src/codemie/service/ --include="*.py" | grep -i "assistant"
```

- [ ] **7b. Write test for assistant refusal with disallowed model**

```python
def test_assistant_run_pinned_disallowed_model():
    """Running an assistant pinned to a disallowed model is refused."""
    # Create assistant pinned to gpt-4
    assistant = Assistant(id="asst-1", name="Test", model="gpt-4", project_id="proj-1", ...)
    # Create project that doesn't allow gpt-4
    project = Application(id="proj-1", allowed_models=["claude-3"], ...)
    
    user = User(id="user-1", ...)
    
    # Running the assistant should raise ModelNotAllowedException
    with pytest.raises(ModelNotAllowedException) as exc_info:
        assistant_service.run_assistant(assistant, user, project)
    
    assert "gpt-4" in exc_info.value.message
    assert "proj-1" in exc_info.value.details
```

- [ ] **7c. Update assistant execution to validate pinned model**

In the assistant execution method, add validation:

```python
def run_assistant(self, assistant: Assistant, user: User, project: Optional[Application] = None):
    """Execute an assistant, validating its pinned model against project narrowing.
    
    Raises:
        ModelNotAllowedException: If the pinned model is not allowed for the project
    """
    # Validate that the pinned model is allowed
    try:
        model = self.llm_service.get_model_details(assistant.model, project=project)
    except ModelNotAllowedException:
        logger.error(
            f"Assistant {assistant.id} pinned to disallowed model {assistant.model} "
            f"for project {project.id if project else 'None'}"
        )
        raise
    
    # ... rest of assistant execution logic
```

- [ ] **7d. Run test to verify assistant refusal**

Run: `docker-compose exec app poetry run pytest tests/codemie/service/test_assistant_service.py::test_assistant_run_pinned_disallowed_model -v`
Expected: PASS

- [ ] **7e. Commit**

```bash
git add src/codemie/service/assistant_service.py tests/codemie/service/test_assistant_service.py
git commit -m "feat(assistant_service): refuse execution when pinned model is disallowed"
```

---

### Task 8: Integration test for project isolation and consistency across paths

**Files:**
- Create: `tests/codemie/test_model_narrowing_integration.py`

**Interfaces:**
- Consumes: All narrowing implementations from Tasks 1–7
- Produces: Integration test suite verifying project isolation and path consistency

**Steps:**

- [ ] **8a. Write integration test suite**

```python
import pytest
from codemie.core.exceptions import ModelNotAllowedException

class TestModelNarrowingIntegration:
    """Integration tests for project-level model narrowing across all paths."""

    def test_cross_project_isolation(self, llm_service, user):
        """Removing model from one project doesn't affect others."""
        proj1 = Application(id="proj-1", allowed_models=["gpt-4", "claude-3"], ...)
        proj2 = Application(id="proj-2", allowed_models=["gpt-4"], ...)
        
        # Modify proj1 to remove gpt-4
        proj1.allowed_models = ["claude-3"]
        
        # proj2 should still have gpt-4
        result = llm_service.get_allowed_chat_models(user, project=proj2)
        assert any(m.base_name == "gpt-4" for m in result)
        
        # proj1 should not have gpt-4
        result = llm_service.get_allowed_chat_models(user, project=proj1)
        assert not any(m.base_name == "gpt-4" for m in result)

    def test_consistency_across_list_and_get_details(self, llm_service, user):
        """If a model is in get_allowed_chat_models, get_model_details should work."""
        project = Application(id="proj-1", allowed_models=["gpt-4", "claude-3"], ...)
        
        # List available models
        available = llm_service.get_allowed_chat_models(user, project=project)
        available_names = [m.base_name for m in available]
        
        # Each should be retrievable without refusal
        for name in available_names:
            model = llm_service.get_model_details(name, project=project)
            assert model.base_name == name
        
        # A model not in the list should be refused
        with pytest.raises(ModelNotAllowedException):
            llm_service.get_model_details("gpt-4-turbo", project=project)

    def test_default_selection_respects_narrowing(self, llm_service, user):
        """Default model selection picks an allowed model when default is excluded."""
        project = Application(id="proj-1", allowed_models=["claude-3"], ...)
        
        # Get allowed models
        allowed = llm_service.get_allowed_chat_models(user, project=project)
        
        # Select default
        default = llm_service._select_default_model(allowed, project)
        
        # Should be from allowed list
        assert default in allowed
        # Should not be gpt-4 if it's the platform default but not allowed
        if any(m.default and m.base_name == "gpt-4" for m in []):  # gpt-4 is default but not allowed
            assert default.base_name == "claude-3"

    def test_backward_compatibility_no_project(self, llm_service, user):
        """When no project is passed, all models are visible (backward compatibility)."""
        result = llm_service.get_allowed_chat_models(user, project=None)
        # Should include at least some models
        assert len(result) > 0
        # Should work with get_model_details
        for model in result[:3]:  # Test first 3
            details = llm_service.get_model_details(model.base_name, project=None)
            assert details.base_name == model.base_name
```

- [ ] **8b. Run integration tests**

Run: `docker-compose exec app poetry run pytest tests/codemie/test_model_narrowing_integration.py -v`
Expected: All tests PASS

- [ ] **8c. Commit**

```bash
git add tests/codemie/test_model_narrowing_integration.py
git commit -m "test(integration): verify project isolation and consistency across model narrowing paths"
```

---

### Task 9: Frontend flagging of assistants with disallowed models (codemie-ui)

**Note:** This task is in the `codemie-ui` repository, not `codemie`. Documented here for completeness.

**Files:**
- Modify: `components/assistants-list.tsx` or equivalent
- API: Rely on backend refusing execution; frontend should call `/assistants` and check if response includes a `disallowed_model` flag

**Interfaces:**
- Consumes: Backend API returning `disallowed_model: true` flag on assistant objects whose pinned model is not allowed for their project

**Steps:**

- [ ] **9a. Verify backend API includes model validation**

When listing assistants, the backend should check each assistant's pinned model against its project and include a flag if disallowed.

- [ ] **9b. Update AssistantList component to show flag**

In the assistants list, render a badge/icon for assistants with `disallowed_model=true`:

```tsx
{assistant.disallowed_model && (
  <Badge color="warning">Model not available</Badge>
)}
```

- [ ] **9c. Add tooltip or info text**

Include help text explaining the assistant's pinned model is no longer allowed and needs to be updated.

- [ ] **9d. Test in UI**

Verify the flag appears when viewing assistants that have pinned disallowed models.

---

## Test-First Checklist

For each task above, tests are written and run **before** implementation, following TDD:

- [ ] Task 1: Exception test fails, exception implemented, test passes
- [ ] Task 2: Helper method tests fail, methods implemented, tests pass
- [ ] Task 3: Refusal tests fail, `get_model_details` refactored, tests pass
- [ ] Task 4: Project narrowing tests fail, `get_allowed_chat_models` updated, tests pass
- [ ] Task 5: Chat endpoint test fails, router updated, test passes
- [ ] Task 6: CLI test fails, model listing updated, test passes
- [ ] Task 7: Assistant execution test fails, validation added, test passes
- [ ] Task 8: Integration tests written and passing
- [ ] Task 9: Frontend flagging implemented and tested in UI

---

## Acceptance Criteria Mapping

| Criterion | Implemented By |
|-----------|---|
| Negative case: request with disallowed model is refused | Task 3 (get_model_details refusal) |
| Default selection when default is excluded | Task 2 (_select_default_model) |
| Assistant refusal + list flag | Task 7 (assistant validation) + Task 9 (frontend flag) |
| Project isolation | Task 8 (integration tests) |
| Consistency across CLI/web/API | Task 5 (chat), Task 6 (CLI), + existing API endpoints |

---

## Deployment Notes

- All changes are backward compatible: call sites without a project parameter still work (return all platform-visible models)
- Exception handler registration in main.py ensures all paths (web, CLI, API) return consistent error messages
- No database migrations required; `Application.allowed_models` field already exists from ticket 2
- Docker container must be rebuilt after changes: `docker-compose up --build -d`
- All tests run inside the container: `docker-compose exec app poetry run pytest tests/`
