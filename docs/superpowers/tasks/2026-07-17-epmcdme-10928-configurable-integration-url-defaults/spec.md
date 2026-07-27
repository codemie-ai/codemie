# Spec: Configurable Integration URL Defaults (EPMCDME-10928)

## Problem

~20 integration `CodeMieToolConfig` subclasses had hardcoded URL placeholder and default values. These could not be customised per deployment (EPAM, SCOR, etc.) without code changes. Operators had no hook to point integrations at their company's Jira, GitLab, etc. without touching source.

## Scope

Backend only. No DB migrations, no API contract changes.

---

## Design

### Core mechanism

All tool config defaults are driven by `customer_config.get_tool_default(tool, field)`, backed by a `tool_defaults` section in `config/customer/customer-config.yaml`. Each model defines a module-level alias and a `TOOL_NAME` ClassVar:

```python
from codemie.configs.customer_config import customer_config
_tool_default = customer_config.get_tool_default

class JiraConfig(CodeMieToolConfig, FileConfigMixin):
    TOOL_NAME: ClassVar[str] = "jira"
    ...
```

Fields read their defaults at **class-definition time** (inside `Field(...)` arguments), so values are baked into field metadata before `get_available_tools_configs_info()` populates its LRU cache at startup. When a YAML key is absent, `get_tool_default` returns `None`; `or ""` / `or False` fallbacks keep fields typed correctly without requiring every tool to be listed in the YAML.

### Field semantics: default vs placeholder

Both `default` and `placeholder` are sourced from YAML at class-definition time:

- **`default=_tool_default(TOOL_NAME, "<field>")`** — the value pre-filled in the UI form when no credential has been saved. Set to the deployment-configured URL so the UI can use it as `defaultUrl`.
- **`json_schema_extra={"placeholder": _tool_default(TOOL_NAME, "<field>_placeholder")}`** — hint text shown in the input field when it is empty.

```python
url: str = Field(
    default=_tool_default(TOOL_NAME, "url") or "",
    description="URL to your Jira instance",
    json_schema_extra={"placeholder": _tool_default(TOOL_NAME, "url_placeholder")},
)
```

YAML key naming matches the Python field name:
- `url` field → YAML keys `url` and `url_placeholder`
- `base_url` field (keycloak, xray) → YAML keys `base_url` and `base_url_placeholder`
- `smtp_url` field (email) → YAML keys `smtp_url` and `smtp_url_placeholder`

### YAML configuration surface

`config/customer/customer-config.yaml` ships a `tool_defaults` section pre-populated with placeholder hint text for all tools. Operators uncomment and set values to pre-fill fields or override UI hints without a code change or app rebuild (restart required for placeholder changes, which are frozen at class-definition time):

```yaml
tool_defaults:
  jira:
    url_placeholder: "URL, e.g. https://jira.example.com/ or https://jira.example.com/jira/"
    # url: "https://jira.example.com/"
    # cloud: false
  git:
    url_placeholder: "URL"
    # url: "https://git.example.com"
    # auth_type: "pat"
  azuredevops:
    url_placeholder: "URL, e.g. https://dev.azure.com"
    # url: "https://dev.azure.com/myorg"
  sql:
    dialect: "mysql"               # postgres | mysql | mssql | influxdb
  # ... (all tools documented inline as comments)
```

### Non-URL configurable fields

Beyond URL defaults, these non-URL fields are also YAML-configurable:

| Field | Tools | YAML key | Default |
|---|---|---|---|
| `auth_type` | `git` (GenericGitConfig) | `auth_type` | `"pat"` |
| `cloud` | `jira`, `confluence` | `cloud` | `false` |
| `use_bearer` | `xwiki` | `use_bearer` | `false` |
| `auth_type` | `email` | `auth_type` | `"basic"` (set in YAML, no code fallback) |
| `oauth_authority` | `email` | `oauth_authority` | `None` |
| `oauth_scope` | `email` | `oauth_scope` | `None` |
| `dialect` | `sql` | `dialect` | `""` (required at runtime) |

PyYAML parses unquoted `true`/`false` as Python bools, so `cloud: true` correctly yields `True`. `get_tool_default` return type is `Optional[Any]` to accommodate non-string values.

---

## Integration inventory

| Config class | TOOL_NAME | File | URL field | YAML key |
|---|---|---|---|---|
| `JiraConfig` | `jira` | `core/project_management/jira/models.py` | `url` | `url` |
| `ConfluenceConfig` | `confluence` | `core/project_management/confluence/models.py` | `url` | `url` |
| `XWikiConfig` | `xwiki` | `core/project_management/xwiki/models.py` | `url` | `url` |
| `GenericGitConfig` | `git` | `core/vcs/git/models.py` | `url` | `url` |
| `GenericAzureDevOpsConfig` | `azuredevops` | `azure_devops/generic/models.py` | `url` | `url` |
| `KeycloakConfig` | `keycloak` | `access_management/keycloak/models.py` | `base_url` | `base_url` |
| `ElasticConfig` | `elastic` | `data_management/elastic/models.py` | `url` | `url` |
| `SharePointConfig` | `sharepoint` | `data_management/sharepoint/models.py` | `url` | `url` |
| `SQLConfig` | `sql` | `data_management/sql/models.py` | — | `dialect` |
| `EmailToolConfig` | `email` | `notification/email/models.py` | `url` (SMTP) | `smtp_url` |
| `XrayConfig` | `xray` | `qa/xray/models.py` | `base_url` | `base_url` |
| `ZephyrConfig` | `zephyr` | `qa/zephyr/models.py` | `url` | `url` |
| `ServiceNowConfig` | `servicenow` | `itsm/servicenow/models.py` | `url` | `url` |
| `ReportPortalConfig` | `report_portal` | `report_portal/models.py` | `url` | `url` |
| `KubernetesConfig` | `kubernetes` | `cloud/kubernetes/models.py` | `url` | `url` |
| `SonarConfig` | `sonar` | `code/models.py` | `url` | `url` |

### Generic UI configs vs. per-tool credential configs

`GenericGitConfig` and `GenericAzureDevOpsConfig` are **new, generic UI-facing configs** that surface under `/v1/tools/configs` for credential entry without tying to a specific VCS host. The existing per-tool models (`GitlabConfig`, `GithubConfig`, `AzureDevOpsGitConfig`, `AzureDevOpsWorkItemConfig`, etc.) retain their hardcoded static placeholders and are **not changed in this branch**.

---

## Prerequisite changes

### ZephyrConfig — bare annotation → Field with default and placeholder

`src/codemie_tools/qa/zephyr/models.py` previously had `url: str` (bare annotation, no `Field`). Changed to `Field(default=_tool_default(...), ...)` — no longer pydantic-required; app enforces URL presence at runtime.

### SharePointConfig — new model

`SharePointCredentials` (`src/codemie/rest_api/models/settings.py`) is a plain `BaseModel` outside the toolkit hierarchy, not surfaced by `/tools/configs`. Created a new `SharePointConfig(CodeMieToolConfig)` in `src/codemie_tools/data_management/sharepoint/models.py`.

---

## Out of scope

- `ZephyrSquadConfig.DEFAULT_BASE_URL` — vendor SaaS constant, not user-configurable.
- `GitlabConfig`, `GithubConfig`, `AzureDevOpsGitConfig`, `AzureDevOpsWorkItemConfig`, `AzureDevOpsWikiConfig`, `AzureDevOpsTestPlanConfig` — retain static placeholders; generic configs cover UI credential entry needs.

---

## Testing

Two assertions per URL field:
1. Placeholder default: `schema["properties"][field]["placeholder"] == "<hint text from YAML>"`
2. Override via monkeypatch: `monkeypatch.setattr(customer_config, "tool_defaults", {tool: {key: value}})`

Additional assertions for non-URL fields (`auth_type`, `cloud`, `use_bearer`, `dialect`): default value check + monkeypatch override.
