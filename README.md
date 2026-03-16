# Codemie 🤖

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Website](https://img.shields.io/badge/website-codemie.ai-informational)](https://codemie.ai)
[![Docs](https://img.shields.io/badge/docs-docs.codemie.ai-informational)](https://docs.codemie.ai)

**Platform for AI-Native Delivery, Modernization, and Business.**

CodeMie is an open platform that lets teams build, orchestrate, and scale AI agents across the entire software lifecycle — from planning and coding to testing, deployment, and operations. It unites intelligent assistants, multi-agent workflows, deep integrations, and project knowledge in one system.

**What you can do with CodeMie:**

- 🚀 **AI-Native SDLC & Delivery** — Automate every phase of the software lifecycle: discovery, architecture, development, testing, and deployment with purpose-built AI agents.
- 🔄 **AI Migration & Modernization** — Migrate and modernize legacy systems and mainframes using AI-powered analysis, code exploration (AICE), and automated transformation workflows.
- 💼 **AI for Business & Operations** — Deploy AI agents across non-engineering functions such as finance, HR, sales, and support.

Key capabilities: multi-agent orchestration, rich data indexing (Git, Jira, Confluence, docs), deep integrations (MCP, AWS, Azure, GCP, Kubernetes), and a no-code Assistants Constructor.

**This repository — `codemie` — is the core backend component of the CodeMie platform.** It contains the FastAPI application, LangChain/LangGraph-based AI agents and orchestration, REST API, tool integrations, knowledge-base indexing (Git, Jira, Confluence), and all service-layer logic that powers the platform.

🌐 **Website:** [codemie.ai](https://codemie.ai)
📖 **Documentation:** [docs.codemie.ai](https://docs.codemie.ai)
🖥️ **CLI tool:** [codemie-code](https://github.com/codemie-ai/codemie-code)

## Quick Start

1. **Setup credentials** (see [Prerequisites & Setup](#prerequisites--setup))
2. **Configure .env** with your keys
3. **Run with Docker**:
   ```bash
   docker compose up --build codemie postgres elasticsearch
   ```
4. **Access**: http://localhost:8080/docs

## Prerequisites & Setup

### Requirements

- `docker` (or compatible engine)
- `docker compose` (modern syntax)
- Python 3.12 (recommended), pip, [Poetry](https://python-poetry.org/)
- [Node.js](https://nodejs.org/) (via NVM), npm
- [Git](https://git-scm.com/)
- [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) (gcloud CLI) - for authentication with GCP Artifact Registry
- [make](https://www.gnu.org/software/make/) (install via brew/choco if missing)

### .env Configuration

```env
AZURE_OPENAI_API_KEY="<your_api_key>"
AZURE_OPENAI_URL="https://your-azure-openai-endpoint.example.com"

AWS_ACCESS_KEY_ID="..."
AWS_SECRET_ACCESS_KEY="..."
AWS_DEFAULT_REGION="us-west-2"

GOOGLE_APPLICATION_CREDENTIALS="key.json"
```

**Add/adjust these in your .env as needed for selected features and stack!**

## Running the Application

### Docker 🐳

#### Core stack:
```bash
docker compose up --build codemie postgres elasticsearch
```

**API Docs**: http://localhost:8080/docs

### Running Locally 🐍

#### Setup
1. Install poetry according to the [official guide](https://python-poetry.org/)
2. Install dependencies: `poetry install`
3. Download NLTK packages: `poetry run download_nltk_packages`

#### Starting up
1. Navigate to src directory: `cd src/`
2. Run server: `poetry run uvicorn codemie.rest_api.main:app --host=0.0.0.0 --port=8080 --reload`
3. Up and running! 🔥 Check out `http://localhost:8080/docs`

## Installation 🏢

### Local Development

```bash
# Install base dependencies
poetry install --sync
```

### Docker Build

```bash
docker build -t codemie:latest .
```

### Makefile Commands

- **Install deps**: `make install`
- **Install OSS**: `make install-oss` - Install base dependencies only (--sync)
- **Build**: `make build`
- **Run unit tests**: `make test`
- **Lint/format (ruff)**: `make ruff`
- **License headers**: `make license` - Fix and verify Apache 2.0 license headers
- **Check license headers**: `make license-check` - Check for missing headers (CI mode)
- **Fix license headers**: `make license-fix` - Add missing license headers
- **Run ALL checks + tests** (strictly needed before commit): `make verify` - Runs ruff, license-check, and tests
- **Import AI Katas**: `make import-katas` - Clone and import AI katas from GitHub repository

### Git Hooks (pre-commit)
Hook toggle:
- You can enable/disable the Codemie pre-commit hook via env var:
  - `CODEMIE_PRECOMMIT_ENABLED=false` (default)
  - `CODEMIE_PRECOMMIT_ENABLED=true`
  - Add to `.env` or export in shell: `export CODEMIE_PRECOMMIT_ENABLED=false`

Install:
- `poetry install`
- `poetry run pre-commit install`

Commit flow:
- `ruff format` + `ruff check --fix`
- If files changed: lists changed files and blocks commit; stage and commit again (tests run once next attempt)
- If no changes: `ruff check` + `pytest`; prints concise test summary and blocks commit on failures

Manual:
- Run all hooks: `poetry run pre-commit run --all-files`
- Skip hooks for a single commit: `git commit --no-verify` (not recommended)

Troubleshooting:
- core.hooksPath set: `git config --unset-all core.hooksPath`; then `poetry run pre-commit install`
- Permission denied: `chmod +x scripts/git-hooks/pre_commit.sh`; `git update-index --chmod=+x scripts/git-hooks/pre_commit.sh`

## Development

### Development Workflow

**Quick Reference**:
- Branch naming: `<TICKET-ID>_short-description`
- Commit format: `<TICKET-ID>: Short Description`
- Before commit: `make verify` (runs ruff, license-check, and tests)
- PR requirements: At least 1 approval, green CI pipeline
- Rerun pipeline: Comment `/recheck` on PR

### Testing 🧪

```bash
poetry run pytest tests/
```

### Linting & Formatting 📝
#### Running Ruff

**Linting:**
```bash
poetry run ruff check
```

**Formatting:**
```bash
poetry run ruff format
```

### License Headers 📄

CodeMie uses Apache License 2.0 headers on all source files. Use these commands to manage license headers:

**Check for missing headers:**
```bash
make license-check                          # All files (CI-friendly)
make license-check FILE=path/to/file.py     # Single file
```

**Add missing headers:**
```bash
make license-fix                            # All files
make license-fix FILE=path/to/file.py       # Single file
```

**Fix and verify (recommended):**
```bash
make license                                # Fix then check all files
make license FILE=path/to/file.py           # Fix then check single file
```

**For CI pipelines:**
```bash
# Option 1: Direct command (quiet mode)
poetry run python scripts/license_headers/check_license_headers.py --check --quiet

# Option 2: Use verify target (includes ruff + license + tests)
make verify
```

The license checker:
- Automatically preserves shebangs, encoding declarations, and XML declarations
- Supports Python (`.py`) and Shell (`.sh`) files in `src/`, `scripts/`, and `tests/`
- Excludes auto-generated files, documentation, and infrastructure files
- Returns non-zero exit code if headers are missing (CI-friendly)

For more details, see [scripts/license_headers/README.md](scripts/license_headers/README.md).

### Tools (`src/codemie_tools/`)

All CodeMie tools are co-located in this repo under `src/codemie_tools/`. There is no separate
external package — tools are developed and shipped as part of the core repository.

#### Architecture

Two-layer design:
- **`src/codemie_tools/`** — foundational tool library: base classes (`CodeMieTool`, `BaseToolkit`,
  `DiscoverableToolkit`), all domain tool implementations, and `ToolMetadata` for SmartToolSelector
  discovery.
- **`src/codemie/agents/tools/`** — agent-facing toolkit layer: composes `codemie_tools` toolkits
  into `CodeToolkit`, `KBToolkit`, `IDEToolkit`, `PlatformToolkit`, `SkillTool`, and plugin tools.

#### Available Tool Categories

| Category | Path | Coverage |
|----------|------|----------|
| Base | `src/codemie_tools/base/` | `CodeMieTool`, `BaseToolkit`, `ToolMetadata`, utilities |
| Code | `src/codemie_tools/code/` | SonarQube, linter, AI-assisted code editing (diff-coder) |
| Cloud | `src/codemie_tools/cloud/` | AWS (S3, Bedrock, KMS), Azure (Blob, KeyVault), GCP (GCS, Vertex AI), Kubernetes |
| VCS / Git | `src/codemie_tools/core/vcs/` | GitHub, GitLab, Bitbucket, Azure DevOps Git |
| Project Management | `src/codemie_tools/core/project_management/` | Confluence, Jira |
| QA | `src/codemie_tools/qa/` | X-ray, Zephyr Scale, Zephyr Squad |
| Data Management | `src/codemie_tools/data_management/` | Elasticsearch, SQL, file system, code executor |
| File Analysis | `src/codemie_tools/file_analysis/` | CSV, DOCX, PDF, PPTX, XLSX |
| Azure DevOps | `src/codemie_tools/azure_devops/` | Wiki, Work Items, Test Plans |
| Notifications | `src/codemie_tools/notification/` | Email, Telegram |
| ITSM | `src/codemie_tools/itsm/` | ServiceNow |
| Access Management | `src/codemie_tools/access_management/` | Keycloak |
| Research | `src/codemie_tools/research/` | Web research |
| Vision | `src/codemie_tools/vision/` | Image analysis |
| Open API | `src/codemie_tools/open_api/` | Generic REST/OpenAPI invocation |

#### Contributing to Tools

To add or modify a tool, work directly in `src/codemie_tools/`. For architecture patterns and step-by-step guides:
- `.codemie/guides/agents/agent-tools.md` — base classes, execution flow, metadata
- `.codemie/guides/agents/custom-tool-creation.md` — creating new tools
- `.codemie/guides/agents/tool-overview.md` — SmartToolSelector and `DiscoverableToolkit`

## Advanced Configuration

### Custom LLM and Embedding Models

By default, AI/Run provides predefined LLM and embedding models for AWS, Azure, GCP. They can be found here: `config/llms`.

The `MODELS_ENV` is used to specify the environment for the models. For example, `MODELS_ENV=azure` will use the models from the `config/llms/llm-azure-config.yaml` file (Pattern: `llm-<MODELS_ENV>-config.yaml`).

Example configuration in `deploy-templates/values.yaml`:

```yaml
extraEnv:
  - name: MODELS_ENV
    value: "your-org"

extraVolumeMounts: |
  - name: codemie-llm-customer-config
    mountPath: /app/config/llms/llm-your-org-config.yaml
    subPath: llm-your-org-config.yaml

extraVolumes: |
  - name: codemie-llm-customer-config
    configMap:
      name: codemie-llm-customer-config

extraObjects:
  - apiVersion: v1
    kind: ConfigMap
    metadata:
      name: codemie-llm-customer-config
    data:
      llm-your-org-config.yaml: |
        llm_models:
          - base_name: "gpt-4o-2024-08-06"
            deployment_name: "gpt-4o-2024-08-06"
            label: "GPT-4o 2024-08-06"
            multimodal: true
            enabled: true
            default: true
            provider: "azure_openai"
            cost:
              input: 0.0000025
              output: 0.000011

        embeddings_models:
          - base_name: "ada-002"
            deployment_name: "text-embedding-ada-002"
            label: "Text Embedding Ada"
            enabled: true
            default: true
            provider: "azure_openai"
            cost:
              input: 0.0000001
              output: 0
```

## Monitoring & Operations

### Database Migrations
For information about working with database migrations, see the [Alembic README](src/external/alembic/README.MD).

### Metrics 📊

CodeMie provides the following metrics:

| Metric name                                | Description                                  |
|--------------------------------------------|----------------------------------------------|
| `create_assistant`                         | Number of created assistants                 |
| `create_assistant_error`                   | Number of created assistants with errors     |
| `update_assistant`                         | Number of updated assistants                 |
| `update_assistant_error`                   | Number of updated assistants with errors     |
| `delete_assistant`                         | Number of deleted assistants                 |
| `codemie_tools_usage_total`                | Number of tools usage                        |
| `codemie_tools_usage_tokens`               | Number of tokens used with tools             |
| `codemie_tools_usage_errors_total`         | Number of tools usage with errors            |
| `workflow_execution_total`                 | Number of workflow executions                |
| `workflow_execution_state_total`           | Number of workflow executions by state       |
| `workflow_created_total`                   | Number of created workflows                  |
| `workflow_updated_total`                   | Number of updated workflows                  |
| `workflow_deleted_total`                   | Number of deleted workflows                  |
| `datasource_index_total`                   | Number of indexed datasources                |
| `datasource_index_documents`               | Number of indexed documents in datasources   |
| `datasource_index_errors_total`            | Number of indexed datasources with errors    |
| `datasource_reindex_total`                 | Number of reindexed datasources              |
| `datasource_reindex_documents`             | Number of reindexed documents in datasources |
| `datasource_reindex_errors_total`          | Number of reindexed datasources with errors  |
| `delete_datasource`                        | Total number of deleted datasources          |
| `update_datasource`                        | Total number of updated datasources          |
| `codemie_mcp_servers`                      | MCP server status and configuration          |
| `codemie_assistant_generator_total`        | Assistant generation requests                |
| `codemie_assistant_generator_errors_total` | Assistant generation errors                  |
| `codemie_prompt_generator_total`           | Prompt generation requests                   |
| `codemie_prompt_generator_errors_total`    | Prompt generation errors                     |

## License Compliance

**Check licenses of production dependencies**:
```bash
poetry run pip-licenses --packages $(poetry show --only main | awk '{print $1}' | tr '\n' ' ')
```
This checks licenses only for production packages (excludes dev dependencies like `pytest`, etc.).

## Contributing

We welcome contributions! Please read our [Contributing Guide](CONTRIBUTING.md) and [Code of Conduct](CODE_OF_CONDUCT.md) before submitting a pull request.

## License

CodeMie is licensed under the [Apache License 2.0](LICENSE).