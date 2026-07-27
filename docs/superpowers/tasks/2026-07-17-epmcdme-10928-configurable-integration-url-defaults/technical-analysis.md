# Technical Research

**Task**: integration config url placeholder env-var tools datasource credentials
**Generated**: 2026-07-17T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-10928: Default hostnames for integrations & data sources. Many integrations & data sources require users to configure a hostname (AzureDevOps, Github, Gitlab, Jira, Confluence, Xray, etc.). Currently input fields show either a placeholder (hint) or a pre-filled default value. The proposal is to make these default/placeholder URLs configurable per deployment via environment variables, so each deployment (EPAM, SCOR, etc.) can override them. Implementation direction: a utility method used in each dedicated config file that retrieves an env var override for the URL field and replaces the hardcoded placeholder/default, with fallback to the existing hardcoded value. NOT a global config override hack. Example config model: src/codemie_tools/qa/xray/models.py has XrayConfig.base_url with placeholder 'https://xray.cloud.getxray.app'. The same pattern applies to data sources. Default values (pre-filled in the UI) should also be configurable in the same way as placeholders.

---

## 2. Codebase Findings

### Existing Implementations

**Base classes:**

- `src/codemie_tools/base/models.py` — `CodeMieToolConfig(BaseModel)`: thin base with only `credential_type` field. All integration configs inherit this. `RequiredField(default="", **kwargs)` helper injects `required_at_runtime: True` into `json_schema_extra` and is the standard constructor for mandatory URL/token fields. The `placeholder` value is stored in `json_schema_extra={"placeholder": "https://..."}` on the `Field(...)` call — it is arbitrary JSON schema metadata, not a pydantic concept.
- `src/codemie_tools/base/toolkit_provider.py` — `get_available_tools_configs_info()`: scans all `CodeMieToolConfig` subclasses, calls `model_json_schema()`, extracts `placeholder`/`default` metadata via `_extract_field_data()`, and serves results to the UI. **LRU-cached at startup** via `@functools.lru_cache(maxsize=None)`. Env-var values must be resolved at module-import time (class-definition time) to be captured by this cache.
- `src/codemie/configs/config.py` — `Config(BaseSettings)`: pydantic-settings class, singleton `config = Config()`. Already holds infrastructure URL fields: `ELASTIC_URL`, `KEYCLOAK_ADMIN_URL`, `MERMAID_SERVER_URL`, `KUBERNETES_API_URL`, etc. Auto-reads from `.env` + environment.

**Integration config models with URL fields — full inventory:**

| Config class | File | URL field | Field type | Current default | Current placeholder |
|---|---|---|---|---|---|
| `GitlabConfig` | `src/codemie_tools/core/vcs/gitlab/models.py` | `url` | `RequiredField` | `""` | `"https://gitlab.example.com"` |
| `GithubConfig` | `src/codemie_tools/core/vcs/github/models.py` | `url` | `Field(default=...)` | `"https://api.github.com"` | `"https://api.github.com"` — **pre-filled default** |
| `AzureDevOpsGitConfig` | `src/codemie_tools/core/vcs/azure_devops_git/models.py` | `url` | `RequiredField` | `""` | `"https://dev.azure.com"` |
| `JiraConfig` | `src/codemie_tools/core/project_management/jira/models.py` | `url` | `Field(default="")` | `""` | `"https://jira.example.com/"` |
| `ConfluenceConfig` | `src/codemie_tools/core/project_management/confluence/models.py` | `url` | `Field(default="")` | `""` | `"https://confluence.example.com/"` |
| `XWikiConfig` | `src/codemie_tools/core/project_management/xwiki/models.py` | `url` | `Field(default="")` | `""` | `"https://wiki.example.com"` |
| `KeycloakConfig` | `src/codemie_tools/access_management/keycloak/models.py` | `base_url` | `RequiredField` | `""` | `"https://keycloak.example.com"` |
| `ElasticConfig` | `src/codemie_tools/data_management/elastic/models.py` | `url` | `RequiredField` | `""` | `"https://your-elastic-instance.com"` |
| `EmailToolConfig` | `src/codemie_tools/notification/email/models.py` | `url` (SMTP) | `RequiredField` | `""` | `"smtp.gmail.com:587"` |
| `EmailToolConfig` | same | `oauth_authority` | `Field(default=...)` | `"https://login.microsoftonline.com"` | same — **pre-filled default** |
| `EmailToolConfig` | same | `oauth_scope` | `Field(default=...)` | `"https://outlook.office365.com/.default"` | same — **pre-filled default** |
| `XrayConfig` | `src/codemie_tools/qa/xray/models.py` | `base_url` | `RequiredField` | `""` | `"https://xray.cloud.getxray.app"` |
| `ZephyrConfig` | `src/codemie_tools/qa/zephyr/models.py` | `url` | bare annotation, no `Field` | — | — |
| `ServiceNowConfig` | `src/codemie_tools/itsm/servicenow/models.py` | `url` | `RequiredField` | `""` | `"https://your-instance.service-now.com"` |
| `ReportPortalConfig` | `src/codemie_tools/report_portal/models.py` | `url` | `RequiredField` | `""` | `"https://reportportal.example.com"` |
| `KubernetesConfig` | `src/codemie_tools/cloud/kubernetes/models.py` | `url` | `RequiredField` | `""` | `"https://kubernetes.default.svc"` |
| `AzureDevOpsWorkItemConfig` | `src/codemie_tools/azure_devops/work_item/models.py` | `organization_url` | `RequiredField` | `""` | `"https://dev.azure.com/your-organization"` |
| `AzureDevOpsWikiConfig` | `src/codemie_tools/azure_devops/wiki/models.py` | `organization_url` | `RequiredField` | `""` | `"https://dev.azure.com/your-organization"` |
| `AzureDevOpsTestPlanConfig` | `src/codemie_tools/azure_devops/test_plan/models.py` | `organization_url` | `RequiredField` | `""` | `"https://dev.azure.com/your-organization"` |
| `SonarConfig` | `src/codemie_tools/code/models.py` | `url` | `RequiredField` | `""` | `"https://sonarqube.example.com"` |

**Out-of-scope / special cases (not standard `CodeMieToolConfig` URL fields):**

- `ZephyrSquadConfig` — `src/codemie_tools/qa/zephyr_squad/models.py` — no URL field at all; the URL `"https://prod-api.zephyr4jiracloud.com/connect"` is a module-level constant `DEFAULT_BASE_URL` in `src/codemie_tools/qa/zephyr_squad/api_wrapper.py`, not user-configurable
- `SharePointCredentials` — `src/codemie/rest_api/models/settings.py` — plain `BaseModel` (not `CodeMieToolConfig`), has `url: str` with no `Field` metadata; accessed via `SettingsService.SHAREPOINT_FIELDS`; not scanned by `toolkit_provider`
- `SQLConfig` — `src/codemie_tools/data_management/sql/models.py` — uses `host + port` pattern (not a URL), so env-var URL override is not directly applicable

**Config loading flow:**

- `src/codemie/service/settings/settings.py` — `SettingsService.get_config(config_class, user_id, ...)`: queries DB (`settings` table), calls `setting.normalize_values()` → `{key: value}` dict, returns `config_class(**dict)`
- `_handle_missing_config()` in the same file (lines 692–704): the **only existing env-var URL override** in the codebase — for `CredentialTypes.ELASTIC`, reads `config.ELASTIC_URL` from `Config(BaseSettings)` and returns `ElasticConfig(url=config.ELASTIC_URL)`. This is the service-layer fallback pattern established for Elastic.
- `src/codemie/service/tools/discovery/config_extractor.py` — maps toolkit classes → config classes; imports every config class

### Architecture and Layers Affected

- **Config model layer** (`src/codemie_tools/**/models.py`): where `placeholder` and `default` are declared on each Field. The utility method will be called at class-definition time so that values are captured before the LRU cache is populated at startup.
- **Base utilities layer** (`src/codemie_tools/base/models.py`): new utility function will live here, adjacent to `RequiredField` and `CodeMieToolConfig`.
- **App config layer** (`src/codemie/configs/config.py`): if the implementation follows the configuration guide's directive to centralise env vars in `Config(BaseSettings)`, new fields (e.g., `DEFAULT_JIRA_URL`, `DEFAULT_GITLAB_URL`) go here and are auto-read from the environment. If the implementation uses direct `os.getenv` in the utility function, this layer is not touched.
- **Settings service layer** (`src/codemie/service/settings/settings.py`): the existing `_handle_missing_config` Elastic precedent. The new utility approach at model-definition time does not require changes here for placeholder-only fields, but may need updating if "pre-filled default" behaviour is also wanted at the service fallback level.
- **Schema/discovery layer** (`src/codemie_tools/base/toolkit_provider.py`): passively affected — `_extract_field_data()` reads `placeholder` and `default` from `model_json_schema()` and serves them to the UI. The LRU cache means changes must be resolved at startup; no code changes needed here.

### Integration Points

- `src/codemie/rest_api/routers/tool.py` — `GET /v1/tools/configs` and `GET /v1/tools/{tool_name}/schema`: serve the cached config schemas including placeholder values to the frontend UI. Env-var-resolved placeholders/defaults will be reflected here automatically once values are baked into field metadata at import time.
- `src/codemie/service/settings/settings.py` — `get_config()`: instantiates typed config objects from DB credentials. No direct change needed for placeholder-only fields. Pre-filled-default behaviour (GitHub `url`, Email `oauth_authority/oauth_scope`) may need to be coordinated here if the intent is also to affect runtime fallback defaults.
- `src/external/deployment_scripts/preconfigured_workflows.py` — uses `CHANGEME_URL = 'https://changeme.example.com'` constant from `base_settings.py` to seed pre-configured integration credentials. If per-integration default URL env vars are introduced, `CHANGEME_URL` in seeding scripts may also need to reference these new env vars.
- `deploy-templates/values.yaml` — Helm chart; currently has no Jira/Confluence/GitLab URL entries. New env var names introduced by this ticket will need corresponding entries in the Helm values template for deployment configuration.

### Patterns and Conventions

- `RequiredField(description="...", json_schema_extra={"placeholder": "https://..."})` — the standard pattern for all mandatory URL fields; sets `default=""` and `required_at_runtime: True`.
- `Field(default="https://api.github.com", json_schema_extra={"placeholder": "https://api.github.com"})` — the pre-filled default pattern (GitHub, Email oauth fields); `default` is a real value, not empty.
- `json_schema_extra` key `"sensitive": True` — present on secret fields alongside `placeholder`.
- `@model_validator(mode="before")` for legacy field name aliasing — used in `KeycloakConfig` (`url` → `base_url`), `XrayConfig` (`url` → `base_url`), `KubernetesConfig` (`kubernetes_url` → `url`), `AzureDevOpsWorkItemConfig` (constructs `organization_url` from parts).
- `Config(BaseSettings)` — the established pattern for infrastructure URL env vars; `ELASTIC_URL`, `KEYCLOAK_ADMIN_URL` are examples of URLs that are already env-var-backed.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/development/configuration-patterns.md` — **Critical constraint**: "Never read env vars directly in feature code. All runtime settings go through `src/codemie/configs/` and YAML config trees." This creates a direct tension with the ticket's proposed utility method that reads env vars inside each config model file. The guide's intent would be satisfied by adding URL fields to `Config(BaseSettings)` and reading them from there rather than via raw `os.getenv` in tool model files.
- `.ai-run/guides/integration/external-services.md` — "Credentials and URLs must never be hardcoded; they must flow through config/provider mechanisms. Sanitize errors, never log provider credentials."
- `.ai-run/guides/agents/agent-tools.md` and `tool-overview.md` — Two distinct layers: platform agent tools at `src/codemie/agents/tools/`; reusable external integrations at `src/codemie_tools/`. Never call provider SDKs from routers.
- `.ai-run/guides/agents/custom-tool-creation.md` — Extend nearest toolkit; add tests in `tests/codemie_tools/`.

### Architectural Decisions

- **`placeholder` is a UI display hint, not a runtime default.** The `json_schema_extra={"placeholder": "..."}` convention is used only to provide a hint to the frontend; the field's actual `default` (usually `""`) is what the pydantic model uses at runtime.
- **`Config(BaseSettings)` is the single authority for env-var-backed values.** Infrastructure-level URLs (Elastic, Keycloak, Mermaid, Kubernetes API, NATS) are declared as typed attributes in `Config` and read automatically. `CodeMieToolConfig` subclasses are plain `BaseModel` — not `BaseSettings` — so they do not automatically read from the environment.
- **The only existing env-var URL override precedent** is the Elastic special case in `SettingsService._handle_missing_config()`: `config.ELASTIC_URL` from `Config(BaseSettings)` is used as a fallback when no credential record exists in the DB. This is a service-layer fallback, not a model-definition-time pattern.
- **Zephyr Squad's `DEFAULT_BASE_URL`** is an anti-pattern noted in research: the vendor URL is a module-level constant in `api_wrapper.py` rather than a config model field, making it untouchable by the proposed utility method without first elevating it to a `ZephyrSquadConfig` field.

### Derived Conventions

- New env var names for integration URL defaults should follow the existing `Config` field naming style: uppercase, underscore-separated, prefixed consistently (e.g., `DEFAULT_JIRA_URL`, `DEFAULT_CONFLUENCE_URL`, or `JIRA_DEFAULT_URL` — the project uses both orderings; `ELASTIC_URL` and `KEYCLOAK_ADMIN_URL` suggest noun-first).
- The utility function should produce a value usable as a `Field` default or `json_schema_extra` placeholder. To satisfy the configuration guide, the cleanest approach is: add named fields to `Config(BaseSettings)`, then call them from a utility function in `codemie_tools/base/models.py` (e.g., `from codemie.configs import config; return config.DEFAULT_JIRA_URL`). However, this creates a cross-package dependency from `codemie_tools` → `codemie`. An alternative that avoids the guide tension but still uses the same env var names: define the utility as `os.getenv("DEFAULT_JIRA_URL", "https://jira.example.com/")` called at class-definition time in each model file.
- Three Azure DevOps configs share the same base hostname placeholder (`https://dev.azure.com`). A single env var (`DEFAULT_AZURE_DEVOPS_URL`) could serve all three, reducing the env var count.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie_tools/core/project_management/jira/test_models.py` — `JiraConfig` field validation (url, token, cloud); uses hardcoded `https://jira.example.com`; no env-var URL override tests
- `tests/codemie_tools/core/project_management/confluence/test_models.py` — `ConfluenceConfig` validation; no env-var tests
- `tests/codemie_tools/core/vcs/gitlab/test_models.py` — `GitlabConfig` (url, token), empty-config behavior; no env-var tests
- `tests/codemie_tools/core/vcs/github/test_models.py` — `GithubConfig` (token only; url is fixed default); no env-var tests
- `tests/codemie_tools/core/vcs/azure_devops_git/test_models.py` — `AzureDevOpsGitConfig` (url, organization, token, api_version); no env-var tests
- `tests/codemie_tools/qa/xray/test_xray_models.py` — `XrayConfig` (base_url, client_id, client_secret, limit, verify_ssl); no env-var tests
- `tests/codemie_tools/code/sonar/test_sonar_tools.py` — `SonarConfig` via `SonarTool`; no dedicated `test_models.py`
- `tests/codemie/configs/test_config.py` — **best reference for env-var override testing**: uses `monkeypatch.setenv(...)` then instantiates `Config()`; `test_authorized_apps_allowed_key_domains_env_override` is the direct pattern to follow
- `tests/codemie_tools/data_management/code_executor/test_models.py` — **richest reference for env-var-to-config-field pattern**: uses `patch.dict(os.environ, {...})` then calls `CodeExecutorConfig.from_env()` with both default and override assertions

### Testing Framework and Patterns

- pytest 8.3.1, pytest-asyncio 0.23.7, pytest-mock 3.14.0, pytest-env 1.1.3, pytest-httpx 0.35.0, pydantic-settings 2.5.2
- Three coexisting test styles: `unittest.TestCase` (jira, confluence, gitlab, github model tests), plain pytest class (xray, code_executor model tests), pytest functions (configs/test_config.py, azure_devops_git model tests)
- Fixtures in per-integration `conftest.py` files with hardcoded example URLs
- `monkeypatch.setenv()` — pytest-native env var injection; used in `test_config.py` and `test_file_system_toolkit.py`
- `patch.dict(os.environ, {...})` — `unittest.mock` style; used extensively in `code_executor/test_models.py` and `test_security_config.py`; `patch.dict(os.environ, {}, clear=True)` tests the "no env var set → use hardcoded default" fallback behavior

### Coverage Gaps

- **Zero existing tests** for env-var URL overrides on any integration config model (Jira, Confluence, GitLab, GitHub, Azure DevOps, Xray, Sonar, SharePoint, ServiceNow, Kubernetes, etc.)
- No `test_models.py` for SonarConfig — only `test_sonar_tools.py` exists
- No test file for SharePointCredentials URL field
- No tests for fallback-to-hardcoded-value semantics (i.e., `os.getenv("DEFAULT_JIRA_URL", hardcoded_fallback)` returning fallback when var is absent)
- No tests for ZephyrConfig URL field at all
- No tests for ZephyrSquadConfig's `DEFAULT_BASE_URL` constant in `api_wrapper.py`

---

## 5. Configuration and Environment

### Environment Variables

Currently declared URL-related fields in `Config(BaseSettings)` at `src/codemie/configs/config.py`:
- `ELASTIC_URL` — default `"http://localhost:9200"`
- `KEYCLOAK_ADMIN_URL`, `KEYCLOAK_LOGOUT_URL`
- `MERMAID_SERVER_URL` — default `"http://localhost:8082"`
- `CALLBACK_API_BASE_URL` — default `"http://host.docker.internal:8080"`
- `KUBERNETES_API_URL`, `VAULT_URL`, `AZURE_KEY_VAULT_URL`
- `NATS_SERVERS_URI` — default `"nats://nats:4222"`
- `MCP_CONNECT_URL` — default `"http://localhost:3000"`
- `FRONTEND_URL` — default `"http://localhost:3000"`
- `LITE_LLM_URL`, `A2A_PROVIDER_URL`, `PYROSCOPE_SERVER_URL`
- `KATAS_REPO_URL` — default `"https://github.com/codemie-ai/codemie-katas.git"`

**Not yet declared** (to be added by this ticket): `DEFAULT_JIRA_URL`, `DEFAULT_CONFLUENCE_URL`, `DEFAULT_GITLAB_URL`, `DEFAULT_GITHUB_URL`, `DEFAULT_AZURE_DEVOPS_URL`, `DEFAULT_XRAY_URL`, `DEFAULT_SONAR_URL`, `DEFAULT_KEYCLOAK_URL`, `DEFAULT_SERVICENOW_URL`, `DEFAULT_KUBERNETES_URL`, `DEFAULT_REPORTPORTAL_URL`, `DEFAULT_XWIKI_URL`, `DEFAULT_ELASTIC_TOOL_URL`, `DEFAULT_EMAIL_SMTP_URL`, `DEFAULT_ZEPHYR_URL`

One pre-existing pattern in `codemie_tools` that reads env vars directly (not via `Config`): `os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")` in `langfuse/dependencies.py`.

### Configuration Files

- `src/codemie/configs/config.py` — `Config(BaseSettings)` singleton; loaded at startup via `env_file=find_dotenv(".env")`; all fields are auto-env-var-overridable
- `src/codemie/service/settings/base_settings.py` — contains `CHANGEME_URL = 'https://changeme.example.com'` constant used when seeding pre-configured credentials via `src/external/deployment_scripts/preconfigured_workflows.py`
- `config/datasources/datasources-config.yaml` — loaded by `src/codemie/datasource/datasources_config.py`; has `graph_base_url: "https://graph.microsoft.com"` for SharePoint — a YAML-level pattern for vendor default URLs
- `.env` — sparse local dev file; no integration hostname defaults present

### Feature Flags and Deployment Concerns

- No feature flags relevant to this change.
- `deploy-templates/values.yaml` — Helm chart values file. Currently has `ELASTIC_URL`, `VAULT_URL`, `KEYCLOAK_LOGOUT_URL` etc. as named entries with empty values. New integration URL env vars must be added here for each deployment to override them. The file uses `%%DOMAIN%%` token for the ingress host — new entries should follow the same pattern (key present, value empty, filled per deployment).
- The Helm chart is the primary deployment customization mechanism; EPAM and SCOR deployments would set their specific values here.

---

## 6. Risk Indicators

- **LRU cache timing constraint**: `get_available_tools_configs_info()` in `toolkit_provider.py` is LRU-cached at startup. The utility function must resolve env-var values at Python module import time (i.e., as class-level `Field` defaults), not lazily at request time, or the cached schema will contain unresolved values. This means calling `os.getenv(...)` or `config.FIELD` in the `Field(default=...)` argument at class definition — a subtle timing requirement that is easy to get wrong.
- **Configuration guide conflict**: `.ai-run/guides/development/configuration-patterns.md` states "Never read env vars directly in feature code." The proposed utility method (reading `os.getenv` directly inside `codemie_tools/**/models.py`) technically violates this. The guide-compliant alternative — adding fields to `Config(BaseSettings)` and importing `config` from `codemie_tools/` — creates a cross-package dependency (`codemie_tools` → `codemie`) that may be an architectural violation (tools package depending on main app package). This design decision must be resolved before implementation.
- **Two semantically different URL field types require different handling**: (a) placeholder-only fields (`default=""`, `json_schema_extra={"placeholder": "..."}`) — env var should override the `placeholder` value in schema; (b) pre-filled default fields (`default="https://api.github.com"`, `GithubConfig.url`; `EmailToolConfig.oauth_authority/oauth_scope`) — env var should override the `default` value itself. The utility function must handle both cases, or two variants are needed.
- **ZephyrSquadConfig DEFAULT_BASE_URL is not a config field**: The URL `"https://prod-api.zephyr4jiracloud.com/connect"` lives as a module constant in `src/codemie_tools/qa/zephyr_squad/api_wrapper.py`. To apply the env-var pattern, this constant must first be elevated to a field in `ZephyrSquadConfig` — a prerequisite step not mentioned in the ticket.
- **ZephyrConfig.url has a bare annotation (no Field)**: The bare `url: str` annotation in `src/codemie_tools/qa/zephyr/models.py` must be converted to a `Field(...)` call before the utility method can be applied.
- **SharePointCredentials is not a CodeMieToolConfig subclass**: `SharePointCredentials` in `src/codemie/rest_api/models/settings.py` is a plain `BaseModel` outside the standard toolkit config hierarchy, with `url: str` and no `Field` metadata. The standard utility method approach will not cover it without separate handling.
- **Elastic has duplicate URL configuration**: `ElasticConfig` has both a `RequiredField` with placeholder AND the `_handle_missing_config` service-layer override using `config.ELASTIC_URL`. Introducing a `DEFAULT_ELASTIC_TOOL_URL` env var alongside the existing `ELASTIC_URL` field in `Config` would create two env vars with overlapping semantics for Elastic — the relationship between them must be clarified.
- **Three Azure DevOps configs share the same base URL**: `AzureDevOpsGitConfig.url`, `AzureDevOpsWorkItemConfig.organization_url`, `AzureDevOpsWikiConfig.organization_url`, and `AzureDevOpsTestPlanConfig.organization_url` all currently placeholder to `https://dev.azure.com`. A single env var would serve all, but the field names differ (`url` vs `organization_url`) and `AzureDevOpsWorkItemConfig` has a `model_validator` that constructs `organization_url` from legacy parts — the utility must account for this.
- **No existing tests for env-var URL overrides**: Zero test coverage for this pattern across all integration config models. The new utility method and its per-integration application will require new test cases across approximately 17 config model test files. The testing baseline for this pattern exists in `tests/codemie/configs/test_config.py` and `tests/codemie_tools/data_management/code_executor/test_models.py`.
- **CHANGEME_URL seeding interaction**: `src/external/deployment_scripts/preconfigured_workflows.py` seeds pre-configured credentials using `CHANGEME_URL = 'https://changeme.example.com'` as the URL value. This seeding script bypasses the config model layer entirely and writes directly to the DB. If per-integration URL env vars are meant to also influence seeded credentials, the seeding script must also be updated — a scope question not resolved in the ticket.
- **Helm values.yaml must be updated for each new env var**: New env var names must be added to `deploy-templates/values.yaml` for deployments to override them. Missing this step means env vars are defined in code but have no deployment-time override surface.
- **~20 files in codemie_tools require changes**: The scope is wider than it might appear — approximately 18–20 model files each need a one-line change to wrap their URL field definition in the utility function. Consistency across all of them (same env var naming convention, same fallback behavior, same treatment of placeholder vs default) is a code-review discipline risk.

---

## 7. Summary for Complexity Assessment

This task spans four architectural layers: the config model layer (~18–20 `CodeMieToolConfig` subclass files in `src/codemie_tools/`), the base utilities layer (`src/codemie_tools/base/models.py` — one new utility function), the app config layer (`src/codemie/configs/config.py` — ~15 new fields if following the guide), and the deployment layer (`deploy-templates/values.yaml` — matching Helm entries). The code change surface is wide but shallow: the core utility function is a few lines, and each of the ~18–20 integration model files needs a targeted one-line change wrapping the URL field's `default` or `placeholder` value. No database migrations, no API changes, and no workflow logic changes are required.

There is one genuine design decision with architectural consequences: whether to implement the utility as raw `os.getenv(ENV_VAR, hardcoded_fallback)` called at class-definition time in each model file (simple, self-contained, but conflicts with the configuration guide's "no direct env var reads in feature code" rule), or to add new fields to `Config(BaseSettings)` and import `config` from within `codemie_tools/` (guide-compliant, but creates a cross-package dependency from the tools package into the main app package). Neither option is novel — both patterns exist in the codebase already (`os.getenv` used in `langfuse/dependencies.py` and `code_executor`; `Config` fields used for all infrastructure URLs). The implementation team must resolve this design point before writing code, as it affects where env var names are canonically declared and how they are documented for operators.

Test coverage posture is weak for this domain: zero existing tests for env-var URL overrides on any integration config. New tests are needed for each integration's utility method — both the "env var set → override" case and the "env var absent → hardcoded fallback" case. Two strong reference patterns exist (`tests/codemie/configs/test_config.py` using `monkeypatch.setenv`, and `tests/codemie_tools/data_management/code_executor/test_models.py` using `patch.dict(os.environ)`). Key risks to flag for complexity scoring: the LRU cache startup-time constraint (wrong timing = schema always shows hardcoded value), the three special cases that require prerequisite work before the pattern can be applied (ZephyrSquad, ZephyrConfig, SharePoint), the Elastic dual-URL ambiguity, and the deployment-time Helm values update that must accompany every new env var.
