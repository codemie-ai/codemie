# AGENTS.md

**Purpose**: AI-optimized execution guide for coding agents working with the Codemie codebase

---

## GUIDE IMPORTS

This document references detailed guides stored in `.codemie/guides/`. Use those guides as the primary source for implementation patterns and repo conventions.

### MANDATORY RULE: Check Guides First

**Before searching the codebase broadly or editing files**: classify the task, load the relevant P0 guides, check the documented patterns, then search code only as needed.

**Why**: the guides capture established architecture, implementation rules, and anti-patterns to avoid.

---

### Guide References by Category

**Agents & Tools**:
- Agent patterns: `.codemie/guides/agents/langchain-agent-patterns.md`
- Tool usage: `.codemie/guides/agents/agent-tools.md`
- Tool overview: `.codemie/guides/agents/tool-overview.md`
- Custom tools: `.codemie/guides/agents/custom-tool-creation.md`

**API Development**:
- REST patterns: `.codemie/guides/api/rest-api-patterns.md`
- Endpoint conventions: `.codemie/guides/api/endpoint-conventions.md`

**Architecture**:
- Layered architecture: `.codemie/guides/architecture/layered-architecture.md`
- Service layer: `.codemie/guides/architecture/service-layer-patterns.md`
- Project structure: `.codemie/guides/architecture/project-structure.md`

**Data & Database**:
- Database patterns: `.codemie/guides/data/database-patterns.md`
- Repository patterns: `.codemie/guides/data/repository-patterns.md`
- Database optimization: `.codemie/guides/data/database-optimization.md`
- Elasticsearch: `.codemie/guides/data/elasticsearch-integration.md`

**Development Practices**:
- Error handling: `.codemie/guides/development/error-handling.md`
- Logging patterns: `.codemie/guides/development/logging-patterns.md`
- Security patterns: `.codemie/guides/development/security-patterns.md`
- Performance: `.codemie/guides/development/performance-patterns.md`
- Configuration: `.codemie/guides/development/configuration-patterns.md`
- Setup guide: `.codemie/guides/development/setup-guide.md`
- Local testing: `.codemie/guides/development/local-testing.md`

**Testing**:
- Testing patterns: `.codemie/guides/testing/testing-patterns.md`
- API testing: `.codemie/guides/testing/testing-api-patterns.md`
- Service testing: `.codemie/guides/testing/testing-service-patterns.md`

**Integrations**:
- Cloud services: `.codemie/guides/integration/cloud-integrations.md`
- LLM providers: `.codemie/guides/integration/llm-providers.md`
- External services overview: `.codemie/guides/integration/external-services.md`
- Confluence: `.codemie/guides/integration/confluence-integration.md`
- Jira: `.codemie/guides/integration/jira-integration.md`
- X-ray: `.codemie/guides/integration/xray-integration.md`
- Google Docs: `.codemie/guides/integration/google-docs-integration.md`
- MCP integration: `.codemie/guides/integration/mcp-integration.md`

**Standards**:
- Code quality: `.codemie/guides/standards/code-quality.md`
- Git workflow: `.codemie/guides/standards/git-workflow.md`

**Workflows**:
- LangGraph workflows: `.codemie/guides/workflows/langgraph-workflows.md`

---

## INSTANT START

### 1. Critical Rules

| Rule | Trigger | Action Required |
|------|---------|-----------------|
| Check Guides First | Any new prompt or task | Always check relevant guides before broad search or edits |
| Testing | User explicitly asks to write, fix, or run tests | Only then work on tests |
| Git Ops | User explicitly asks to commit, push, or create a PR | Only then do git operations |
| VirtualEnv | Any Python or Poetry command | Activate `.venv` first |
| Shell | Any shell command | Use bash/Linux command syntax |

**Recovery**: if Python tooling fails, check the troubleshooting section and verify the virtualenv is active.

### 2. Task Classifier

| Category | Intent | P0 Guide |
|----------|--------|----------|
| Architecture | System structure, file placement | `layered-architecture.md`, `project-structure.md` |
| Agents & Tools | Create or modify agents, add tools | `langchain-agent-patterns.md` |
| API | Create endpoints, schemas, validation | `rest-api-patterns.md` |
| Database | Queries, models, repositories, optimization | `database-patterns.md`, `repository-patterns.md` |
| Search | Elasticsearch, vector search | `elasticsearch-integration.md` |
| Error Handling | Exceptions, failure paths | `error-handling.md` |
| Logging | Add logs, debug flows | `logging-patterns.md` |
| Security | Auth, validation, permissions | `security-patterns.md` |
| Performance | Optimize code, async patterns | `performance-patterns.md` |
| Configuration | Settings, environment variables | `configuration-patterns.md` |
| Testing | Write or fix tests | `testing-patterns.md` |
| Cloud | AWS, Azure, GCP integration | `cloud-integrations.md` |
| LLM | Model configuration, provider selection | `llm-providers.md` |
| Confluence/Jira/X-ray/GDocs | External service indexing or integration | Service-specific integration guides |
| MCP | Model Context Protocol | `mcp-integration.md` |
| Code Quality | Linting, formatting, implementation hygiene | `code-quality.md` |
| Git | Commits, PRs, branch workflow | `git-workflow.md` |
| Workflows | LangGraph workflows | `langgraph-workflows.md` |

All guides live under `.codemie/guides/<category>/`.

**Complexity rule**:
- Simple: 1 file or highly localized change -> direct work after checking guides
- Medium: 2 to 5 files or cross-layer change -> use guides plus targeted code search
- High: 6+ files, architectural change, or unclear ownership -> plan first before editing

### 3. Self-Check Before Starting

- [ ] Checked relevant guides first
- [ ] Identified any critical rules triggered by the task
- [ ] Assessed keywords, affected layers, and complexity
- [ ] Confidence is high enough to proceed safely

If not, load the P0 guide first or clarify with the user.

---

## EXECUTION WORKFLOW

**Standard flow**: parse task -> check confidence -> load relevant guides -> inspect current implementation -> apply documented patterns -> validate changes

**Decision gates**:
- Confidence below threshold -> load P0 guides
- Still unclear -> load adjacent guides or ask the user
- Pattern mismatch -> prefer documented guide over ad hoc local variation unless the codebase has clearly evolved
- Validation fails -> fix before handoff

**Pre-delivery checklist**:
- Meets the requested behavior
- Follows guide-defined patterns
- No hardcoded secrets or unsafe SQL
- Uses API -> Service -> Repository layering where applicable
- Keeps I/O paths async
- Adds or preserves type hints on touched code
- No TODO placeholders
- Relevant linting or tests were run when appropriate

---

## PATTERN QUICK REFERENCE

### Error Handling

| When | Exception | Import From | Related Patterns |
|------|-----------|-------------|------------------|
| Validation failed | `ValidationException` | `codemie.core.exceptions` | Logging, security |
| Resource not found | `NotFoundException` | `codemie.core.exceptions` | API patterns |
| Business rule violation | `CodeMieException` | `codemie.core.exceptions` | Service layer |
| Database failure | `DatabaseException` | `codemie.core.exceptions` | Repository patterns |
| Authentication failure | `AuthenticationException` | `codemie.core.exceptions` | Security |

**Detail**: `.codemie/guides/development/error-handling.md`

### Logging Patterns

| Do | Do Not | Why |
|----|--------|-----|
| Use f-strings with useful context | Use the `extra` parameter casually | Repo guidance favors message-contained context |
| Log enough context to debug | Log secrets, tokens, passwords, or PII | Security risk |
| Use appropriate log level | Flatten all events to one log level | Reduces signal quality |
| Keep one coherent log message per event | Split a single event across many weak logs | Makes debugging harder |

**Detail**: `.codemie/guides/development/logging-patterns.md`

### Architecture Patterns

| Layer | Responsibility | Example Path | Related Guide |
|-------|----------------|--------------|---------------|
| API | Request validation, response shaping, HTTP concerns | `src/codemie/rest_api/` | `.codemie/guides/api/rest-api-patterns.md` |
| Service | Business logic and orchestration | `src/codemie/service/` | `.codemie/guides/architecture/service-layer-patterns.md` |
| Repository | Data access and persistence | `src/codemie/repository/` | `.codemie/guides/data/repository-patterns.md` |

**Flow**: `API -> Service -> Repository`

Do not skip layers unless the guides or existing architecture for that subsystem explicitly require it.

### Security Patterns

| Rule | Implementation | Related Guide |
|------|----------------|---------------|
| No hardcoded credentials | Use environment-based configuration via repo config patterns | `.codemie/guides/development/configuration-patterns.md` |
| Parameterized SQL | Use SQLModel or SQLAlchemy parameter binding | `.codemie/guides/data/database-patterns.md` |
| Input validation | Use Pydantic models for API inputs | `.codemie/guides/api/rest-api-patterns.md` |
| Managed encryption | Use cloud KMS patterns when encryption is needed | `.codemie/guides/integration/cloud-integrations.md` |

**Detail**: `.codemie/guides/development/security-patterns.md`

### Common Pitfalls

| Category | Never Do This | Do This Instead | Guide Reference |
|----------|----------------|-----------------|-----------------|
| Python | Bare `except:` | Catch specific exceptions | `.codemie/guides/development/error-handling.md` |
| Python | Mutable defaults like `x=[]` | Use `None` and initialize inside | `.codemie/guides/standards/code-quality.md` |
| Python | `eval()` or `exec()` on untrusted input | Avoid entirely | `.codemie/guides/development/security-patterns.md` |
| Async | `time.sleep()` in async code | `asyncio.sleep()` | `.codemie/guides/development/performance-patterns.md` |
| Async | Forgetting `await` | Await coroutines explicitly | `.codemie/guides/development/performance-patterns.md` |
| Async | Blocking I/O in async flow | Use async libraries | `.codemie/guides/development/performance-patterns.md` |
| Database | SQL string interpolation | Parameterized queries | `.codemie/guides/data/database-patterns.md` |
| Database | N+1 query patterns | Batch or eager-load deliberately | `.codemie/guides/data/database-optimization.md` |
| Database | Loading unbounded datasets | Paginate or constrain scope | `.codemie/guides/data/database-patterns.md` |
| Logging | Weak context-free log lines | Include operational context in the message | `.codemie/guides/development/logging-patterns.md` |
| Security | Hardcoded secrets | Use environment configuration | `.codemie/guides/development/security-patterns.md` |

---

## DEVELOPMENT COMMANDS

**Critical**: always activate the environment before Python tooling.

**Activate**: `source .venv/bin/activate`

**Install**: `poetry install --sync`

**NLTK data**: `poetry run download_nltk_packages`

**Run app**: `make run`

**Direct API run**: `cd src/ && poetry run uvicorn codemie.rest_api.main:app --reload`

**Docker stack**: `docker compose up --build codemie postgres elasticsearch`

**Lint/format**: `make ruff`

**Tests**: `make test` or targeted `poetry run pytest tests/codemie/service/`

**Coverage**: `make coverage`

**Full verification**: `make verify`

**API docs**: `http://localhost:8080/docs`

**Migrations**: see `src/external/alembic/README.MD`; do not modify migration infrastructure casually.

---

## TROUBLESHOOTING

**`poetry`/`ruff`/`pytest` not found**: activate `.venv`

**`ModuleNotFoundError`**: run `poetry install --sync`

**Import path issues**: verify `which python` points to `.venv/bin/python`

**Ruff failures**: run `make ruff`

**Database or async failures**: load the relevant data or performance guides before changing implementation

**Task feels stuck**: re-check task classification, load P0 guides, then inspect the local pattern again

---

## PROJECT CONTEXT

### Technology Stack

| Component | Tool | Version | Purpose |
|-----------|------|---------|---------|
| Language | Python | 3.12+ | Core application language |
| API Framework | FastAPI | 0.115.0+ | REST API |
| AI Framework | LangChain | 0.3.x | Agent framework |
| AI Workflows | LangGraph | 0.6.7+ | Workflow orchestration |
| Testing | pytest | 8.3.x | Test framework |
| Linting | Ruff | 0.5.4+ | Linting and formatting |
| ORM | SQLModel | Current | Database models and access |
| Database | PostgreSQL | Current | Primary DB with pgvector |
| Search | Elasticsearch | Current | Search and indexing |
| Packages | Poetry | 1.x | Dependency management |
| Migrations | Alembic | Current | Database migrations |

### Core Components

| Component | Path | Purpose | Guide |
|-----------|------|---------|-------|
| REST API | `src/codemie/rest_api/` | FastAPI routers and endpoints | `.codemie/guides/api/rest-api-patterns.md` |
| Agents | `src/codemie/agents/` | LangChain agents | `.codemie/guides/agents/langchain-agent-patterns.md` |
| Workflows | `src/codemie/workflows/` | LangGraph workflows | `.codemie/guides/workflows/langgraph-workflows.md` |
| Tools | `src/codemie/agents/tools/` | Agent tools | `.codemie/guides/agents/agent-tools.md` |
| Services | `src/codemie/service/` | Business logic | `.codemie/guides/architecture/service-layer-patterns.md` |
| Repositories | `src/codemie/repository/` | Data access | `.codemie/guides/data/repository-patterns.md` |
| Shared tools | `src/codemie_tools/` | Shared tool implementations | Relevant guide by subsystem |
| Config | `config/` | Model configs and templates | `.codemie/guides/development/configuration-patterns.md` |
| Tests | `tests/` | Automated tests | `.codemie/guides/testing/testing-patterns.md` |
| Migrations/utilities | `src/external/` | Alembic and external utilities | Data and setup guides |

### Key Integrations

| Integration | Purpose | Guide |
|-------------|---------|-------|
| Elasticsearch | Vector search and indexing | `.codemie/guides/data/elasticsearch-integration.md` |
| PostgreSQL + pgvector | Persistence and embeddings | `.codemie/guides/data/database-patterns.md` |
| Multi-cloud LLM providers | Model access and provider configuration | `.codemie/guides/integration/llm-providers.md` |
| Cloud services | AWS, Azure, GCP service integrations | `.codemie/guides/integration/cloud-integrations.md` |
| MCP servers | Dynamic tool loading and MCP connectivity | `.codemie/guides/integration/mcp-integration.md` |
| External services | Confluence, Jira, X-ray, Google Docs | `.codemie/guides/integration/external-services.md` and service-specific guides |

**Note**: enterprise-specific capabilities may be maintained in separate repositories. Do not assume their code or docs exist in this repo unless referenced explicitly.

### Architecture Overview

**LangGraph workflows**:
- Supervisor-style coordination patterns
- Agent, tool, state, and finalizer nodes
- Memory and summarization patterns
- Sync and async execution modes
- YAML and Jinja2-driven configuration

**LangChain agents**:
- Tool-calling agents with dynamic tool loading
- Context from repository and indexed sources
- Streaming responses and usage monitoring

**Data processing**:
- Datasource ingestion across code and external docs
- Chunking, embeddings, and indexing
- Hybrid retrieval and ranking patterns

See the referenced guides for implementation details.

---

## CODING STANDARDS

**Python 3.12+**:
- Prefer modern syntax such as `match/case`, `str | int`, and current async primitives when appropriate

**Type hints**:
- Required on new or meaningfully changed functions
- Prefer `from __future__ import annotations` and modern union syntax

**Async**:
- Keep I/O flows async end to end
- Use `asyncio.sleep()` instead of `time.sleep()` in async code
- Prefer async context managers and task orchestration patterns when supported by the surrounding code

**Naming and formatting**:
- Modules in `snake_case`
- Classes in `PascalCase`
- Functions and tests in `snake_case`
- Ruff is the formatter and linter; target line length is 120

**License headers**:
- Source files in `src/`, `tests/`, and `scripts/` require Apache 2.0 headers

---

## POLICIES

**Testing**:
- Do not add or run tests unless the user requested testing work or test execution is necessary to validate the requested change safely
- Prefer targeted test runs while iterating

**Git**:
- Do not commit, push, or create PRs unless the user explicitly asks
- Follow branch and commit naming conventions documented by the repo

**VirtualEnv**:
- Activate `.venv` before Python or Poetry commands

**Shell**:
- Use bash/Linux command syntax in documented examples and command guidance

---

## REMEMBER

For every task: check guides first, classify the work, inspect the existing implementation, apply the documented pattern, then validate.

Non-negotiable quality bar:
- No secrets, placeholders, or unsafe shortcuts
- Parameterized SQL and consistent logging
- Respect API -> Service -> Repository boundaries
- Preserve async correctness
- Keep type hints and repo conventions intact

If confidence remains low after checking guides, ask instead of guessing.
