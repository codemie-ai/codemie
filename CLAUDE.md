# CLAUDE.md

**Purpose**: AI-optimized execution guide for Claude Code agents working with the Codemie codebase

---

## 📚 GUIDE IMPORTS

This document references detailed guides stored in `.codemie/guides/`. Key references:

### 🚨 MANDATORY RULE: Check Guides First

**BEFORE searching codebase**: Use Task Classifier → Load P0 guides → Check content → Then search if needed

**Why**: Guides contain established patterns, prevent anti-patterns, ensure consistency

---

### 📖 Guide References by Category

**Agents & Tools**:
- Agent patterns: .codemie/guides/agents/langchain-agent-patterns.md
- Tool usage: .codemie/guides/agents/agent-tools.md
- Tool overview: .codemie/guides/agents/tool-overview.md
- Custom tools: .codemie/guides/agents/custom-tool-creation.md

**API Development**:
- REST patterns: .codemie/guides/api/rest-api-patterns.md
- Endpoint conventions: .codemie/guides/api/endpoint-conventions.md

**Architecture**:
- Layered architecture: .codemie/guides/architecture/layered-architecture.md
- Service layer: .codemie/guides/architecture/service-layer-patterns.md
- Project structure: .codemie/guides/architecture/project-structure.md

**Data & Database**:
- Database patterns: .codemie/guides/data/database-patterns.md
- Repository patterns: .codemie/guides/data/repository-patterns.md
- Database optimization: .codemie/guides/data/database-optimization.md
- Elasticsearch: .codemie/guides/data/elasticsearch-integration.md

**Development Practices**:
- Error handling: .codemie/guides/development/error-handling.md
- Logging patterns: .codemie/guides/development/logging-patterns.md
- Security patterns: .codemie/guides/development/security-patterns.md
- Performance: .codemie/guides/development/performance-patterns.md
- Configuration: .codemie/guides/development/configuration-patterns.md
- Setup guide: .codemie/guides/development/setup-guide.md
- Local testing: .codemie/guides/development/local-testing.md

**Testing**:
- Testing patterns: .codemie/guides/testing/testing-patterns.md
- API testing: .codemie/guides/testing/testing-api-patterns.md
- Service testing: .codemie/guides/testing/testing-service-patterns.md

**Integrations**:
- Cloud services: .codemie/guides/integration/cloud-integrations.md
- LLM providers: .codemie/guides/integration/llm-providers.md
- External services overview: .codemie/guides/integration/external-services.md
- Confluence: .codemie/guides/integration/confluence-integration.md
- Jira: .codemie/guides/integration/jira-integration.md
- X-ray: .codemie/guides/integration/xray-integration.md
- Google Docs: .codemie/guides/integration/google-docs-integration.md
- MCP integration: .codemie/guides/integration/mcp-integration.md

**Standards**:
- Code quality: .codemie/guides/standards/code-quality.md
- Git workflow: .codemie/guides/standards/git-workflow.md

**Workflows**:
- LangGraph workflows: .codemie/guides/workflows/langgraph-workflows.md

---

## ⚡ INSTANT START (Read First)

### 1. Critical Rules (MANDATORY - Always Check)

| Rule | Trigger | Action Required |
|------|---------|-----------------|
| 🚨 **Check Guides First** | ANY new prompt/task | ALWAYS check relevant guides BEFORE searching codebase |
| 🚨 **Testing** | User says "write tests", "run tests" | ONLY then work on tests → [Policies](#-policies) |
| 🚨 **Git Ops** | User says "commit", "push", "create PR" | ONLY then do git operations → [Policies](#-policies) |
| 🚨 **VirtualEnv** | ANY Python/Poetry command | ALWAYS activate first → [Policies](#-policies) |
| 🚨 **Shell** | ANY shell command | ONLY bash/Linux syntax → [Policies](#-policies) |

**Emergency Recovery**: If Python commands fail → Check [Troubleshooting](#-troubleshooting-quick-reference)

### 2. Task Classifier (Intent-Based Mapping)

| Category | Intent | P0 Guide |
|----------|--------|----------|
| **Architecture** | System structure, where code belongs | layered-architecture.md, project-structure.md |
| **Agents & Tools** | Create/modify agents, add tools | langchain-agent-patterns.md |
| **API** | Create endpoints, validation | rest-api-patterns.md |
| **Database** | Queries, models, repos, optimization | database-patterns.md, repository-patterns.md |
| **Search** | Elasticsearch, vector search | elasticsearch-integration.md |
| **Error Handling** | Add exceptions, handle errors | error-handling.md |
| **Logging** | Add logging, debug | logging-patterns.md |
| **Security** | Auth, validation, permissions | security-patterns.md |
| **Performance** | Optimize, async patterns | performance-patterns.md |
| **Configuration** | Settings, env vars | configuration-patterns.md |
| **Testing** ⚠️ | Write/fix tests (explicit only) | testing-patterns.md |
| **Cloud** | AWS/Azure/GCP integration | cloud-integrations.md |
| **LLM** | LiteLLM, model config, budgets | llm-providers.md |
| **Confluence/Jira/X-ray/GDocs** | External service indexing | confluence/jira/xray/google-docs-integration.md |
| **MCP** | Model Context Protocol | mcp-integration.md |
| **Code Quality** | Linting, formatting | code-quality.md |
| **Git** ⚠️ | Commits, PRs (explicit only) | git-workflow.md |
| **Workflows** | LangGraph workflows | langgraph-workflows.md |

All guides in `.codemie/guides/<category>/`. **Complexity**: Simple (1 file) → direct tools | Medium (2-5) → + guides | High (6+) → EnterPlanMode

### 3. Self-Check Before Starting

- [ ] Checked relevant guides first?
- [ ] Critical rules identified? (guides/testing/git/venv/shell)
- [ ] Keywords & complexity assessed?
- [ ] Confidence 80%+?

**If NO**: Load P0 guides or ask user. **If YES**: Proceed.

---

## 🔄 EXECUTION WORKFLOW

**Standard Flow**: Parse → Check Confidence → Load Guides (P0 first) → Apply Patterns → Validate

**Key Gates**:
- Confidence < 80% → Load P0 guides
- Still unclear → Load P1 guides or ask user
- Pattern not found → Read full guide
- Any validation fails → Fix before delivery

**Pre-Delivery Checklist**:
- ✅ Meets requirements & follows critical rules
- ✅ No hardcoded secrets, parameterized SQL
- ✅ Uses `core/exceptions.py`, f-string logging
- ✅ Follows API→Service→Repository architecture
- ✅ Async for I/O, type hints on all functions
- ✅ No TODOs, passes `ruff check`

---

## 📊 PATTERN QUICK REFERENCE

### Error Handling (Use These Exceptions)

| When | Exception | Import From | Related Patterns |
|------|-----------|-------------|------------------|
| Validation failed | `ValidationException` | `codemie.core.exceptions` | [Logging](#logging-patterns-mandatory), [Security](#security-patterns-mandatory) |
| Not found | `NotFoundException` | `codemie.core.exceptions` | [API Patterns](see below) |
| Business logic | `CodeMieException` | `codemie.core.exceptions` | [Service Layer](see below) |
| Database error | `DatabaseException` | `codemie.core.exceptions` | [Repository Patterns](see below) |
| Auth failed | `AuthenticationException` | `codemie.core.exceptions` | [Security](#security-patterns-mandatory) |

**Detail**: .codemie/guides/development/error-handling.md
**See Also**: .codemie/guides/api/rest-api-patterns.md

### Logging Patterns (MANDATORY)

| ✅ DO | ❌ DON'T | Why | Related |
|-------|----------|-----|---------|
| F-strings for context | `extra` parameter | `extra` breaks LogRecord | [Error Handling](#error-handling-use-these-exceptions) |
| Include context in message | Log passwords, keys, PII | Security violation | [Security](#security-patterns-mandatory) |
| Appropriate level (debug/info/error) | Mix log levels | Noise in logs | - |
| Structured context in f-strings | Separate log calls for same event | Fragmented logs | - |

**Detail**: .codemie/guides/development/logging-patterns.md
**Examples**: .codemie/guides/development/logging-patterns.md

### Architecture Patterns (Core Rules)

| Layer | Responsibility | Example Path | Related Guide |
|-------|----------------|--------------|---------------|
| **API** | Request/response, validation | `src/codemie/rest_api/routers/` | .codemie/guides/api/rest-api-patterns.md |
| **Service** | Business logic | `src/codemie/service/` | .codemie/guides/architecture/service-layer-patterns.md |
| **Repository** | Database access | `src/codemie/repository/` | .codemie/guides/data/repository-patterns.md |

**Flow**: `API → Service → Repository` (Never skip layers)
**Async**: Use `async/await` for ALL I/O operations (DB, API, files)

**Detail**: .codemie/guides/architecture/layered-architecture.md
**See Also**: .codemie/guides/architecture/project-structure.md

### Security Patterns (MANDATORY)

| Rule | Implementation | Related Guide |
|------|----------------|---------------|
| No hardcoded credentials | Use environment variables via `codemie.configs` | .codemie/guides/development/configuration-patterns.md |
| Parameterized SQL | Use SQLModel/SQLAlchemy params (never f-strings in SQL) | .codemie/guides/data/database-patterns.md |
| Input validation | Pydantic models for ALL API inputs | .codemie/guides/api/rest-api-patterns.md |
| Encryption | Use AWS/Azure/GCP KMS (never custom crypto) | .codemie/guides/integration/cloud-integrations.md |

**Detail**: .codemie/guides/development/security-patterns.md
**See Also**: [Common Pitfalls](#common-pitfalls-avoid-these)

### Common Pitfalls (Avoid These)

| Category | 🚨 Never Do This | ✅ Do This Instead | Guide Reference |
|----------|------------------|---------------------|-----------------|
| **Python** | Bare `except:` | Specific exceptions | .codemie/guides/development/error-handling.md |
| **Python** | Mutable defaults (`def f(x=[])`) | Use `None`, init in body | .codemie/guides/standards/code-quality.md |
| **Python** | `eval()`/`exec()` with user input | Never use with untrusted data | .codemie/guides/development/security-patterns.md |
| **Async** | `time.sleep()` in async | `asyncio.sleep()` | .codemie/guides/development/performance-patterns.md |
| **Async** | Forget `await` | Always await coroutines | .codemie/guides/development/performance-patterns.md |
| **Async** | Blocking I/O in async | Use async libraries | .codemie/guides/development/performance-patterns.md |
| **Database** | SQL string interpolation | Parameterized queries | .codemie/guides/data/database-patterns.md |
| **Database** | N+1 queries | Eager loading/batch queries | .codemie/guides/data/database-optimization.md |
| **Database** | Load all data | Use pagination | .codemie/guides/data/database-patterns.md |
| **Logging** | `extra` parameter | F-strings | .codemie/guides/development/logging-patterns.md |
| **Security** | Hardcoded secrets | Environment variables | .codemie/guides/development/security-patterns.md |

---

## 🛠️ DEVELOPMENT COMMANDS

**🚨 CRITICAL**: Always `source .venv/bin/activate` before Python commands

**Setup**: `poetry install` → `poetry run download_nltk_packages`
**Run**: `cd src/ && poetry run uvicorn codemie.rest_api.main:app --reload` (port 8080)
**Lint/Format**: `make ruff` or `poetry run ruff check --fix && poetry run ruff format`
**Test** ⚠️: `poetry run pytest tests/` (only when explicitly requested)
**CI**: `make verify`

**API Docs**: http://localhost:8080/docs | **Migrations**: See `src/external/alembic/README.MD` (DO NOT modify)

---

## 🔧 TROUBLESHOOTING

**Command not found** (poetry/ruff/pytest) → `source .venv/bin/activate`
**ModuleNotFoundError** → `poetry install`
**Import errors** → Activate venv, check with `which python` (should show `.venv/bin/python`)
**Linting fails** → `poetry run ruff check --fix`
**Database/Async errors** → Check relevant guides
**Stuck on task** → Check confidence, load P0 guides, or ask user

---

## 🏗️ PROJECT CONTEXT

### Technology Stack

| Component | Tool | Version | Purpose |
|-----------|------|---------|---------|
| Language | Python | 3.12+ | Core language (use modern features) |
| API Framework | FastAPI | 0.115.0+ | REST API |
| AI Framework | LangChain | 0.3.x | Agent framework |
| AI Workflows | LangGraph | 0.6.7+ | Workflow orchestration |
| Testing | pytest | 8.3.x | Test framework |
| Linting | Ruff | 0.5.4+ | Lint + format |
| ORM | SQLModel | Latest | Database models |
| Database | PostgreSQL | Latest | Primary DB + pgvector |
| Search | Elasticsearch | Latest | Vector search |
| Packages | Poetry | 1.0.0+ | Dependency management |
| Migrations | Alembic | Latest | DB migrations |

### Core Components

| Component | Path | Purpose | Guide |
|-----------|------|---------|-------|
| REST API | `src/codemie/rest_api/` | FastAPI routers, endpoints | .codemie/guides/api/rest-api-patterns.md |
| Agents | `src/codemie/agents/` | LangChain agents | .codemie/guides/agents/langchain-agent-patterns.md |
| Workflows | `src/codemie/workflows/` | LangGraph workflows | .codemie/guides/workflows/langgraph-workflows.md |
| Tools | `src/codemie/agents/tools/` | Agent tools | .codemie/guides/agents/agent-tools.md |
| Services | `src/codemie/service/` | Business logic | .codemie/guides/architecture/service-layer-patterns.md |
| Repositories | `src/codemie/repository/` | Data access | .codemie/guides/data/repository-patterns.md |
| Config | `config/` | LLM configs, templates | .codemie/guides/development/configuration-patterns.md |
| Tests | `tests/` | pytest tests | .codemie/guides/testing/testing-patterns.md |

### Key Integrations

| Integration | Purpose | Guide |
|-------------|---------|-------|
| Elasticsearch | Vector search, indexing | .codemie/guides/data/elasticsearch-integration.md |
| PostgreSQL+pgvector | Database + embeddings | .codemie/guides/data/database-patterns.md |
| Multi-Cloud LLM | Direct provider integration (AWS Bedrock, Azure OpenAI, GCP Vertex AI, Anthropic) | .codemie/guides/integration/llm-providers.md |
| Cloud (AWS/Azure/GCP) | Cloud services, KMS | .codemie/guides/integration/cloud-integrations.md |
| MCP Servers | Dynamic tool loading via MCP-Connect | .codemie/guides/integration/mcp-integration.md |
| Git | Code analysis | Project feature |
| External Services | Confluence, Jira, X-ray, Google Docs integrations | .codemie/guides/integration/external-services.md (overview), service-specific guides |

**Note**: Enterprise features (LiteLLM proxy integration, LangFuse observability) are maintained in a separate `codemie-enterprise` repository with dedicated documentation.

### Architecture Patterns Overview

**LangGraph Workflows**:
- Supervisor pattern (central coordination)
- Node types: Agent, tool, state processor, finalizer
- Memory: Conversation summarization
- Execution: Sync/async modes
- Config: YAML + Jinja2 templates
- **Detail**: .codemie/guides/workflows/langgraph-workflows.md

**LangChain Agents**:
- Tool calling with dynamic loading
- Context: Git repo, knowledge base
- Streaming: Real-time responses
- Monitoring: Usage tracking
- **Detail**: .codemie/guides/agents/langchain-agent-patterns.md

**Multi-Cloud LLM Integration**:
- Direct provider integration (AWS Bedrock, Azure OpenAI, GCP Vertex AI, Anthropic)
- Environment-based provider selection (MODELS_ENV)
- Model configuration via YAML
- Category-based model selection (code, chat, reasoning)
- **Detail**: .codemie/guides/integration/llm-providers.md
- **Enterprise Note**: Advanced features (LiteLLM proxy, budget management) available in `codemie-enterprise` package

**Data Processing**:
- Datasources: Multi-format (code, PDF, Confluence)
- Indexing: Chunking + vector embeddings
- Search: Hybrid search with RRF (reciprocal rank fusion)
- Code Analysis: AST parsing, doc generation
- **Detail**: .codemie/guides/data/ (multiple guides)

---

## 📝 CODING STANDARDS

**Python 3.12+**: Use `match/case`, `str | int`, `f"{var=}"`, `asyncio.TaskGroup`, `except*` → code-quality.md

**Type Hints**: Required on all functions. Use `from __future__ import annotations`, modern union syntax (`str | int | None`)

**Async**: Use `asyncio.sleep()` (not `time.sleep`), `async with`, `asyncio.TaskGroup`. Never block I/O → performance-patterns.md

---

## 📚 POLICIES

**Testing**: ONLY when explicitly requested ("write tests", "run tests") - never proactive. Framework: pytest 8.3.x

**Git**: ONLY when explicitly requested ("commit", "push", "create PR") - never proactive. Branch: `EPMCDME-XXXX`

**VirtualEnv**: ALWAYS `source .venv/bin/activate` before Python commands. If "command not found" → forgot activation

**Shell**: Bash/Linux only - no Windows commands

---

## 🎯 REMEMBER

**Every Task**: Check guides first → Self-check → Load P0 guides → Apply patterns → Validate

**Non-Negotiable Quality**:
- No secrets, TODOs, or placeholders
- Parameterized SQL, f-string logging (no `extra`)
- API→Service→Repository layers, async for I/O
- Type hints, specific exceptions

**When to Ask**: Confidence < 80% after guides, ambiguous requirements, or policy unclear

**Always ask rather than assume**.
