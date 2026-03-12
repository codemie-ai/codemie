# Configuration Management Patterns

## Quick Summary

Configuration management patterns for environment variables, Pydantic settings, YAML configs, Jinja2 templates, and customer-specific overrides. Enables environment-based configuration (dev/staging/prod), type-safe settings validation, and secure credential handling following CodeMie conventions.

**Category**: Development/Configuration
**Complexity**: Medium
**Prerequisites**: Python environment variables, Pydantic, YAML, Jinja2

## Prerequisites

- **Python `os.environ`**: Environment variable access
- **python-dotenv**: Load `.env` files automatically
- **Pydantic v2**: BaseSettings for configuration classes
- **pydantic-settings**: YamlConfigSettingsSource, SettingsConfigDict
- **YAML/JSON**: Configuration file formats
- **Jinja2**: Template rendering for customer configurations
- **KMS SDKs**: AWS/Azure/GCP for secret encryption (optional)

---

## Environment Variables

### Loading from .env

**Pattern** (src/codemie/configs/config.py:5-6, 290)

```python
from dotenv import find_dotenv, load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

class Config(BaseSettings):
    ENV: str = "local"
    LOG_LEVEL: str = "INFO"
    POSTGRES_PASSWORD: str = "password"
    AZURE_OPENAI_API_KEY: str = ""

    model_config = SettingsConfigDict(
        env_file=find_dotenv(".env", raise_error_if_not_found=False),
        extra="ignore"
    )

load_dotenv(find_dotenv(".env", raise_error_if_not_found=False))
config = Config()
```

### Naming & Type Conversion

| Convention | Example | Type Conversion |
|------------|---------|-----------------|
| **UPPERCASE_WITH_UNDERSCORES** | `AZURE_OPENAI_API_KEY` | All env vars |
| **Prefix by service** | `POSTGRES_HOST`, `POSTGRES_PORT` | Group related |
| **Auto type conversion** | `POSTGRES_PORT: int = 5432` | `"5432"` → `5432` |
| **Boolean conversion** | `ENABLED: bool = False` | `"true"` → `True` |

---

## Config Directory Structure

### Layout

```
config/
├── llms/                              # LLM provider configurations
│   ├── llm-aws-config.yaml           # AWS Bedrock models
│   ├── llm-azure-config.yaml         # Azure OpenAI models
│   ├── llm-gcp-config.yaml           # Google Vertex AI models
│   └── llm-dial-config.yaml          # EPAM AI DIAL models
├── customer/                          # Customer-specific overrides
│   └── customer-config.yaml          # UI components, features
├── templates/                         # Jinja2 templates
│   ├── assistant/                    # Assistant templates
│   └── workflow/                     # Workflow templates
├── datasources/                       # Datasource configurations
├── categories/                        # Assistant category configs
└── authorized_applications/           # Authorized app configs
```

### Directory References in Code

(src/codemie/configs/config.py:62-69)

```python
class Config(BaseSettings):
    PROJECT_ROOT: Path = Path(__file__).absolute().parents[1]
    LLM_TEMPLATES_ROOT: Path = PROJECT_ROOT.parents[2] / "config/llms"
    DATASOURCES_CONFIG_DIR: Path = PROJECT_ROOT.parents[2] / "config/datasources"
    ASSISTANT_TEMPLATES_DIR: Path = PROJECT_ROOT.parents[2] / "config/templates/assistant"
    WORKFLOW_TEMPLATES_DIR: Path = PROJECT_ROOT.parents[2] / "config/templates/workflow"
    CUSTOMER_CONFIG_DIR: Path = PROJECT_ROOT.parents[2] / "config/customer"
```

---

## Pydantic Settings

### BaseSettings Pattern

(src/codemie/configs/config.py:11-18)

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Config(BaseSettings):
    APP_VERSION: str = "0.16.0"
    ENV: str = "local"
    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
```

### Nested Models with Validation

(src/codemie/configs/llm_config.py:68-91)

```python
from pydantic import BaseModel, model_validator

class CostConfig(BaseModel):
    input: float
    output: float
    cache_read_input_token_cost: Optional[float] = None

class LLMModel(BaseModel):
    base_name: str
    deployment_name: str
    enabled: bool
    cost: Optional[CostConfig] = None

    @model_validator(mode='after')
    def populate_default_field(self):
        if ModelCategory.GLOBAL in self.default_for_categories:
            self.default = True
        return self
```

### YAML Configuration Loading

(src/codemie/configs/llm_config.py:104-121)

```python
from pydantic_settings import BaseSettings, YamlConfigSettingsSource

class LLMYamlSettings(BaseSettings):
    yaml_file: Path

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings,
        dotenv_settings, file_secret_settings
    ):
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(cls, init_settings.init_kwargs["yaml_file"])
        )

class LLMConfig(LLMYamlSettings):
    llm_models: list[LLMModel]
    embeddings_models: list[LLMModel]
```

---

## Environment-Based Configuration

### Environment Types

| Environment | ENV Value | Purpose | Config Source |
|-------------|-----------|---------|---------------|
| **Local** | `local` | Development | `.env` file |
| **Docker** | `local` | Containerized dev | Environment vars |
| **Staging** | `staging` | Pre-production testing | Cloud env vars |
| **Production** | `production` | Live system | Cloud KMS + env vars |

### Environment Switching

```python
ENV: str = "local"  # Set via environment variable

# Conditional configuration based on environment
if config.ENV == "local":
    # Use local file paths, permissive settings
    FILES_STORAGE_TYPE: Literal["filesystem", "aws", "azure", "gcp"] = "filesystem"
else:
    # Use cloud storage, strict security
    FILES_STORAGE_TYPE = "aws"  # or "azure", "gcp"
```

### Model Environment Selection

(src/codemie/configs/config.py:19)

```python
MODELS_ENV: str = "dial"  # Determines which LLM config to load

# Loads: config/llms/llm-dial-config.yaml
# Alternatives: "aws", "azure", "gcp"
```

---

## Customer-Specific Configuration

### Customer Config File

(config/customer/customer-config.yaml)

```yaml
---
components:
  - id: "videoPortal"
    settings:
      name: "EPAM Video Portal"
      enabled: false
      url: "https://example-video-portal.com"
  - id: "mcpConnect"
    settings:
      enabled: true
  - id: "defaultConversationAssistant"
    settings:
      enabled: true
      slug: "ai-run-chatbot"
```

### Loading Customer Config

Customer configurations override defaults, enabling/disabling features per deployment.

---

## Template System

### Jinja2 Template Rendering

(src/codemie/workflows/jinja_template_renderer.py:17-21, src/codemie/workflows/utils/html_utils.py:3-6)

```python
from jinja2 import Template, SandboxedEnvironment

# Basic usage
template = Template('<div>{{ content }}</div>')
rendered = template.render(content="Dynamic content")

# Secure rendering for untrusted templates
env = SandboxedEnvironment()
safe_template = env.from_string(user_provided_template)
```

---

## Configuration Loading Patterns

### Singleton Pattern

(src/codemie/configs/config.py:290-293)

```python
# Module-level singleton
load_dotenv(find_dotenv(".env", raise_error_if_not_found=False))
config = Config()

# Import everywhere
from codemie.configs import config
```

### YAML Config Loading

```python
from codemie.configs.llm_config import LLMConfig

yaml_path = config.LLM_TEMPLATES_ROOT / f"llm-{config.MODELS_ENV}-config.yaml"
llm_config = LLMConfig(yaml_file=yaml_path)
```

### Lazy Loading with Cache

```python
@lru_cache(maxsize=1)
def get_customer_config():
    with open(config.CUSTOMER_CONFIG_DIR / "customer-config.yaml") as f:
        return yaml.safe_load(f)
```

---

## Secret Management

### Environment Variables for Credentials

**NEVER Hardcode Secrets** (src/codemie/configs/config.py:25-45)

```python
# ✅ CORRECT: Load from environment
AZURE_OPENAI_API_KEY: str = ""
ANTHROPIC_API_KEY: str = ""
POSTGRES_PASSWORD: str = "password"
AWS_KMS_KEY_ID: str = ""
GOOGLE_PROJECT_ID: str = ""

# ❌ WRONG: Hardcoded credentials
AZURE_OPENAI_API_KEY = "sk-actual-key-here"  # NEVER DO THIS
```

### Credential Redaction

(src/codemie/configs/config.py:297-318)

```python
def to_safe_dict(self):
    """Mask sensitive information before logging"""
    sensitive_keywords = ['key', 'password', 'secret', 'token']
    config_dict = self.model_dump()
    safe_dict = {}

    for k, v in config_dict.items():
        if any(k.lower().endswith(kw) for kw in sensitive_keywords):
            safe_dict[k] = "******"  # Mask
        else:
            safe_dict[k] = v

    return safe_dict
```

### KMS Integration

Reference: [Security Patterns - KMS Integration](./security-patterns.md)

```python
# AWS KMS
AWS_KMS_KEY_ID: str = ""
AWS_KMS_REGION: str = ""

# Azure Key Vault
AZURE_KEY_VAULT_URL: str = ""
AZURE_KEY_NAME: str = ""

# Google Cloud KMS
GOOGLE_KMS_PROJECT_ID: str = GOOGLE_PROJECT_ID
GOOGLE_KMS_CRYPTO_KEY: str = "codemie"

# HashiCorp Vault
VAULT_URL: str = ""
VAULT_TOKEN: str = ""
ENCRYPTION_TYPE: str = "plain"  # or "aws_kms", "azure_kms", "gcp_kms", "vault"
```

---

## Configuration Testing

(tests/codemie/configs/test_llm_config.py:5-32)

```python
import pytest

@pytest.fixture
def llm_config_yaml(tmp_path):
    yaml_content = 'llm_models:\n  - base_name: "model-a"\n    enabled: true'
    yaml_file = tmp_path / "llm_config.yaml"
    yaml_file.write_text(yaml_content)
    return yaml_file

def test_llm_config_loading(llm_config_yaml):
    config = LLMConfig(yaml_file=llm_config_yaml)
    assert config.llm_models[0].base_name == 'model-a'

def test_env_override(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "test_password")
    config = Config()
    assert config.POSTGRES_PASSWORD == "test_password"

def test_validation():
    with pytest.raises(ValidationError):
        Config(POSTGRES_PORT="invalid")
```

---

## Complete Example

### .env File

```bash
ENV=local
LOG_LEVEL=INFO
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_PASSWORD=secure_password
AZURE_OPENAI_API_KEY=your_key_here
MODELS_ENV=dial
```

### YAML Configuration

(config/llms/llm-aws-config.yaml:1-24)

```yaml
llm_models:
  - base_name: "claude-3-5-sonnet"
    deployment_name: "anthropic.claude-3-5-sonnet-20240620-v1:0"
    enabled: true
    provider: "aws_bedrock"
    max_output_tokens: 4096
    cost:
      input: 0.000003
      output: 0.000015
embeddings_models:
  - base_name: "amazon.titan-embed-text-v2:0"
    deployment_name: "amazon.titan-embed-text-v2:0"
    enabled: true
```

### Usage

```python
from codemie.configs import config

print(config.ENV)  # "local"
print(config.POSTGRES_PORT)  # 5432 (int)
```

---

## Anti-Patterns

| Anti-Pattern | ❌ Wrong | ✅ Correct |
|--------------|---------|-----------|
| **Hardcoded secrets** | `PASSWORD = "secret"` | `PASSWORD: str = ""  # From .env` |
| **No validation** | `config_dict = {"port": "invalid"}` | `class Config(BaseSettings): port: int` |
| **Environment leakage** | `if prod: DB = "localhost"` | `DB = config.POSTGRES_HOST` |
| **Tight coupling** | `os.getenv("PASSWORD")` everywhere | `from codemie.configs import config` |

---

## Verification & Troubleshooting

### Verification

```python
# Load and inspect
from codemie.configs import config
print(config.model_dump())

# Check YAML
from codemie.configs.llm_config import LLMConfig
llm_config = LLMConfig(yaml_file="config/llms/llm-dial-config.yaml")

# Verify secret redaction
print(config.to_safe_dict())  # Passwords show "******"
```

### Common Issues

| Problem | Solution |
|---------|----------|
| **Config not loading** | Verify `.env` exists, use `load_dotenv()` |
| **YAML parse errors** | Validate YAML syntax with `yamllint` |
| **Type conversion fails** | Check env var types match declarations |
| **Missing secrets** | Verify env vars set in deployment |

---

## Next Steps

- **Security**: [Security Patterns - Credential Management](./security-patterns.md)
- **Logging**: [Logging Patterns - Logger Configuration](./logging-patterns.md)
- **Testing**: [Testing Patterns - Configuration Testing](../testing/testing-patterns.md)
- **Error Handling**: [Error Handling - Configuration Errors](./error-handling.md)

---

## References

### Source Files

- `src/codemie/configs/config.py:6-290` - Main configuration class with BaseSettings
- `src/codemie/configs/llm_config.py:104-121` - YAML configuration loading with YamlConfigSettingsSource
- `src/codemie/workflows/jinja_template_renderer.py:17-21` - Jinja2 template rendering
- `config/llms/` - LLM provider YAML configurations (AWS, Azure, GCP, DIAL)
- `config/customer/customer-config.yaml` - Customer-specific feature toggles
- `config/templates/` - Jinja2 templates for assistants and workflows
- `tests/codemie/configs/test_llm_config.py:5-46` - Configuration testing patterns

### Related Documentation

- [Security Patterns](./security-patterns.md) - Secret management, KMS integration
- [Logging Patterns](./logging-patterns.md) - Logger configuration, environment-based logging
- [Testing Patterns](../testing/testing-patterns.md) - Configuration testing, fixtures, mocks
- [Error Handling](./error-handling.md) - Configuration error handling

### External Resources

- [Pydantic Settings Documentation](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [python-dotenv Documentation](https://github.com/theskumar/python-dotenv)
- [Jinja2 Template Documentation](https://jinja.palletsprojects.com/)
