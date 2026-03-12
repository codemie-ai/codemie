# LLM Provider Integration

## Quick Summary

Multi-cloud LLM provider integration supporting Azure OpenAI, AWS Bedrock, GCP Vertex AI, and Anthropic with environment-based configuration, rate limiting, cost optimization, and failover patterns.

**Category**: Integration | **Complexity**: Medium | **Prerequisites**: Provider credentials (Azure API key, AWS IAM, GCP service account), LangChain

---

## Multi-Provider Integration Overview

CodeMie supports direct integration with multiple LLM providers:
- **Unified Configuration**: Single interface for Azure OpenAI, AWS Bedrock, GCP Vertex AI, Anthropic
- **Environment-Based Selection**: Switch providers via `MODELS_ENV` variable
- **Model Categories**: Optimize costs with category-based model selection (code, chat, reasoning)
- **Streaming Support**: Real-time token streaming across all providers

### Direct Provider Integration

**Provider SDKs**: `langchain-anthropic`, `langchain-aws`, `langchain-google-vertexai`, `langchain-openai`
**Configuration**: Environment variable `MODELS_ENV` (azure/aws/gcp) selects provider
**Credentials**: Provider-specific authentication (API keys, IAM roles, service accounts)

**Import Pattern**:
```python
# Use provider-specific clients directly
from codemie.core.dependecies import get_llm_by_credentials

# Initialize LLM with provider configuration
llm = get_llm_by_credentials(
    llm_model="gpt-4o-2024-11-20",
    temperature=0.7,
    streaming=True
)
```

**Provider Support**:
- **Azure OpenAI**: Direct integration via `langchain-openai`
- **AWS Bedrock**: Direct integration via `langchain-aws`
- **GCP Vertex AI**: Direct integration via `langchain-google-vertexai`
- **Anthropic**: Direct API integration via `langchain-anthropic`

**Enterprise Note**: Advanced features (LiteLLM proxy integration, budget management, observability) available in `codemie-enterprise` package

## Provider Configuration

**Environment variable** `MODELS_ENV` selects config: `config/llms/llm-{MODELS_ENV}-config.yaml`

| Provider | MODELS_ENV | Config File | Authentication | Key Env Vars |
|----------|------------|-------------|----------------|--------------|
| Azure OpenAI | `azure` | `llm-azure-config.yaml` | API Key | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_URL`, `OPENAI_API_VERSION` |
| AWS Bedrock | `aws` | `llm-aws-config.yaml` | IAM | `AWS_BEDROCK_REGION`, `AWS_BEDROCK_MAX_RETRIES` |
| GCP Vertex AI | `gcp` | `llm-gcp-config.yaml` | Service Account | `GOOGLE_PROJECT_ID`, `GOOGLE_VERTEXAI_REGION` |

**YAML Schema**: See `config/llms/llm-azure-config.yaml` for structure (base_name, deployment_name, cost, max_output_tokens, features)

**Sources**: `src/codemie/configs/llm_config.py:124`, provider configs in `config/llms/`, `src/codemie/core/dependecies.py`

## Model Configuration Patterns

**Categories**: `GLOBAL`, `CHAT`, `CODE`, `DOCUMENTATION`, `REASONING` (see `src/codemie/configs/llm_config.py:39-53`)

**Selection**:
```python
# Category-based selection
default_model = llm_service.get_default_model_for_category("code")
deployment_name = llm_service.get_llm_deployment_name("", category="reasoning")
```

**Model Schema**: `base_name`, `deployment_name`, `max_output_tokens`, `features` (LLMFeatures), `cost` (CostConfig), `default_for_categories`

**Parameter filtering**: See `src/codemie/core/dependecies.py:186-201` (auto-adjusts temperature/streaming/max_tokens based on model features)

## Environment-Based Provider Selection

**Config switching**: `MODELS_ENV={azure|aws|gcp}` → loads `config/llms/llm-{MODELS_ENV}-config.yaml`

**Runtime provider detection**: See `src/codemie/core/dependecies.py:211-238` (factory pattern routes to provider-specific LLM initialization)

## Rate Limiting Strategies

### Provider-Specific Rate Limiting

Application-level rate limiting through provider SDKs:

**AWS Bedrock**:
```python
# src/codemie/core/dependecies.py:101-122
bedrock_client = boto3.client(
    "bedrock-runtime",
    config=Config(retries={"max_attempts": config.AWS_BEDROCK_MAX_RETRIES})
)
```

**Azure OpenAI**:
```python
# max_retries parameter in AzureChatOpenAI
AzureChatOpenAI(max_retries=config.AZURE_OPENAI_MAX_RETRIES, ...)
```

**GCP Vertex AI**:
```python
# max_retries in ChatVertexAI
ChatVertexAI(max_retries=config.GOOGLE_VERTEXAI_MAX_RETRIES, ...)
```

**Configuration**: All providers use `max_retries` parameter. See `src/codemie/core/dependecies.py`

---

## Error Handling & Retry Patterns

### Provider Failure Handling

**Retry logic with exponential backoff** built into provider SDKs:
- AWS Bedrock: Boto3 `Config(retries={"max_attempts": AWS_BEDROCK_MAX_RETRIES})`
- Azure OpenAI: `max_retries` parameter
- GCP Vertex AI: `max_retries` parameter

**Custom exception handling**: Provider-specific exceptions are caught and wrapped in appropriate application exceptions from `codemie.core.exceptions`.

## Cost Optimization Strategies

**Model Selection by Task**: Use category-based selection (cheaper models for simple tasks). Cost comparison (Azure):

| Model | Input | Output | Use Case |
|-------|-------|--------|----------|
| GPT-4o | $0.0025/1K | $0.01/1K | Complex |
| GPT-4o-mini | $0.000165/1K | $0.00066/1K | Simple |
| o1 | $0.015/1K | $0.06/1K | Reasoning |

**Prompt Caching**: Claude models use `SmartCacheCallbackHandler` (cache read ~10% of input cost). See `src/codemie/core/dependecies.py:148-162`

### Prompt Caching

**Anthropic Claude caching** via smart cache:
```python
# src/codemie/core/dependecies.py:148-162
if is_claude and not is_unsupported_bedrock_model:
    from langchain_anthropic_smart_cache import SmartCacheCallbackHandler

    cache_handler = SmartCacheCallbackHandler()
    llm.callbacks.append(cache_handler)
```

**Cache costs** (Claude models):
- Cache read: ~10% of input token cost
- Example: Claude 4.5 Sonnet cache read $0.0000003/token vs $0.000003/token input

Source: `src/codemie/core/dependecies.py:148-162`

### Cost Tracking

```python
# src/codemie/configs/llm_config.py:12-17
class CostConfig(BaseModel):
    input: float                            # Cost per input token
    output: float                           # Cost per output token
    input_cost_per_token_batches: Optional[float]  # Batch input cost
    output_cost_per_token_batches: Optional[float] # Batch output cost
    cache_read_input_token_cost: Optional[float]   # Cache read cost
```

Source: `src/codemie/configs/llm_config.py:12-17`

## Fallback Patterns

### Model Degradation

**Category fallback to global**:
```python
# src/codemie/service/llm_service/llm_service.py:183-210
def get_default_model_for_category(category: str) -> Optional[LLMModel]:
    # Try category-specific default
    category_default = next((m for m in models if m.is_default_for(category)), None)
    if category_default:
        return category_default

    # Fallback to GLOBAL default
    return next((m for m in models if m.is_default_for(ModelCategory.GLOBAL)), None)
```

Source: `src/codemie/service/llm_service/llm_service.py:183-210, 260-268`

### Provider Failover

Environment-based provider switching enables manual failover:

```bash
# Primary: Azure OpenAI
MODELS_ENV=azure

# Failover to AWS Bedrock
MODELS_ENV=aws

# Failover to GCP Vertex AI
MODELS_ENV=gcp
```

**Model availability** based on configured providers:
```python
# src/codemie/service/llm_service/llm_service.py
def get_allowed_models(user: User) -> ModelList:
    # Return configured models based on MODELS_ENV
    return ModelList(
        chat_models=self.get_all_llm_model_info(),
        embedding_models=self.get_all_embedding_model_info()
    )
```

## Streaming Response Patterns

**Default**: Enabled for all providers. Set via `streaming=True` parameter in `get_llm_by_credentials()`

**Callbacks**: Use `AgentStreamingCallback` or `ChainStreamingCallback` with `ThoughtQueue`. Key methods: `on_llm_start()`, `on_llm_new_token(token)`, `on_llm_end(response)`. See `src/codemie/agents/callbacks/agent_streaming_callback.py:19-80`

**Error Handling**: Same retry/fallback as non-streaming

## Examples

```python
# Example 1: Basic initialization
from codemie.core.dependecies import get_llm_by_credentials
llm = get_llm_by_credentials(llm_model="gpt-4o-2024-11-20", temperature=0.7)

# Example 2: Category-based selection
from codemie.service.llm_service.llm_service import llm_service
code_model = llm_service.get_llm_deployment_name("", category="code")
llm = get_llm_by_credentials(llm_model=code_model)

# Get default model for reasoning
reasoning_model_name = llm_service.get_llm_deployment_name("", category="reasoning")
llm = get_llm_by_credentials(llm_model=reasoning_model_name)
```

### Example 3: AWS Bedrock with Claude

```python
# config/llms/llm-aws-config.yaml active (MODELS_ENV=aws)
from codemie.core.dependecies import get_llm_by_credentials

# Initialize Claude on Bedrock
llm = get_llm_by_credentials(
    llm_model="claude-4-5-sonnet",
    temperature=0.5,
    streaming=True
)

# React JSON agent for Bedrock (no tool-calling support on some models)
from langchain.agents import create_json_chat_agent
agent = create_json_chat_agent(llm, tools, system_prompt)
```

Source: `src/codemie/agents/assistant_agent.py:108-156`

### Example 4: GCP Vertex AI with Gemini

```python
# config/llms/llm-gcp-config.yaml active (MODELS_ENV=gcp)
from codemie.core.dependecies import get_llm_by_credentials

# Initialize Gemini on Vertex AI
llm = get_llm_by_credentials(
    llm_model="gemini-2.5-pro",
    temperature=0.3,
    streaming=True
)
```

### Example 5: Custom Headers for Long Context

```yaml
# config/llms/llm-aws-config.yaml
- base_name: "claude-4-sonnet-1m"
  deployment_name: "us.anthropic.claude-sonnet-4-20250514-v1:0"
  configuration:
    client_headers:
      anthropic_beta:
        - context-1m-2025-08-07
```

```python
# Headers automatically applied from config
llm = get_llm_by_credentials(llm_model="claude-4-sonnet-1m")
```

Source: `config/llms/llm-aws-config.yaml:50-64`, `src/codemie/core/dependecies.py:240-260`

## Verification

```python
# Test provider connectivity
from codemie.core.dependecies import get_llm_by_credentials
llm = get_llm_by_credentials(llm_model="gpt-4o-2024-11-20")
response = llm.invoke("Hello")  # Should return response

# Test AWS Bedrock
llm = get_llm_by_credentials(llm_model="claude-4-5-sonnet")
response = llm.invoke("Hello")

# Test GCP Vertex AI
llm = get_llm_by_credentials(llm_model="gemini-2.5-pro")
response = llm.invoke("Hello")
```

### Test Model Availability

```python
from codemie.service.llm_service.llm_service import llm_service

# Get available models
models = llm_service.get_all_llm_model_info()
print(f"Available models: {[m.base_name for m in models]}")

# Get embedding models
embeddings = llm_service.get_all_embedding_model_info()
print(f"Embedding models: {[e.base_name for e in embeddings]}")
```

## Troubleshooting

### Authentication Failures

**Azure OpenAI 401 Unauthorized**:
- Verify `AZURE_OPENAI_API_KEY` is set
- Check `AZURE_OPENAI_URL` endpoint is correct
- Verify `OPENAI_API_VERSION` is supported

**AWS Bedrock AccessDeniedException**:
- Verify IAM credentials have `bedrock:InvokeModel` permission
- Check `AWS_BEDROCK_REGION` matches model availability
- Verify model ID exists in region

**GCP Vertex AI 403 Forbidden**:
- Verify service account has `aiplatform.endpoints.predict` permission
- Check `GOOGLE_PROJECT_ID` and `GOOGLE_VERTEXAI_REGION` are correct
- Verify Application Default Credentials are configured

### Rate Limit Errors

**Provider Rate Limits**:
- Azure: 429 Too Many Requests → Retry with exponential backoff
- AWS: Throttling exception → Increase `AWS_BEDROCK_MAX_RETRIES`
- GCP: ResourceExhausted → Check quota limits in GCP console

### Model Access Errors

**Model Not Found**:
- Verify model name matches config: `llm_service.get_model_details(model_name)`
- Check `enabled: true` in YAML config
- Verify `MODELS_ENV` points to correct config file

**Model Not Available**:
- Azure: Verify deployment exists in Azure OpenAI resource
- AWS: Check model availability in `AWS_BEDROCK_REGION`
- GCP: Verify model access in project

---

## References

**Source Files**:
- **LLM Config**: `src/codemie/configs/llm_config.py` (Model configuration schema)
- **Azure Config**: `config/llms/llm-azure-config.yaml` (Azure OpenAI models)
- **AWS Config**: `config/llms/llm-aws-config.yaml` (AWS Bedrock models)
- **GCP Config**: `config/llms/llm-gcp-config.yaml` (GCP Vertex AI models)
- **LLM Service**: `src/codemie/service/llm_service/llm_service.py` (Model selection, category defaults)
- **Dependencies**: `src/codemie/core/dependecies.py` (LLM initialization factory)

**Related Guides**: [Cloud Integrations](./cloud-integrations.md), [External Services](./external-services.md), [LangChain Agents](../agents/langchain-agent-patterns.md), [Service Layer](../architecture/service-layer-patterns.md)

**External Resources**:
- [Azure OpenAI Service](https://learn.microsoft.com/en-us/azure/ai-services/openai/)
- [AWS Bedrock](https://docs.aws.amazon.com/bedrock/)
- [GCP Vertex AI](https://cloud.google.com/vertex-ai/docs/generative-ai/learn/overview)
- [LangChain Documentation](https://python.langchain.com/docs/)
