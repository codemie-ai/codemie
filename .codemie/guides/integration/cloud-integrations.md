# Cloud Platform Integrations

## Quick Summary

Multi-cloud integration patterns for AWS, Azure, and GCP services. Covers storage (S3/Blob/GCS), encryption (KMS/Key Vault/Cloud KMS), and LLM providers (Bedrock/Azure OpenAI/Vertex AI) with environment-based configuration switching.

**Category**: Integration | **Complexity**: Medium | **Prerequisites**: Cloud provider accounts, service credentials, dependency injection patterns

---

## Cloud Services Overview

| Cloud | Storage | Encryption | LLM Provider | Auth Method | Credentials |
|-------|---------|------------|--------------|-------------|-------------|
| **AWS** | S3 | KMS | Bedrock | IAM | boto3 default chain (env vars, ~/.aws/credentials, instance metadata) |
| **Azure** | Blob Storage | Key Vault | Azure OpenAI | Service Principal | DefaultAzureCredential (env vars, managed identity, az login) |
| **GCP** | Cloud Storage | Cloud KMS | Vertex AI | Service Account | Application Default Credentials (GOOGLE_APPLICATION_CREDENTIALS) |

**Config**: `src/codemie/configs/config.py:74,148-179,188` | **Factories**: `encryption_factory.py`, `repository_factory.py`

---

## Configuration Pattern

### Factory-Based Provider Selection

```python
# Encryption - controlled by ENCRYPTION_TYPE env var
from codemie.service.encryption.encryption_factory import EncryptionFactory
service = EncryptionFactory.get_current_encryption_service()  # Auto-selects from config

# File Storage - controlled by FILES_STORAGE_TYPE env var
from codemie.repository.repository_factory import FileRepositoryFactory
repo = FileRepositoryFactory.get_current_repository()  # Defaults to 'filesystem'
```

**Source**: `encryption_factory.py:22-55`, `repository_factory.py:17-54`

### Configuration Variables

| Provider | Purpose | Key Variables | Defaults/Notes |
|----------|---------|---------------|----------------|
| **AWS** | Encryption + Storage | `AWS_KMS_KEY_ID`, `AWS_KMS_REGION`, `AWS_S3_REGION`, `AWS_S3_BUCKET_NAME`, `AWS_BEDROCK_REGION` | Bedrock: 5 retries, 60s timeout |
| **Azure** | Encryption + Storage | `AZURE_KEY_VAULT_URL`, `AZURE_KEY_NAME`, `AZURE_STORAGE_CONNECTION_STRING`, `AZURE_STORAGE_ACCOUNT_NAME` | Service principal: TENANT_ID, CLIENT_ID, CLIENT_SECRET |
| **GCP** | Encryption + Storage | `GOOGLE_KMS_PROJECT_ID`, `GOOGLE_KMS_KEY_RING`, `GOOGLE_KMS_CRYPTO_KEY`, `GOOGLE_KMS_REGION`, `FILES_STORAGE_GCP_REGION` | Key ring/crypto key default to "codemie" |
| **Provider Selection** | Runtime switching | `ENCRYPTION_TYPE` (plain/aws/azure/gcp/base64/vault), `FILES_STORAGE_TYPE` (filesystem/aws/azure/gcp) | - |

**Source**: `config.py:74,148-179,188`

---

## AWS Integration Patterns

### KMS Encryption Example

```python
from codemie.service.encryption.aws_encryption_service import AWSKMSEncryptionService

service = AWSKMSEncryptionService()
encrypted = service.encrypt("sensitive-data")  # KMS encrypt with base64 encoding
decrypted = service.decrypt(encrypted)         # KMS decrypt
```

**Implementation**: `aws_encryption_service.py:1-32`
- Auth: boto3 IAM credential chain (env vars → ~/.aws/credentials → instance metadata)
- Base64 encodes ciphertext for storage
- Requires `AWS_KMS_KEY_ID` and optional `AWS_KMS_REGION`

### S3 File Storage Example

```python
from codemie.repository.aws_file_repository import AWSFileRepository

repo = AWSFileRepository(region_name="us-west-2", root_bucket="my-bucket")
file = repo.write_file(name="doc.pdf", mime_type="application/pdf", owner="user123", content=pdf_bytes)
retrieved = repo.read_file(file_name="doc.pdf", owner="user123")  # Path: user123/doc.pdf
```

**Implementation**: `aws_file_repository.py:11-46`
- Multi-tenancy: Files organized by owner prefix (`{owner}/{filename}`)
- Auto-detects ContentType on read

### Bedrock LLM Integration

```python
from codemie.service.aws_bedrock.base_bedrock_service import BaseBedrockService

# Abstract base for Bedrock services (agents, knowledge bases, guardrails, flows)
class BaseBedrockService(ABC):
    @staticmethod
    @abstractmethod
    def get_all_settings_overview(user: User, page: int, per_page: int):
        pass

    @staticmethod
    @abstractmethod
    def list_main_entities(user: User, setting_id: str, page: int, per_page: int,
                          next_token: Optional[str] = None) -> tuple[List[dict], Optional[str]]:
        return [], None

    @staticmethod
    @abstractmethod
    def get_main_entity_detail(user: User, main_entity_id: str, setting_id: str) -> dict:
        pass
```

**Implementations**: `BedrockAgentService`, `BedrockKnowledgeBaseService`, `BedrockGuardrailService`, `BedrockFlowService`
**Pattern**: Abstract base class with service-specific implementations
**Usage**: Bedrock LLM calls handled via direct provider integration (see [LLM Provider Integration](./llm-providers.md))

Source: `src/codemie/service/aws_bedrock/base_bedrock_service.py:14-60`

---

## Azure Integration Patterns

### Key Vault Encryption Example

```python
from codemie.service.encryption.azure_encryption_service import AzureKMSEncryptionService

service = AzureKMSEncryptionService()
encrypted = service.encrypt("sensitive-data")  # RSA-OAEP encryption with base64 encoding
decrypted = service.decrypt(encrypted)
```

**Implementation**: `azure_encryption_service.py:1-39`
- Auth: `DefaultAzureCredential` (env vars → managed identity → az login → VS → PowerShell)
- Algorithm: RSA-OAEP asymmetric encryption
- Requires `AZURE_KEY_VAULT_URL` and `AZURE_KEY_NAME`

### Blob Storage Example

```python
from codemie.repository.azure_file_repository import AzureFileRepository

repo = AzureFileRepository(connection_string=conn_str)  # Or use storage_account_name with DefaultAzureCredential
file = repo.write_file(name="doc.pdf", mime_type="application/pdf", owner="user123", content=pdf_bytes)
# Auto-creates container named "user123" if missing
```

**Implementation**: `azure_file_repository.py:10-44`
- Multi-tenancy: Each owner gets dedicated container (auto-created)
- Overwrites existing blobs by default

### Azure OpenAI Integration

Azure OpenAI models configured in `config/llms/llm-azure-config.yaml`. See [LLM Provider Integration Guide](./llm-providers.md) for detailed configuration patterns.

---

## GCP Integration Patterns

### Cloud KMS Encryption Example

```python
from codemie.service.encryption.gcp_encryption_service import GCPKMSEncryptionService

service = GCPKMSEncryptionService()
encrypted = service.encrypt("sensitive-data")  # Cloud KMS encrypt with base64 encoding
decrypted = service.decrypt(encrypted)
```

**Implementation**: `gcp_encryption_service.py:1-34`
- Auth: Application Default Credentials (`GOOGLE_APPLICATION_CREDENTIALS` → GCE metadata → gcloud CLI)
- Key path: `projects/{project}/locations/{region}/keyRings/{keyring}/cryptoKeys/{key}`
- Defaults: Key ring/crypto key default to "codemie"

### Cloud Storage Example

```python
from codemie.repository.gcp_file_repository import GCPFileRepository

repo = GCPFileRepository()
file = repo.write_file(name="doc.pdf", mime_type="application/pdf", owner="user123", content=pdf_bytes)
# Auto-creates bucket named "user123" in FILES_STORAGE_GCP_REGION (default: "US")
```

**Implementation**: `gcp_file_repository.py:10-57`
- Multi-tenancy: One bucket per owner (auto-created)
- Charset detection for text MIME types

### Vertex AI Integration

GCP Vertex AI models configured in `config/llms/llm-gcp-config.yaml`. Supports both Vertex AI native models and Claude on Vertex AI.

Configuration:
- `GOOGLE_VERTEXAI_REGION`: Region for Vertex AI models
- `GOOGLE_CLAUDE_VERTEXAI_REGION`: Region for Claude on Vertex AI

See [LLM Provider Integration Guide](./llm-providers.md) for detailed patterns.

---

## LLM Provider Integration

CodeMie supports direct integration with multiple cloud LLM providers:

```python
from codemie.core.dependecies import get_llm_by_credentials

# Initialize LLM with provider-specific configuration
llm = get_llm_by_credentials(
    llm_model="gpt-4o-2024-11-20",
    temperature=0.7,
    streaming=True
)
```

**Provider Support**:
- **AWS Bedrock**: Claude, Titan, Llama models via `langchain-aws`
- **Azure OpenAI**: GPT-4, GPT-3.5, embeddings via `langchain-openai`
- **GCP Vertex AI**: Gemini, Claude on Vertex AI via `langchain-google-vertexai`
- **Anthropic**: Direct API integration via `langchain-anthropic`

**Configuration**: `config/llms/` directory with per-provider model configs
**Environment Selection**: `MODELS_ENV` variable switches between providers

**Enterprise Note**: Advanced features (LiteLLM proxy integration, budget management) available in `codemie-enterprise` package

Source: `src/codemie/core/dependecies.py`, `src/codemie/configs/llm_config.py`

---

## Authentication Patterns

| Cloud | Credential Chain (in order) | CodeMie Pattern | Required Permissions |
|-------|---------------------------|-----------------|----------------------|
| **AWS** | 1. Env vars (ACCESS_KEY_ID, SECRET_ACCESS_KEY)<br>2. ~/.aws/credentials<br>3. IAM role (EC2/ECS/Lambda)<br>4. Container credentials | boto3 auto-discovery | KMS: Encrypt/Decrypt<br>S3: GetObject/PutObject<br>Bedrock: InvokeModel |
| **Azure** | 1. Env vars (TENANT_ID, CLIENT_ID, CLIENT_SECRET)<br>2. Managed Identity<br>3. az login<br>4. VS/PowerShell | `DefaultAzureCredential()` | Key Vault: Crypto User role<br>Blob: Data Contributor<br>OpenAI: Custom role |
| **GCP** | 1. GOOGLE_APPLICATION_CREDENTIALS<br>2. Attached service account (GCE/GKE)<br>3. gcloud auth | Application Default Credentials | KMS: useToEncrypt/Decrypt<br>Storage: objects.create/get<br>Vertex: endpoints.predict |

---

## Usage Examples

**Switch Encryption Providers** (environment-based):
```bash
# Dev: plain, Staging: GCP, Production: AWS
export ENCRYPTION_TYPE=aws AWS_KMS_KEY_ID=... AWS_KMS_REGION=us-east-1
# Code stays identical
service = EncryptionFactory.get_current_encryption_service()
encrypted = service.encrypt("sensitive-data")
```

**Multi-Cloud File Storage**:
```bash
export FILES_STORAGE_TYPE=aws AWS_S3_REGION=us-west-2 AWS_S3_BUCKET_NAME=my-files
```
```python
repo = FileRepositoryFactory.get_current_repository()  # Cloud-agnostic code
file = repo.write_file(name="doc.pdf", mime_type="application/pdf", owner="user123", content=pdf_bytes)
```

**Service Layer** (auto-uses factories): `FileService.get_file_object()` internally uses `FileRepositoryFactory` (see `file_service.py:6-24`)

---

## Verification

**Test Encryption**:
```bash
# Set provider and credentials
export ENCRYPTION_TYPE=aws
export AWS_KMS_KEY_ID=your-key-id
export AWS_KMS_REGION=us-east-1

# Python test
python -c "
from codemie.service.encryption.encryption_factory import EncryptionFactory
service = EncryptionFactory.get_current_encryption_service()
encrypted = service.encrypt('test-data')
decrypted = service.decrypt(encrypted)
assert decrypted == 'test-data'
print('Encryption service working')
"
```

**Test File Storage**:
```bash
# Set provider and credentials
export FILES_STORAGE_TYPE=gcp
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
export FILES_STORAGE_GCP_REGION=US

# Python test
python -c "
from codemie.repository.repository_factory import FileRepositoryFactory
repo = FileRepositoryFactory.get_current_repository()
file = repo.write_file('test.txt', 'text/plain', 'test-owner', b'test content')
retrieved = repo.read_file('test.txt', 'test-owner')
assert retrieved.content == b'test content'
print('File storage working')
"
```

**Verify LLM Configuration**:
```bash
# Check LLM configs exist
ls -l config/llms/

# Verify provider configs loaded
python -c "
from codemie.configs import config
print(f'Models Environment: {config.MODELS_ENV}')
print(f'Templates: {config.LLM_TEMPLATES_ROOT}')
"
```

**Prerequisites**: Set env vars (`ENCRYPTION_TYPE`, `FILES_STORAGE_TYPE`, provider credentials) before testing

---

## Troubleshooting

### Authentication Failures

**AWS "Unable to locate credentials"**:
- Verify IAM credentials in environment or ~/.aws/credentials
- Check IAM role attached to EC2/ECS/Lambda if running on AWS
- Ensure `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` are set for local development

**Azure "DefaultAzureCredential failed"**:
- Run `az login` to authenticate Azure CLI
- Verify service principal environment variables: `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`
- Check managed identity is enabled for Azure resources

**GCP "Could not automatically determine credentials"**:
- Set `GOOGLE_APPLICATION_CREDENTIALS` to service account key path
- Verify service account key JSON is valid
- Check Application Default Credentials: `gcloud auth application-default login`

### Permission Errors

**AWS KMS "AccessDeniedException"**:
- Verify IAM user/role has `kms:Encrypt` and `kms:Decrypt` permissions
- Check KMS key policy allows the principal
- Confirm `AWS_KMS_KEY_ID` is correct and accessible

**Azure Key Vault "Forbidden"**:
- Assign "Key Vault Crypto User" role to service principal/user
- Verify Key Vault firewall allows access
- Check `AZURE_KEY_VAULT_URL` and `AZURE_KEY_NAME` are correct

**GCP Cloud KMS "Permission denied"**:
- Grant `cloudkms.cryptoKeyVersions.useToEncrypt` and `useToDecrypt` permissions
- Verify key ring and crypto key exist in specified project/region
- Check `GOOGLE_KMS_PROJECT_ID`, `GOOGLE_KMS_KEY_RING`, `GOOGLE_KMS_CRYPTO_KEY` configuration

### Configuration Issues

**Factory returns wrong provider**:
- Check `ENCRYPTION_TYPE` or `FILES_STORAGE_TYPE` environment variable value
- Verify enum values match: `aws`, `azure`, `gcp` (lowercase)
- Restart application after changing environment variables

**Missing required configuration**:
- Encryption: Verify `*_KEY_ID/*_KEY_NAME` and `*_REGION` variables set
- Storage: Verify `*_BUCKET_NAME/*_STORAGE_ACCOUNT_NAME` and region variables set
- LLM: Check `MODELS_ENV` and provider-specific credentials

---

## References

**Source Files**:
- **Encryption Factory**: `src/codemie/service/encryption/encryption_factory.py`
- **File Repository Factory**: `src/codemie/repository/repository_factory.py`
- **AWS Services**:
  - KMS: `src/codemie/service/encryption/aws_encryption_service.py`
  - S3: `src/codemie/repository/aws_file_repository.py`
  - Bedrock: `src/codemie/service/aws_bedrock/base_bedrock_service.py`
- **Azure Services**:
  - Key Vault: `src/codemie/service/encryption/azure_encryption_service.py`
  - Blob Storage: `src/codemie/repository/azure_file_repository.py`
- **GCP Services**:
  - Cloud KMS: `src/codemie/service/encryption/gcp_encryption_service.py`
  - Cloud Storage: `src/codemie/repository/gcp_file_repository.py`
- **Configuration**: `src/codemie/configs/config.py`

**Related Guides**: [Service Layer Patterns](../architecture/service-layer-patterns.md), [Repository Patterns](../data/repository-patterns.md), [Elasticsearch Integration](../data/elasticsearch-integration.md), [LLM Providers](llm-providers.md), [External Services](external-services.md)

**External Resources**:
- [AWS SDK for Python (Boto3)](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html)
- [Azure SDK for Python](https://learn.microsoft.com/en-us/python/api/overview/azure/)
- [Google Cloud Client Libraries](https://cloud.google.com/python/docs/reference)
- [AWS KMS Best Practices](https://docs.aws.amazon.com/kms/latest/developerguide/best-practices.html)
- [Azure Key Vault Security](https://learn.microsoft.com/en-us/azure/key-vault/general/security-features)
- [GCP Cloud KMS Key Management](https://cloud.google.com/kms/docs/key-management)
