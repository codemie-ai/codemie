# CodeMie Project Structure

## Quick Summary

CodeMie (v0.8.0) is a FastAPI-based AI assistant platform organized by technical responsibility into 11 core modules. Python >=3.12,<3.14 required. Each module has clear purpose: API layer handles HTTP, service layer handles business logic, repository layer handles data access.

**Category**: Architecture
**Complexity**: Medium
**Prerequisites**: Python 3.12+, FastAPI, Poetry

---

## Directory Layout

```
codemie/
├── .codemie/          # CodeMie meta-config (guides, virtual assistants)
├── config/            # Project configuration
│   ├── llms/          # LLM provider configs (AWS, Azure, GCP, DIAL)
│   ├── customer/      # Customer-specific settings
│   ├── datasources/   # Datasource configurations
│   └── templates/     # Assistant & workflow templates
├── src/
│   ├── codemie/       # Main application code
│   │   ├── rest_api/  # FastAPI app, routers (20+ endpoints), middleware
│   │   ├── service/   # Business logic (24K LOC - largest module)
│   │   │   ├── assistant/    # Assistant services
│   │   │   ├── conversation/ # Conversation services
│   │   │   ├── workflow_config/ # Workflow configuration
│   │   │   ├── workflow_execution/ # Workflow execution
│   │   │   ├── index/        # Index management
│   │   │   ├── llm_service/  # LLM service (multi-provider)
│   │   │   ├── mcp/          # Model Context Protocol
│   │   │   ├── monitoring/   # Monitoring services
│   │   │   ├── tools/        # Tool services
│   │   │   ├── settings/     # Settings management
│   │   │   ├── encryption/   # Encryption (AWS/Azure/GCP KMS)
│   │   │   └── ...
│   │   ├── repository/# Data access, cloud storage (AWS/Azure/GCP)
│   │   ├── agents/    # LangChain agents, tools, toolkits
│   │   │   ├── assistant_agent.py # Main assistant agent
│   │   │   ├── callbacks/         # Streaming, monitoring
│   │   │   └── tools/             # Toolkits (cloud, code, kb, plugin)
│   │   ├── workflows/ # LangGraph workflows, nodes, callbacks
│   │   ├── datasource/# Content ingestion (file, code, confluence, jira)
│   │   ├── clients/   # External adapters (Elasticsearch, NATS, Postgres)
│   │   ├── core/      # Shared models, exceptions, constants
│   │   ├── configs/   # Runtime config, logging, LLM config
│   │   ├── templates/ # Prompt templates
│   │   ├── triggers/  # Background triggers, actors, bindings
│   │   └── enterprise/# Enterprise integrations (helper layer)
│   │       ├── __init__.py       # Re-exports from loader
│   │       ├── loader.py         # Import resolution (HAS_* flags)
│   │       └── litellm/          # LiteLLM integration layer
│   │           ├── __init__.py          # Public API
│   │           ├── client.py            # Async client wrapper
│   │           ├── credentials.py       # User credentials lookup
│   │           ├── dependencies.py      # Service lifecycle & helpers
│   │           ├── llm_factory.py       # LLM model creation
│   │           ├── models.py            # Model mapping
│   │           └── proxy_router.py      # FastAPI proxy endpoints
│   └── external/
│       ├── alembic/   # Database migrations (schema versioning)
│       ├── migrations/# Data migrations
│       ├── deployment_scripts/ # Deployment automation
│       └── utility_scripts/    # CLI tools (NLTK setup)
├── tests/             # Mirrors src/codemie/ structure (pytest)
├── deploy-templates/  # Helm charts for K8s deployment
├── docs/              # Additional project documentation
├── pyproject.toml     # Poetry config & dependencies
├── docker-compose.yml # Local development stack
├── Makefile           # Development commands
├── README.md          # Project documentation
├── CLAUDE.md          # Claude Code instructions
└── AGENTS.md          # Agent documentation
```

---

## Module Responsibilities

| Module | Purpose | LOC | Example Import |
|--------|---------|-----|----------------|
| **rest_api** | HTTP layer: routes, middleware, auth | 16.6K | `from codemie.rest_api.routers import assistant` |
| **service** | Business logic: assistant, LLM, indexing | 24.3K | `from codemie.service.assistant_service import AssistantService` |
| **repository** | Data persistence: DB, file storage, cloud | 1.1K | `from codemie.repository.repository_factory import FileRepositoryFactory` |
| **agents** | LangChain agents, tools, smart selection | 5.6K | `from codemie.agents.assistant_agent import AIToolsAgent` |
| **workflows** | LangGraph workflows, state management | 4.2K | `from codemie.workflows.workflow import WorkflowBuilder` |
| **datasource** | Content processors: file, code, confluence | 4.4K | `from codemie.datasource.file import FileProcessor` |
| **clients** | External service adapters | 3.7K | `from codemie.clients.elasticsearch import ElasticSearchClient` |
| **core** | Shared infrastructure: models, exceptions | 4.6K | `from codemie.core.exceptions import ExtendedHTTPException` |
| **configs** | Configuration management, logging | 0.8K | `from codemie.configs import config, logger` |
| **templates** | Prompt & export templates | 2.4K | `from codemie.templates.agents import agent_templates` |
| **triggers** | Background task orchestration | 1.5K | `from codemie.triggers.node_controller import NodeController` |
| **enterprise** | Enterprise integration helpers (optional) | ~1K | `from codemie.enterprise.litellm import check_user_budget` |

**Note**: The `enterprise/` module is an **integration layer** that provides helpers and interfaces for optional enterprise features. The full enterprise implementation (LiteLLM proxy, budget management, observability) is maintained in the separate `codemie-enterprise` repository/package.

---

## Entry Points

### API Server (main.py)

```python
# src/codemie/rest_api/main.py
from fastapi import FastAPI
from codemie.rest_api.routers import assistant, workflow, conversation

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: DB migrate → migrations → default apps → provision assistants
    await run_alembic_migrations()
    await migrate_all()  # Elastic→Postgres
    await create_default_applications()
    await manage_preconfigured_assistants()
    yield
    # Shutdown: cleanup

app = FastAPI(lifespan=lifespan)
app.include_router(assistant.router)  # 20+ routers total
app.add_middleware(CORSMiddleware, ...)
app.add_exception_handler(ExtendedHTTPException, ...)
```

**Development Setup**:
```bash
poetry install  # Install dependencies
poetry run download_nltk_packages  # Download NLTK data
cd src/
poetry run uvicorn codemie.rest_api.main:app --host=0.0.0.0 --port=8080 --reload
```

**CLI Tools**:
```bash
poetry run download_nltk_packages  # Setup NLTK (src/external/utility_scripts/setup_nltk.py)
python src/external/migrations/migrate_to_postgres.py  # Data migrations
make test         # Run pytest tests
make ruff         # Lint & format with Ruff
make coverage     # Coverage report
```

Source: `pyproject.toml` (scripts), `Makefile` (commands)

---

## Configuration Architecture

### Environment-Based Config

```python
# src/codemie/configs/config.py
from pydantic_settings import BaseSettings

class Config(BaseSettings):
    DATABASE_URL: str
    ELASTICSEARCH_URL: str
    AWS_S3_BUCKET_NAME: Optional[str]

    class Config:
        env_file = ".env"  # Auto-load from .env
        case_sensitive = False

config = Config()  # Singleton instance
```

### Config Files (`config/` directory)

```
config/
├── llms/               # LLM provider configs (Anthropic, OpenAI, AWS, GCP)
├── customer/           # Customer-specific settings
├── datasources/        # Data source configurations
└── templates/          # Template configurations
```

Usage: `from codemie.configs import config` → `config.DATABASE_URL`

---

## Module Organization Patterns

### Import Hierarchy

```python
# ✅ CORRECT: Top-level package imports
from codemie.service.assistant_service import AssistantService
from codemie.rest_api.models.assistant import Assistant
from codemie.core.exceptions import ExtendedHTTPException

# ✅ CORRECT: Shared infrastructure from core
from codemie.core.models import BaseResponse
from codemie.core.constants import APP_DESCRIPTION

# ❌ WRONG: Cross-layer violations
from codemie.rest_api.routers.assistant import router  # Don't import router internals
from codemie.service.assistant.internal_helper import _private_func  # Don't import privates
```

### Module Boundaries

- **API ↔ Service**: Routers call service methods, pass Pydantic models
- **Service ↔ Repository**: Services call repository methods for data access
- **Core → All**: All modules can import from `core/` (shared infrastructure)
- **No circular imports**: Use dependency injection, avoid circular references

### Enterprise Integration Pattern

The `enterprise/` module provides an **integration layer** for optional enterprise features:

```python
# ✅ CORRECT: Use integration layer helpers
from codemie.enterprise.litellm import (
    is_litellm_enabled,          # Check if enterprise features available
    check_user_budget,            # Budget checking helper
    get_available_models,         # Model availability helper
    create_litellm_chat_model     # LLM factory helper
)

# Integration layer handles:
# - Optional dependency loading (HAS_LITELLM flag)
# - Graceful degradation when enterprise package unavailable
# - Unified interface for enterprise features

# ❌ WRONG: Direct import from enterprise package
from codemie_enterprise.litellm.service import LiteLLMService  # Don't do this
```

**Architecture**:
- **Core codebase**: `src/codemie/enterprise/` (integration helpers, always present)
- **Enterprise package**: `codemie-enterprise` (full implementation, optional dependency)
- **Pattern**: Integration layer checks if enterprise package installed, provides fallbacks

**When Enterprise Features Are Unavailable**:
- Core functionality works without enterprise package
- Integration layer returns `None` or default values
- No errors thrown when enterprise features not installed

See: [LLM Provider Integration Guide](../integration/llm-providers.md) for detailed patterns

---

## Anti-Patterns

### ❌ Circular Imports

```python
# DON'T: Create circular dependencies
# service/assistant_service.py
from codemie.rest_api.models.assistant import Assistant  # OK
from codemie.rest_api.routers.assistant import router     # WRONG - circular

# Solution: Move shared models to core/ or use TYPE_CHECKING
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from codemie.rest_api.models.assistant import Assistant
```

### ❌ Unclear Boundaries

```python
# DON'T: Mix layer responsibilities
# rest_api/routers/assistant.py
@router.post("/assistant/chat")
async def chat(request: ChatRequest):
    # WRONG: Business logic in router
    result = llm.invoke(request.prompt)
    db.save(result)
    return result

# DO: Delegate to service layer
@router.post("/assistant/chat")
async def chat(request: ChatRequest):
    return await AssistantService.chat(request)  # Service handles logic
```

### ❌ Direct DB Access from API

```python
# DON'T: Query database directly from routers
# rest_api/routers/assistant.py
@router.get("/assistants")
async def get_assistants():
    return session.query(Assistant).all()  # WRONG

# DO: Use service → repository pattern
@router.get("/assistants")
async def get_assistants():
    return AssistantService.list_assistants()  # Service→Repo
```

---

## When to Use

### Use This Structure When

- [ ] Building API-driven applications with clear layer separation
- [ ] Need to support multiple storage backends (filesystem, AWS, Azure, GCP)
- [ ] Require external integrations (LLM providers, search, messaging)
- [ ] Want testable, maintainable code with clear responsibilities

### Consider Alternatives When

- [ ] Building simple scripts or CLI tools (structure may be overkill)
- [ ] Need monolithic architecture (this is layered/modular)

---

## References

- **Source**: `src/codemie/`
- **Analysis**: `local/docs/analysis/project-structure.md`
- **Related**: [layered-architecture.md](layered-architecture.md) (layer responsibilities)
- **Config**: `config/` directory, `src/codemie/configs/config.py`
- **Tests**: `tests/codemie/` (mirrors src structure)
