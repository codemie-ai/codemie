# Technical Research

**Task**: llm_service model_filtering project_narrowing
**Generated**: 2026-09-08T00:00:00Z
**Research path**: filesystem + agent research (codegraph unavailable)

---

## 1. Original Context

Non-proxy path: apply project narrowing directly against the deployment's model config. Where LiteLLM is disabled (or member isn't external-with-LiteLLM), CodeMie must apply the project's available-models set itself. The natural insertion point is `src/codemie/service/llm_service/llm_service.py:364-411`, specifically the `_filter_models_by_visibility` and `get_allowed_chat_models` methods. After platform-wide visibility filtering, additionally exclude models the caller's project has ruled out. Apply this for every member-driven call site: web chat (via `assistant.project`), CLI (via resolved project), direct API calls. Must produce the same list a proxy-backed deployment would produce for the same member+project.

---

## 2. Codebase Findings

### Existing Implementations

**LLM Service Layer** (`src/codemie/service/llm_service/llm_service.py`):
- Lines 320–362: `get_allowed_models(user)` — returns `LiteLLMModels` (chat + embedding) based on proxy enabled/user type/LiteLLM integration
  - Non-proxy path (line 334–337): returns all configured models from YAML
  - Internal user path (line 340–343): returns all configured models
  - External user path (line 345–362): delegates to LiteLLM or returns defaults
- Lines 364–388: `_filter_models_by_visibility(models, include_all)` — filters models where `forbidden_for_web=True`
- Lines 390–412: `get_allowed_chat_models(user, include_all)` — orchestrates: calls `get_allowed_models()`, applies visibility filter, applies premium flags
  - **Current limitation**: accepts only `user` parameter; no project context

**Project Model** (`src/codemie/core/models.py` line 397):
- `Application.allowed_models: Optional[list[str]]` — stored as PostgreSQL ARRAY field
- Contains list of model identifiers the project permits; populated by ticket 2 (storage/admin UI)

**API Routers** (`src/codemie/rest_api/routers/llm_models.py`):
- Lines 37–56: `GET /v1/llm_models` endpoint — calls `llm_service.get_allowed_chat_models(user, include_all)` **without passing project context**
- Lines 111–121: `GET /v1/llm_models/default/<category>` — same limitation
- Both routers have access to request context but do not extract or pass project information

**Assistant and Chat Context** (`src/codemie/rest_api/models/assistant.py` line 325):
- `AssistantRequest.project: str` field available in chat endpoints; defaults to DEMO_PROJECT
- Web chat calls can extract project and pass it to model filtering

**Enterprise LiteLLM Integration** (`src/codemie/enterprise/litellm/`):
- `get_user_allowed_models(user_id, user_applications)` — returns LiteLLM-mediated models for external users
- Called from `llm_service.py:348`; integration already handles user-level narrowing
- Project narrowing for proxy path (ticket 3) uses this integration

### Architecture and Layers Affected

| Layer | Files | Role |
|---|---|---|
| **Router/HTTP** | `rest_api/routers/llm_models.py`, `rest_api/routers/chat.py` | Entry points; receive user + project context; must pass both downstream |
| **Service/Business** | `service/llm_service/llm_service.py` | Model filtering logic; currently user-aware, must become project-aware |
| **Enterprise** | `enterprise/litellm/*` | Conditional LiteLLM integration; already handles user-level restrictions |
| **Model/Config** | `core/models.py`, `configs/llm_config.py` | Data structures; project has `allowed_models` field |
| **Repository** | (none for this concern) | Project data persisted in PostgreSQL via SQLModel |

### Integration Points

**Call chain for non-proxy path:**
1. Router receives request (user + optionally project context in body/URL)
2. Router calls `llm_service.get_allowed_chat_models(user, include_all)`
3. Service queries YAML config via `get_all_llm_model_info()` (non-proxy path)
4. Service applies visibility filter (`_filter_models_by_visibility`)
5. Returns filtered list to router

**Project data availability:**
- **Web chat**: Project in `AssistantRequest.project` (passed via body)
- **CLI**: Project resolved from ticket 1 implementation (context-aware)
- **Direct API calls**: Project may be in URL parameter or header (varies by endpoint)

**Model identity matching:**
- Project `allowed_models` stores identifiers (typically `base_name` or model provider IDs)
- `LLMModel` class has attributes: `base_name`, `deployment_name`, `provider_id` (from research)
- Filtering logic must match project-stored IDs against model object attributes

### Patterns and Conventions

**Conditional business logic by config flag:**
- `config.LLM_PROXY_ENABLED` gates entire proxy vs. YAML config path in `get_allowed_models()`
- Pattern: check flag early, branch logic, no intermingling

**User classification patterns:**
- `user.is_external_user` — boolean flag to determine LiteLLM eligibility
- `user.project_names` — list of project memberships (passed to LiteLLM)

**Filtering chains:**
- Applied post-retrieval, not pre-retrieval
- Current chain: (proxy/YAML config) → visibility → premium flags
- Must extend to: (proxy/YAML config) → visibility → **project narrowing** → premium flags

**Service method signature patterns:**
- Methods accept domain objects, not IDs (user `User`, not user_id)
- Optional flags for control flow (`include_all=False` for visibility filtering)
- Return typed results (`LiteLLMModels`, `List[LLMModel]`)

**Dependency injection:**
- Services injected via FastAPI Depends; no constructor DI visible in routers
- `llm_service` instance appears to be singleton or app-scoped

---

## 3. Documentation Findings

### Guides and Architecture Docs

**Available guides:**
- `.ai-run/guides/integration/llm-providers.md` — LiteLLM as enterprise feature; gates with `is_litellm_enabled` config; documents provider-aware patterns
- `.ai-run/guides/architecture/layered-architecture.md` — API → Service → Repository pattern; enterprises features imported via boundaries
- `.ai-run/guides/development/security-patterns.md` — auth/validation patterns; untrusted input handling

**Key architectural principle from guides:**
- "Keep HTTP concerns in routers, business orchestration in services"
- Project narrowing business logic belongs in service layer, not routers
- Router responsibility: extract project context and pass to service

### Architectural Decisions

**LiteLLM integration boundary:**
- Enterprise feature wrapped in `codemie.enterprise.litellm` module
- Non-proxy path uses direct YAML config; both paths should converge on same output for same member+project
- Guides emphasize: "Keep AWS, Azure, GCP, Anthropic, LiteLLM paths pluggable"

**Model filtering chain design:**
- Visibility filtering (`forbidden_for_web`) is platform-level, applied uniformly
- User-level restrictions (LiteLLM integration) applied after visibility
- **Implication**: project narrowing should follow same chain order

### Derived Conventions

**No existing project-aware model filtering:**
- Current code has no method accepting project parameter
- Will require either: new method signature or refactor of existing method
- Backward compatibility concern: existing call sites do not pass project

**Config-driven branching pattern:**
- Used successfully for LiteLLM proxy (`LLM_PROXY_ENABLED`)
- Suggests project narrowing logic can also be conditional if needed

---

## 4. Testing Landscape

### Existing Coverage

**LLM Service tests** (`tests/codemie/service/llm_service/`):
- `test_llm_service_litellm_integration.py` — LiteLLM initialization, fallback behavior when LiteLLM unavailable
- `test_litellm_service.py` — (referenced; full coverage unknown)
- `test_llm_service.py` — model visibility filtering (`_filter_models_by_visibility`), test fixtures at lines 118–150

**Fixture patterns:**
- Mock `LLMService` instances with preset models
- Mock `User` objects with `is_external_user` flag
- Mock LiteLLM integration responses

### Testing Framework and Patterns

**Framework**: pytest with mock/patch
- Fixtures for service initialization
- Parametrized tests for conditional logic (`include_all` flag, visibility states)
- Mock strategies for LiteLLM and config

**Current test scope:**
- Visibility filtering with `include_all` parameter
- External user fallback to default LiteLLM models
- User type branching (external vs. internal)

### Coverage Gaps

**Project-level filtering:**
- No existing tests for project narrowing (feature not yet implemented)
- Test scenarios needed:
  - Non-proxy deployment: member of narrowed project sees only allowed models
  - Non-proxy deployment: member of narrowed project requesting disallowed model gets rejected
  - Project `allowed_models` change reflected in next request without restart
  - Proxy-backed and non-proxy deployments produce same list for same member+project

**Call site coverage:**
- Tests exist for service layer; need integration tests for routers + service
- No tests for web chat project context propagation
- No tests for CLI project context propagation

---

## 5. Configuration and Environment

### Environment Variables

**LLM proxy feature flag:**
- `LLM_PROXY_ENABLED` (boolean; from `codemie.configs.config`) — gates entire proxy vs. YAML config path

**Model configuration:**
- `MODELS_ENV` — README references this; points to YAML model config location
- `config/llms/` directory — YAML model definitions with `forbidden_for_web` flags

### Configuration Files

**YAML model definitions** (`config/llms/litellm_config.yaml`):
- Model entries with attributes: `forbidden_for_web`, provider ID, deployment name
- Lines 128, 139, 169, 180, 192, 354, 365, 377, 388 contain `forbidden_for_web: true` examples (per ticket description)

**Project configuration** (database):
- `Application.allowed_models` field — PostgreSQL ARRAY of model identifiers
- Populated by ticket 2 (storage) and admin UI (ticket 2)
- Retrieved via project repository queries

### Feature Flags and Deployment Concerns

**Conditional behavior:**
- `config.LLM_PROXY_ENABLED=True` → LiteLLM proxy path; models from LiteLLM integration
- `config.LLM_PROXY_ENABLED=False` → YAML config path; models from YAML directly
- Both paths must respect project narrowing after this ticket

**Deployment note:**
- Project `allowed_models` changes take effect on next request (no restart required)
- Test acceptance criterion verifies this: "Given a project's available models change, when a member next lists/chooses a model, then the change is in effect without restart/redeploy"

---

## 6. Risk Indicators

- **No project parameter currently passed to model service**: routers call `get_allowed_chat_models(user)` without project context; requires signature change or new method
- **Three distinct call paths to instrument**: web chat, CLI, direct API — each may have different project context extraction logic
- **Model identity mismatch**: project `allowed_models` IDs must match LLMModel attributes (`base_name`, `deployment_name`); mismatch breaks filtering
- **Backward compatibility**: existing code assumes no project narrowing; refactor must not break call sites that don't provide project context
- **Test coverage gap**: no existing tests for project-level filtering; acceptance criteria require new test scenarios
- **LiteLLM integration coupling**: ticket 3 (proxy path) and this ticket (non-proxy path) must converge on same observable output; coordination needed
- **Project membership assumption**: assumes user requesting models belongs to the project; no explicit validation exists in current code
- **Configuration drift**: `Application.allowed_models` stored in database; if project record is stale or misconfigured, filtering will silently permit/deny wrong models

---

## 7. Summary for Complexity Assessment

**Scope and Surface:**
This task extends the existing model filtering chain in `llm_service.py` (lines 320–412) to add project-level narrowing. The implementation touches three layers: routers (`llm_models.py`, chat endpoint), the service layer (`llm_service.py` methods), and possibly router integration test fixtures. Primary files to modify: `llm_service.py` (add project parameter + filtering logic), `llm_models.py` (pass project context from routers), and call sites in CLI/chat code paths. Estimated file surface: 4–6 core files + 3–4 test files.

**Technical Novelty:**
The filtering logic itself is straightforward — set intersection of `project.allowed_models` against model identifiers. The novelty is in **where** to apply it (after visibility filtering, before premium flags) and **how** to propagate project context through existing call sites without breaking backward compatibility. The primary decision is whether to refactor existing method signatures or create a new project-aware wrapper method. Given the call-site count (2–3 main routers + 2 CLI paths), a refactor with optional project parameter is likely cheaper than multiple wrappers.

**Testing Posture:**
Existing test coverage for model filtering is moderate (visibility filtering tests exist; LiteLLM integration tested). However, **project-level filtering has zero coverage**. Acceptance criteria require three new test scenarios: (1) narrowed project sees correct model list on non-proxy deployment, (2) narrowed project requesting disallowed model is refused, (3) project `allowed_models` change takes effect on next request without restart. These scenarios must be exercised against both proxy and non-proxy configurations (per ticket notes), which implies a shared test fixture approach. Test effort: ~4–6 test methods + shared fixture setup.

**Risk Factors:**
- Model identity matching: project stores identifiers; must verify they align with `LLMModel.base_name` or deployment name — risk of silent filtering failure if mismatch exists
- Call-site coordination: three distinct paths (web, CLI, API) must all pass project context; incomplete instrumentation leaves one path unwittingly narrowed or unnarkowed
- Backward compatibility: routers currently call without project; adding project parameter requires either optional parameter handling or new method — risk of breaking existing integrations if done carelessly
- Ticket interdependency: depends on ticket 2 (storage of `Application.allowed_models`); parallel with ticket 3 (proxy path); convergence testing needed

