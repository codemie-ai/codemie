# Security Patterns

## Quick Summary

Security patterns for handling credentials, encryption, input validation, SQL injection prevention, JWT validation, and authentication/authorization in CodeMie. Follow these patterns to implement security controls consistently following industry best practices and OWASP guidelines.

**Category**: Development/Security
**Complexity**: Medium
**Prerequisites**: Pydantic, FastAPI, SQLModel, KMS SDKs (AWS/Azure/GCP), python-jose

## Prerequisites

- **Pydantic v2**: Type validation and request models
- **FastAPI**: Web framework with built-in security utilities
- **SQLModel**: ORM with parameterized queries
- **KMS SDKs**: AWS boto3, Azure Key Vault, Google Cloud KMS, HashiCorp Vault
- **python-jose**: JWT token handling
- **Environment Configuration**: `.env` file for secrets
- **Bleach**: HTML sanitization for XSS prevention

---

## Pattern 1: Credential Management

### Never Hardcode Credentials

**Environment Variables** (src/codemie/configs/config.py:6-290)

```python
from pydantic_settings import BaseSettings
from dotenv import find_dotenv, load_dotenv

class Config(BaseSettings):
    # API Keys - NEVER hardcode
    AZURE_OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    # Database credentials
    POSTGRES_PASSWORD: str = "password"
    ELASTIC_PASSWORD: str = ""

    # Cloud credentials
    AWS_KMS_KEY_ID: str = ""
    AZURE_CLIENT_SECRET: str = ""
    GCP_API_KEY: str = ""

    model_config = SettingsConfigDict(
        env_file=find_dotenv(".env", raise_error_if_not_found=False),
        extra="ignore"
    )

load_dotenv(find_dotenv(".env", raise_error_if_not_found=False))
config = Config()
```

**Credential Redaction** (src/codemie/configs/config.py:297-318)

```python
def to_safe_dict(self):
    """Mask sensitive information before logging"""
    sensitive_keywords = ['key', 'password', 'secret', 'token']
    sensitive_keys = ["AZURE_STORAGE_CONNECTION_STRING", "PG_URL"]
    config_dict = self.model_dump()
    safe_dict = {}

    for k, v in config_dict.items():
        if not any(k.lower().endswith(keyword) for keyword in sensitive_keywords)
           and k not in sensitive_keys:
            safe_dict[k] = v
        else:
            safe_dict[k] = "******"  # Mask credentials

    return safe_dict
```

### KMS Integration Pattern

**AWS KMS** (src/codemie/service/encryption/aws_encryption_service.py:7-31)

```python
import boto3
from codemie.configs import config

class AWSKMSEncryptionService(BaseEncryptionService):
    def __init__(self):
        region_kwargs = {}
        region = str(getattr(config, 'AWS_KMS_REGION', '') or '').strip()
        if region:
            region_kwargs['region_name'] = region
        self.kms_client = boto3.client('kms', **region_kwargs)
        self.key_id = config.AWS_KMS_KEY_ID

    def encrypt(self, data: str):
        response = self.kms_client.encrypt(
            KeyId=self.key_id,
            Plaintext=data.encode()
        )
        return base64.b64encode(response['CiphertextBlob']).decode('utf-8')
```

**Azure Key Vault** (src/codemie/service/encryption/azure_encryption_service.py:10-27)

```python
from azure.identity import DefaultAzureCredential
from azure.keyvault.keys import KeyClient
from azure.keyvault.keys.crypto import CryptographyClient, EncryptionAlgorithm

class AzureKMSEncryptionService(BaseEncryptionService):
    def __init__(self):
        credentials = DefaultAzureCredential()
        self.secret_client = KeyClient(
            vault_url=config.AZURE_KEY_VAULT_URL,
            credential=credentials
        )
        key = self.secret_client.get_key(config.AZURE_KEY_NAME)
        self.crypto_client = CryptographyClient(key, credentials)

    def encrypt(self, data: str) -> str:
        if not data:
            raise ValueError("Data to encrypt cannot be empty.")
        response = self.crypto_client.encrypt(
            EncryptionAlgorithm.rsa_oaep,
            data.encode()
        )
        return base64.b64encode(response.ciphertext).decode('utf-8')
```

**GCP KMS** (src/codemie/service/encryption/gcp_encryption_service.py:9-24)

```python
from google.cloud import kms

class GCPKMSEncryptionService(BaseEncryptionService):
    def __init__(self):
        self.client = kms.KeyManagementServiceClient()
        self.key_name = self.client.crypto_key_path(
            config.GOOGLE_KMS_PROJECT_ID,
            config.GOOGLE_KMS_REGION,
            config.GOOGLE_KMS_KEY_RING,
            config.GOOGLE_KMS_CRYPTO_KEY
        )

    def encrypt(self, data: str):
        response = self.client.encrypt(
            request={'name': self.key_name, 'plaintext': data.encode()}
        )
        return base64.b64encode(response.ciphertext).decode('utf-8')
```

---

## Pattern 2: Input Validation

### Pydantic Model Validation

**Request Model with Type Enforcement** (src/codemie/rest_api/models/settings.py:25-40)

```python
from pydantic import BaseModel, Field, model_validator

class Credentials(BaseModel):
    url: str  # Required field, must be string
    token: str
    token_name: Optional[str] = None

    @model_validator(mode='before')
    def validate_config(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        # Custom validation logic
        if "name" in values:
            values["token_name"] = values.pop("name")
        return values

class SettingRequest(BaseModel):
    project_name: Optional[str] = None
    alias: str  # Required
    credential_type: CredentialTypes  # Enum validation
    credential_values: List[CredentialValues]  # List type validation
    is_global: Optional[bool] = False
```

### FastAPI Dependency Injection for Validation

**Authentication with Validated Headers** (src/codemie/rest_api/security/authentication.py:17-27)

```python
from fastapi.security import APIKeyHeader

user_id_header = APIKeyHeader(
    name=USER_ID_HEADER,
    auto_error=False,
    scheme_name=USER_ID_HEADER
)
keycloak_authorization_token = APIKeyHeader(
    name=KEYCLOAK_BEARER_AUTHORIZATION_HEADER,
    auto_error=False,
    scheme_name=KEYCLOAK_BEARER_AUTHORIZATION_HEADER
)
```

---

## Pattern 3: SQL Injection Prevention

### SQLModel Parameterized Queries

**Safe Query with WHERE Clause** (src/codemie/rest_api/models/settings.py:276-282)

```python
from sqlmodel import Session, select

# ✅ SAFE: Parameterized query prevents SQL injection
with Session(cls.get_engine()) as session:
    statement = select(Settings)
    statement = statement.where(Settings.project_name.in_(project_names))
    statement = statement.where(Settings.setting_type == SettingType.PROJECT.value)
    if credential_type:
        statement = statement.where(Settings.credential_type == credential_type.value)
    return session.exec(statement).all()
```

**Anti-Pattern: String Concatenation**

```python
# ❌ NEVER DO THIS - Vulnerable to SQL injection
query = f"SELECT * FROM settings WHERE project_name = '{project_name}'"
session.execute(query)

# ❌ NEVER DO THIS - String formatting is vulnerable
query = "DELETE FROM users WHERE id = {}".format(user_id)
```

### ORM Best Practices

- **Always use SQLModel `.where()` methods** - automatically parameterized
- **Use `.in_()` for list parameters** - prevents injection in IN clauses
- **Never concatenate user input** - use bound parameters
- **Use ORM methods** - `select()`, `insert()`, `update()`, `delete()`

---

## Pattern 4: Authentication & Authorization

### Multi-Provider Authentication

**IDP-Based Auth** (src/codemie/rest_api/security/authentication.py:43-72)

```python
from fastapi import Depends, Request

async def authenticate(
    request: Request,
    user_id: str | None = Depends(user_id_header),
    keycloak_auth_header: str = Depends(keycloak_authorization_token),
    oidc_auth_header: str = Depends(oidc_authorization_token),
) -> User:
    """Authenticate using configured IDP provider"""
    try:
        if keycloak_auth_header or oidc_auth_header:
            auth_token = keycloak_auth_header or oidc_auth_header
            idp = get_idp_provider()
            request.state.user = await idp.authenticate(request, auth_token)
        elif user_id:
            idp = get_idp_provider(IdentityProvider.LOCAL)
            request.state.user = await idp.authenticate(request, user_id)
        else:
            raise _create_auth_error(
                "No valid authentication credentials were provided."
            )

        return request.state.user
    except Exception as e:
        if isinstance(e, ExtendedHTTPException):
            raise e
        raise _create_auth_error(str(e))
```

### Role-Based Access Control

**Admin Authorization** (src/codemie/rest_api/security/authentication.py:75-86)

```python
async def admin_access_only(request: Request):
    """Checks if current user is admin"""
    if not request.state.user.is_admin:
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Access denied",
            details="This action requires administrator privileges.",
            help="Contact your system administrator for access."
        )
```

**Application-Level Access** (src/codemie/rest_api/security/authentication.py:89-100)

```python
def application_access_check(request: Request, app_name: str):
    """Checks if current user has access to application"""
    if not request.state.user.has_access_to_application(app_name):
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Access denied",
            details=f"No permission to access application '{app_name}'.",
            help="Contact your system administrator or application owner."
        )
```

---

## Pattern 5: JWT Validation

### Token Decoding with Validation

**Keycloak JWT Parsing** (src/codemie/rest_api/security/idp/keycloak.py:89-107)

```python
import jwt

def _get_user_info_from_token(self, access_token: str) -> Dict | None:
    """Get user info from JWT access token"""
    try:
        # Decode without signature verification (proxy validates)
        payload = jwt.decode(access_token, options={"verify_signature": False})
        return {
            "sub": payload["sub"],
            "preferred_username": payload["preferred_username"],
            "name": payload["name"],
            "realm_access": {"roles": payload["realm_access"]["roles"]},
            "applications": self._parse_attribute_to_list(payload.get("applications")),
        }
    except jwt.InvalidTokenError:
        return None  # Token is invalid
```

**Note**: In production with direct JWT validation (not behind auth proxy), always verify signature:

```python
# ✅ Production pattern with signature verification
payload = jwt.decode(
    token,
    key=public_key,
    algorithms=["RS256"],
    audience="expected-audience",
    options={
        "verify_signature": True,
        "verify_exp": True,
        "verify_aud": True
    }
)
```

---

## Pattern 6: Encryption at Rest & Transit

### Encryption Service Factory

**Multi-Provider Encryption** (encryption_factory.py pattern)

```python
from abc import ABC, abstractmethod

class BaseEncryptionService(ABC):
    @abstractmethod
    def encrypt(self, data: str) -> str:
        pass

    @abstractmethod
    def decrypt(self, data: str) -> str:
        pass

# Factory selects provider based on config.ENCRYPTION_TYPE
# Values: "plain", "base64", "aws", "azure", "gcp", "vault"
encryption_service = EncryptionFactory.create(config.ENCRYPTION_TYPE)
encrypted_data = encryption_service.encrypt(sensitive_data)
```

### Error Handling in Encryption

```python
def encrypt(self, data: str):
    try:
        response = self.kms_client.encrypt(KeyId=self.key_id, Plaintext=data.encode())
        return base64.b64encode(response['CiphertextBlob']).decode('utf-8')
    except Exception as e:
        logger.error(f"Failed to encrypt data: {e}")
        raise e  # Re-raise to caller

def decrypt(self, data: str):
    try:
        encoded_data = base64.b64decode(data)
        response = self.kms_client.decrypt(CiphertextBlob=encoded_data)
        return response['Plaintext'].decode()
    except Exception as e:
        logger.error(f"Failed to decrypt data: {e}")
        return data  # Fail gracefully, return original
```

---

## OWASP Top 10 Coverage

| Vulnerability | CodeMie Prevention Pattern | Implementation |
|--------------|---------------------------|----------------|
| **A01 Broken Access Control** | RBAC with FastAPI dependencies | `admin_access_only`, `application_access_check` |
| **A02 Cryptographic Failures** | KMS integration (AWS/Azure/GCP) | BaseEncryptionService, multi-cloud KMS |
| **A03 Injection** | SQLModel parameterized queries | `.where()`, `.in_()` methods |
| **A04 Insecure Design** | Pydantic validation at API boundary | BaseModel with Field constraints |
| **A05 Security Misconfiguration** | Environment-based config | Config class with `.env` loading |
| **A06 Vulnerable Components** | Dependency pinning | pyproject.toml with version constraints |
| **A07 Auth Failures** | Multi-provider IDP support | Keycloak, OIDC, Local auth |
| **A08 Integrity Failures** | JWT signature validation | python-jose token verification |
| **A09 Logging Failures** | Credential redaction | `to_safe_dict()` before logging |
| **A10 SSRF** | Input validation | Pydantic URL types, sanitization |

---

## Anti-Patterns

### What NOT to Do

```python
# ❌ Hardcoding credentials
OPENAI_API_KEY = "sk-12345..."
DB_PASSWORD = "postgres123"

# ❌ SQL injection via string concatenation
query = f"SELECT * FROM users WHERE name = '{user_name}'"

# ❌ Logging sensitive data
logger.info(f"User password: {password}")

# ❌ Skipping validation
def create_user(data: dict):  # Use Pydantic model instead
    db.execute(f"INSERT INTO users VALUES ('{data['name']}')")

# ❌ Plain text encryption
def encrypt(data):
    return base64.b64encode(data)  # Not encryption, just encoding!

# ❌ JWT without expiration check
payload = jwt.decode(token, verify_exp=False)

# ❌ Exposing internal errors to users
except Exception as e:
    return {"error": str(e)}  # Might leak stack traces
```

---

## Examples

### Example 1: Secure API Endpoint

```python
from fastapi import Depends, APIRouter
from codemie.rest_api.security.authentication import authenticate, admin_access_only

router = APIRouter()

@router.get("/admin/users")
async def get_all_users(
    user: User = Depends(authenticate),
    _: None = Depends(admin_access_only)
):
    """Admin-only endpoint with authentication"""
    # Both authentication and authorization enforced
    return {"users": User.get_all()}
```

### Example 2: Secure Credential Storage

```python
# Settings model with credential encryption
class Settings(BaseModelWithSQLSupport, SettingsBase, table=True):
    credential_values: List[CredentialValues] = SQLField(
        default_factory=list,
        sa_column=Column(PydanticListType(CredentialValues))
    )

    def save(self):
        # Encrypt sensitive values before storage
        encryption_service = EncryptionFactory.create(config.ENCRYPTION_TYPE)
        for cred in self.credential_values:
            if cred.key in ['password', 'token', 'api_key']:
                cred.value = encryption_service.encrypt(cred.value)
        super().save()
```

### Example 3: Input Validation Pipeline

```python
from pydantic import BaseModel, Field, field_validator

class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern="^[a-zA-Z0-9_-]+$")
    email: str = Field(pattern=r"^[\w\.-]+@[\w\.-]+\.\w+$")
    password: str = Field(min_length=8)

    @field_validator('email')
    @classmethod
    def validate_email_domain(cls, v):
        if not v.endswith('@epam.com'):
            raise ValueError('Must use EPAM email')
        return v
```

---

## Verification

### Testing Security Controls

```bash
# Run security-focused tests
pytest tests/security/ -v

# Check for hardcoded secrets
git secrets --scan

# Dependency vulnerability scan
pip-audit

# Run validation script on this doc
python .codemie/guides/validate-docs.py --check all --dir .codemie/guides/development/
```

### Manual Security Checklist

- [ ] No credentials hardcoded in source code
- [ ] All API keys loaded from environment variables
- [ ] KMS integration configured for encryption at rest
- [ ] Pydantic models validate all user input
- [ ] SQLModel used for all database queries (no raw SQL)
- [ ] JWT tokens validated with signature and expiration
- [ ] Authentication required on all sensitive endpoints
- [ ] Authorization checks enforce least privilege
- [ ] Sensitive data masked in logs
- [ ] HTTPS enforced for API communication

---

## Troubleshooting

### Authentication Failures

**Problem**: `HTTP 401 Unauthorized`

```python
# Check if IDP provider configured correctly
# In config.py
IDP_PROVIDER: Literal["keycloak", "local", "oidc"] = "local"

# Verify headers are present
if not keycloak_auth_header and not oidc_auth_header and not user_id:
    raise _create_auth_error("No authentication credentials provided")
```

### Permission Errors

**Problem**: `HTTP 403 Forbidden`

```python
# Verify user has required role
if not request.state.user.is_admin:
    raise ExtendedHTTPException(code=403, message="Access denied")

# Check application access
if app_name not in user.applications:
    raise ExtendedHTTPException(code=403, message="No access to application")
```

### KMS Encryption Failures

**Problem**: Encryption service initialization fails

```python
# AWS KMS - check region and key ID
AWS_KMS_REGION = "us-east-1"
AWS_KMS_KEY_ID = "arn:aws:kms:us-east-1:123456789012:key/..."

# Azure Key Vault - check URL and key name
AZURE_KEY_VAULT_URL = "https://my-vault.vault.azure.net/"
AZURE_KEY_NAME = "codemie-key"

# GCP KMS - check project, region, key ring
GOOGLE_KMS_PROJECT_ID = "my-project"
GOOGLE_KMS_REGION = "us-central1"
GOOGLE_KMS_KEY_RING = "codemie"
```

---

## Next Steps

- **Testing Patterns**: See testing-strategy-pytest-patterns.md (Story 5.1) for security test examples
- **Error Handling**: See error-handling-exception-patterns.md (Story 5.2) for security exception patterns
- **Configuration**: See configuration-management-patterns.md (Story 5.4) for secure config practices
- **LLM Providers**: See [llm-providers.md](../integration/llm-providers.md) for API key management
- **Cloud Integrations**: See [cloud-integrations.md](../integration/cloud-integrations.md) for KMS details

---

## References

**Source Files**:
- `src/codemie/rest_api/security/authentication.py` - Auth/authz patterns
- `src/codemie/rest_api/security/idp/` - IDP implementations (Keycloak, OIDC, Local)
- `src/codemie/service/encryption/` - KMS encryption services
- `src/codemie/configs/config.py` - Environment-based configuration
- `src/codemie/core/exceptions.py` - Security exception classes
- `src/codemie/rest_api/models/settings.py` - Pydantic validation examples
- `src/codemie/repository/` - SQLModel parameterized query patterns

**Related Patterns**:
- [REST API Patterns](../api/rest-api-patterns.md) - API security middleware
- [Repository Patterns](../data/repository-patterns.md) - Data access security
- [Database Patterns](../data/database-patterns.md) - SQL injection prevention

**External Resources**:
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [Pydantic Validation](https://docs.pydantic.dev/latest/concepts/validators/)
- [FastAPI Security](https://fastapi.tiangolo.com/tutorial/security/)
- [SQLModel Documentation](https://sqlmodel.tiangolo.com/)
- [AWS KMS](https://aws.amazon.com/kms/), [Azure Key Vault](https://azure.microsoft.com/en-us/products/key-vault), [GCP KMS](https://cloud.google.com/kms)
