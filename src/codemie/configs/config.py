# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
from pathlib import Path
from typing import Literal

from dotenv import find_dotenv, load_dotenv
from pydantic import BaseModel
from pydantic_settings import SettingsConfigDict, BaseSettings


class PredefinedBudgetConfig(BaseModel):
    """Full definition of a budget that is managed via configuration.

    Predefined budgets are force-created/updated at startup and cannot be
    modified through the API or UI — configuration is the source of truth.
    """

    budget_id: str
    name: str
    description: str | None = None
    soft_budget: float = 0.0
    max_budget: float
    budget_duration: str = "30d"
    budget_category: str  # "platform" | "cli" | "premium_models"


ENV_LOCAL = "local"


class Config(BaseSettings):
    """
    Variables contained in this model will attempt to load from .env or environment and if variable is missing,
    it will throw exception.
    """

    APP_VERSION: str = "0.16.0"
    ENV: str = ENV_LOCAL
    MODELS_ENV: str = "dial"
    LOG_LEVEL: str = "INFO"
    CALLBACK_API_BASE_URL: str = "http://host.docker.internal:8080"
    API_ROOT_PATH: str = ""
    TIMEZONE: str = "UTC"

    OPENAI_API_TYPE: str = "azure"
    OPENAI_API_VERSION: str = "2024-12-01-preview"
    AZURE_OPENAI_API_KEY: str = ""
    AZURE_OPENAI_URL: str = ""
    AZURE_OPENAI_MAX_RETRIES: int = 5

    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MAX_RETRIES: int = 2

    IMAGE_GENERATION_MODEL: str = "gemini-3.1-flash-image-preview"

    STT_API_URL: str = ""
    STT_API_KEY: str = ""
    STT_API_DEPLOYMENT_NAME: str = ""
    STT_MODEL_NAME: str = ""

    ELASTIC_URL: str = "http://localhost:9200"
    ELASTIC_PASSWORD: str = ""
    ELASTIC_USERNAME: str = ""

    # Mermaid diagram rendering configuration
    MERMAID_SERVER_URL: str = "http://localhost:8082"  # URL of the local Mermaid rendering server
    MERMAID_SERVER_TIMEOUT: int = 50  # Timeout (in seconds) for requests to the Mermaid server
    MERMAID_USE_MERMAID_INC: bool = False  # Use Mermaid Inc. hosted service if True, otherwise use local server

    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "postgres"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "password"

    PG_URL: str = ""
    PG_POOL_SIZE: int = 10
    DEFAULT_DB_SCHEMA: str = "codemie"

    PROJECT_ROOT: Path = Path(__file__).absolute().parents[1]
    LLM_TEMPLATES_ROOT: Path = Path(__file__).absolute().parents[3] / "config/llms"
    DATASOURCES_CONFIG_DIR: Path = Path(__file__).absolute().parents[3] / "config/datasources"
    ASSISTANT_TEMPLATES_DIR: Path = Path(__file__).absolute().parents[3] / "config/templates/assistant"
    WORKFLOW_TEMPLATES_DIR: Path = Path(__file__).absolute().parents[3] / "config/templates/workflow"
    SKILL_TEMPLATES_DIR: Path = Path(__file__).absolute().parents[3] / "config/templates/skill"
    CUSTOMER_CONFIG_DIR: Path = Path(__file__).absolute().parents[3] / "config/customer"
    BUDGETS_CONFIG_DIR: Path = Path(__file__).absolute().parents[3] / "config/budgets"
    ASSISTANT_CATEGORIES_CONFIG_DIR: Path = Path(__file__).absolute().parents[3] / "config/categories"
    KATA_TAGS_CONFIG_PATH: Path = Path(__file__).absolute().parents[3] / "config/categories/kata-tags.yaml"
    KATA_ROLES_CONFIG_PATH: Path = Path(__file__).absolute().parents[3] / "config/categories/kata-roles.yaml"
    KATAS_SOURCE_DIR: Path = Path(__file__).absolute().parents[3] / "config/katas"
    KATAS_REPO_URL: str = "https://github.com/codemie-ai/codemie-katas.git"
    KATAS_MAX_FILE_SIZE: int = 1 * 1024 * 1024  # 1 MB per file
    KATAS_MAX_YAML_SIZE: int = 100 * 1024  # 100 KB for YAML
    KATAS_MAX_MARKDOWN_SIZE: int = 1000 * 1024  # 1000 KB for Markdown
    KATAS_ALLOWED_EXTENSIONS: list[str] = [".yaml", ".yml", ".md"]  # Only these files are validated; images are ignored
    LEADERBOARD_FRAMEWORK_METADATA_PATH: Path = (
        Path(__file__).absolute().parents[3] / "config/leaderboard/framework_metadata.yaml"
    )
    AUTHORIZED_APPS_CONFIG_DIR: Path = Path(__file__).absolute().parents[3] / "config/authorized_applications"
    INDEX_DUMPS_DIR: Path = Path(__file__).absolute().parents[3] / "config/index-dumps"
    ALEMBIC_MIGRATIONS_DIR: Path = Path(__file__).absolute().parents[2] / "external/alembic"
    ALEMBIC_INI_PATH: Path = Path(__file__).absolute().parents[2] / "external/alembic/alembic.ini"
    REPOS_LOCAL_DIR: str = "./codemie-repos"
    FILES_STORAGE_DIR: str = "./codemie-storage"
    FILES_STORAGE_TYPE: Literal["filesystem", "aws", "azure", "gcp"] = 'filesystem'
    FILES_STORAGE_MAX_UPLOAD_SIZE: int = 100 * 1024 * 1024  # 100 MB
    FILES_STORAGE_GCP_REGION: str = "US"
    LLM_REQUEST_ADD_MARKDOWN_PROMPT: bool = True

    VERTEX_AI_ANTHROPIC_ENABLE_PROMPT_CACHE: bool = False

    ELASTIC_APPLICATION_INDEX: str = "applications"
    ELASTIC_GIT_REPO_INDEX: str = "repositories"
    ELASTIC_LOGS_INDEX: str = "codemie_infra_logs*"
    ELASTIC_METRICS_INDEX: str = "codemie_metrics_logs*"
    FEEDBACK_INDEX_NAME: str = "ca_feedback"
    BACKGROUND_TASKS_INDEX: str = "background_tasks"
    USER_CONVERSATION_INDEX: str = "codemie_raw_user_conversations"
    USER_CONVERSATION_FOLDER_INDEX: str = "codemie_conversation_folder"
    CONVERSATIONS_METRICS_INDEX: str = "codemie_conversation_metrics"
    SHARED_CONVERSATION_INDEX: str = "codemie_shared_conversations"
    KZ_USERS_INDEX: str = "codemie_kz_users_data"
    ASSISTANTS_INDEX: str = "codemie_assistants"
    WORKFLOWS_INDEX: str = "workflows"
    SETTINGS_INDEX: str = "codemie_user_settings"
    USER_DATA_INDEX: str = "codemie_user_data"
    INDEX_STATUS_INDEX: str = "index_status"
    PROVIDERS_INDEX: str = "providers"
    WORKFLOW_EXECUTION_INDEX: str = "workflows_execution_history"
    WORKFLOW_EXECUTION_STATE_INDEX: str = "workflows_execution_states"
    WORKFLOW_EXECUTION_STATE_THOUGHTS_INDEX: str = "workflows_execution_state_thoughts"

    WORKFLOW_MAX_CONCURRENCY: int = 5
    WORKFLOW_DEFAULT_CONCURRENCY: int = 2

    DATASOURCE_CONCURRENCY_LIMIT_ENABLED: bool = False
    MAX_CONCURRENT_DATASOURCE_INDEXING: int = 5
    DATASOURCE_QUEUE_TIMEOUT: int = 3600  # seconds; 0 disables the timeout

    # Analytics dashboard configuration
    ANALYTICS_DEFAULT_PAGE_SIZE: int = 20  # Default number of rows for analytics endpoints

    INDEXES_PERMITTED_FOR_SEARCH: list[str] = [
        KZ_USERS_INDEX,
    ]

    IDP_PROVIDER: Literal["keycloak", "local", "oidc", "entraid-oidc"] = "local"
    KEYCLOAK_LOGOUT_URL: str = ""
    ADMIN_USER_ID: str = ""
    ADMIN_ROLE_NAME: str = "admin"

    # ===========================================
    # User Management Feature Flag
    # ===========================================
    ENABLE_USER_MANAGEMENT: bool = False  # Master switch for new user management system

    USER_PROJECT_LIMIT: int = 3  # Max number of shared projects per user (enforced when ENABLE_USER_MANAGEMENT=True)
    COST_CENTER_NAME_PATTERN: str = r"^[a-z0-9]+-[a-z0-9]+$"
    # ===========================================
    # SuperAdmin Bootstrap
    # ===========================================
    SUPERADMIN_EMAIL: str = ""  # Auto-create SuperAdmin if set and none exists
    SUPERADMIN_PASSWORD: str = ""

    # ===========================================
    # Keycloak Migration
    # ===========================================
    # Only active when IDP_PROVIDER="keycloak" AND ENABLE_USER_MANAGEMENT=True
    KEYCLOAK_MIGRATION_ENABLED: bool = False
    KEYCLOAK_ADMIN_URL: str = ""  # e.g. https://keycloak.example.com
    KEYCLOAK_ADMIN_REALM: str = ""  # realm to migrate, e.g. "codemie"
    KEYCLOAK_ADMIN_CLIENT_ID: str = ""  # service-account client ID
    KEYCLOAK_ADMIN_CLIENT_SECRET: str = ""  # service-account client secret
    KEYCLOAK_MIGRATION_BATCH_SIZE: int = 100  # users per page
    KEYCLOAK_MIGRATION_LOCK_TIMEOUT_MINUTES: int = 30  # stale lock threshold
    KEYCLOAK_MIGRATION_WAIT_INTERVAL_SECONDS: int = 5  # follower poll interval

    # ===========================================
    # JWT (RS256) for Local Authentication
    # ===========================================
    JWT_ALGORITHM: str = "RS256"
    JWT_EXPIRATION_HOURS: int = 24
    JWT_PRIVATE_KEY_PATH: str = ".keys/jwt_private.pem"
    JWT_PUBLIC_KEY_PATH: str = ".keys/jwt_public.pem"
    JWT_ISSUER: str = "codemie-local"  # Issuer claim for local JWTs

    # ===========================================
    # Cookie-Based Authentication (Local Auth)
    # ===========================================
    AUTH_COOKIE_NAME: str = "codemie_access_token"
    AUTH_COOKIE_HTTPONLY: bool = True  # Prevent JS access (XSS protection)
    AUTH_COOKIE_SECURE: bool = False  # Set True in production (HTTPS only)
    AUTH_COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    AUTH_COOKIE_PATH: str = "/"

    # Auth token cache — skips DB for repeated requests with the same token within TTL
    AUTH_TOKEN_CACHE_MAX_SIZE: int = 10000
    AUTH_TOKEN_CACHE_TTL: int = 30  # seconds

    # ===========================================
    # Email Verification & Password Reset
    # ===========================================
    EMAIL_VERIFICATION_ENABLED: bool = True  # Enable/disable email verification for local auth
    EMAIL_SMTP_HOST: str = ""
    EMAIL_SMTP_PORT: int = 587
    EMAIL_SMTP_USERNAME: str = ""
    EMAIL_SMTP_PASSWORD: str = ""
    EMAIL_FROM_ADDRESS: str = ""
    EMAIL_FROM_NAME: str = "CodeMie"
    EMAIL_USE_TLS: bool = True
    FRONTEND_URL: str = "http://localhost:3000"  # For email links

    # ===========================================
    # Password Policy
    # ===========================================
    PASSWORD_MIN_LENGTH: int = 12  # Configurable minimum password length

    # Broker Token Exchange configuration (multi-hop token exchange)
    # Comma-separated lists for each hop in the token exchange chain
    # Example: "https://auth1.example.com,https://auth2.example.com"
    BROKER_TOKEN_URLS: str = ""  # Comma-separated base URLs for each broker hop
    BROKER_TOKEN_REALMS: str = ""  # Comma-separated realm names for each hop
    BROKER_TOKEN_BROKERS: str = ""  # Comma-separated broker identifiers for each hop
    BROKER_TOKEN_TIMEOUT: float = 5.0
    BROKER_AUTH_LOCATION_URL: str = ""  # Value for x-user-mcp-auth-location header on broker auth failures

    # OIDC Token Exchange configuration (RFC 8693)
    # Used when an MCP server requires an audience-scoped token via Keycloak token exchange
    # Example: TOKEN_EXCHANGE_URL="https://access.epam.com/auth/realms/plusx/protocol/openid-connect/token"
    TOKEN_EXCHANGE_URL: str = ""  # Keycloak token endpoint URL
    TOKEN_EXCHANGE_GRANT_TYPE: str = "urn:ietf:params:oauth:grant-type:token-exchange"
    TOKEN_EXCHANGE_CLIENT_ID: str = ""  # OAuth2 client ID
    TOKEN_EXCHANGE_CLIENT_SECRET: str = ""  # OAuth2 client secret
    TOKEN_EXCHANGE_SUBJECT_TOKEN_TYPE: str = "urn:ietf:params:oauth:token-type:access_token"
    TOKEN_EXCHANGE_TIMEOUT: float = 5.0

    # External user configuration
    EXTERNAL_USER_TYPE: str = "external"
    EXTERNAL_USER_ALLOWED_PROJECTS: list[str] = ["codemie"]

    GOOGLE_SEARCH_API_KEY: str = ""
    GOOGLE_SEARCH_CSE_ID: str = ""
    TAVILY_API_KEY: str = ""

    KUBERNETES_API_URL: str = ""
    KUBERNETES_API_TOKEN: str = ""

    TRIGGER_ENGINE_ENABLED: bool = False
    STALE_INDEXING_WATCHDOG_ENABLED: bool = True
    SCHEDULER_PROMPT_SIZE_LIMIT: int = 4000

    NATS_PLUGIN_KEY_CHECK_ENABLED: bool = False
    NATS_SERVERS_URI: str = "nats://nats:4222"
    NATS_CLIENT_CONNECT_URI: str = ""
    NATS_USER: str = "codemie"
    NATS_PASSWORD: str = "codemie"
    NATS_SKIP_TLS_VERIFY: bool = False
    NATS_MAX_RECONNECT_ATTEMPTS: int = -1
    NATS_CONNECT_TIMEOUT: int = 5
    NATS_RECONNECT_TIME_WAIT: int = 10
    NATS_VERBOSE: bool = False
    NATS_MAX_OUTSTANDING_PINGS: int = 5  # Set Max Pings Outstanding to 5
    NATS_PING_INTERVAL: int = 120  # Set Ping Interval to 120 seconds
    NATS_PLUGIN_PING_TIMEOUT_SECONDS: int = 1
    NATS_PLUGIN_UPDATE_INTERVAL: int = 60
    NATS_PLUGIN_LIST_TIMEOUT_SECONDS: int = 15
    NATS_PLUGIN_MAX_VALIDATION_ATTEMPTS: int = 3
    NATS_PLUGIN_V2_ENABLED: bool = True
    NATS_PLUGIN_TOOL_TIMEOUT: int = 302
    NATS_CONNECTION_POOL_SIZE: int = 20  # Size of the NATS connection pool
    NATS_CONNECTION_POOL_MAX_AGE: int = 300  # Maximum age of connections in the pool in seconds
    NATS_CONNECTION_POOL_ACQUIRE_TIMEOUT: float = 10.0  # Timeout in seconds for acquiring a connection from the pool
    NATS_PLUGIN_EXECUTE_TIMEOUT: int = 302

    AZURE_SUBSCRIPTION_ID: str = ""
    AZURE_TENANT_ID: str = ""
    AZURE_CLIENT_ID: str = ""
    AZURE_CLIENT_SECRET: str = ""
    AZURE_KEY_VAULT_URL: str = ""  # Azure Key Vault URL
    AZURE_KEY_NAME: str = ""  # Azure Key Vault Key Name
    AZURE_STORAGE_CONNECTION_STRING: str = ""  # Azure Blob Storage configurations
    AZURE_STORAGE_ACCOUNT_NAME: str = ""  # Azure StorageAccount name

    AWS_KMS_KEY_ID: str = ""
    AWS_S3_REGION: str = ""
    AWS_S3_BUCKET_NAME: str = ""
    AWS_BEDROCK_MAX_RETRIES: int = 5
    AWS_BEDROCK_READ_TIMEOUT: int = 60000
    AWS_KMS_REGION: str = ""
    AWS_BEDROCK_REGION: str = ""

    # Accepts GCP service account key(additionally "base64 -w 0" encoded by a user)
    GCP_API_KEY: str = ""
    GOOGLE_PROJECT_ID: str = ""
    GOOGLE_REGION: str = ""

    # GCP KMS configuration specifics
    GOOGLE_KMS_PROJECT_ID: str = GOOGLE_PROJECT_ID
    GOOGLE_KMS_KEY_RING: str = "codemie"
    GOOGLE_KMS_CRYPTO_KEY: str = "codemie"
    GOOGLE_KMS_REGION: str = GOOGLE_REGION

    # GCP models configuration specifics
    GOOGLE_VERTEXAI_REGION: str = ""
    GOOGLE_CLAUDE_VERTEXAI_REGION: str = ""
    GOOGLE_VERTEXAI_MAX_RETRIES: int = 5

    # HashiCorp Vault configuration
    VAULT_URL: str = ""
    VAULT_TOKEN: str = ""
    VAULT_NAMESPACE: str = ""
    VAULT_TRANSIT_KEY_NAME: str = "codemie"
    VAULT_TRANSIT_MOUNT_POINT: str = "transit"

    ENCRYPTION_TYPE: str = "plain"

    STATE_IMPORT_DIR: str = "./state_import"
    STATE_IMPORT_ENABLED: bool = False
    CODEMIE_EXPORT_ROOT: str = "/app"
    THREAD_POOL_MAX_WORKERS: int = 5
    CODEMIE_STORAGE_BUCKET_NAME: str = "codemie-global-storage"
    AZURE_SPEECH_REGION: str = ""
    AZURE_SPEECH_SERVICE_KEY: str = ""

    GITHUB_IDENTIFIERS: list[str] = ["github"]
    GITLAB_IDENTIFIERS: list[str] = ["gitlab"]
    BITBUCKET_IDENTIFIERS: list[str] = ["bitbucket"]
    AZURE_DEVOPS_REPOS_IDENTIFIERS: list[str] = ["dev.azure.com"]

    A2A_AGENT_CARD_FETCH_TIMEOUT: float = 30.0
    A2A_AGENT_REQUEST_TIMEOUT: float = 30.0
    A2A_PROVIDER_ORGANIZATION: str = ""
    A2A_PROVIDER_URL: str = ""

    # SharePoint OAuth (delegated auth via Device Code Flow)
    SHAREPOINT_OAUTH_CLIENT_ID: str = ""
    SHAREPOINT_OAUTH_SCOPES: str = "Sites.Read.All Files.Read.All offline_access User.Read"

    MCP_CONNECT_ENABLED: bool = True
    MCP_CONNECT_URL: str = "http://localhost:3000"
    MCP_CONNECT_BUCKETS_COUNT: int = 10
    MCP_TOOL_TOKENS_SIZE_LIMIT: int = 30000

    # CLI metrics data quality cutoff
    CLI_METRICS_CUTOFF_DATE: str = "2026-02-07"

    # Cache configuration for MCP toolkit instances
    MCP_TOOLKIT_SERVICE_CACHE_SIZE: int = 100
    MCP_TOOLKIT_SERVICE_CACHE_TTL: int = 3600

    # Cache configuration for MCP toolkit factory
    MCP_TOOLKIT_FACTORY_CACHE_SIZE: int = 50
    MCP_TOOLKIT_FACTORY_CACHE_TTL: int = 600

    # Token Exchange Factory configuration
    TOKEN_CACHE_TTL: int = 600  # 10 mins for exchanged tokens
    TOKEN_CACHE_MAX_SIZE: int = 1024  # max entries across all token caches (per-user + per-audience)

    # MCP Client configuration
    MCP_CLIENT_TIMEOUT: float = 300.0  # Timeout in seconds for MCP client requests

    # MCP Header Propagation configuration
    # Comma-separated list of header names (case-insensitive) that should NOT be propagated to MCP servers
    MCP_BLOCKED_HEADERS: str = (
        "authorization,cookie,set-cookie,x-api-key,x-auth-token,x-internal-secret,x-internal-token"
    )

    # AMNA-AIRN feature flags
    AMNA_AIRN_PRECREATE_WORKFLOWS: bool = False
    WORKERS: int = 1
    LANGFUSE_TRACES: bool = False
    LANGFUSE_BLOCKED_INSTRUMENTATION_SCOPES: list[str] = ["elasticsearch-api"]
    CODEMIE_SUPPORT: str = "https://epa.ms/codemie-support"
    CODEMIE_SUPPORT_MSG: str = f"For assistance, please contact support at {CODEMIE_SUPPORT}"

    # Langgraph agent version
    ENABLE_LANGGRAPH_AITOOLS_AGENT: bool = True

    # Dynamic tools configuration - tool name mappings
    DYNAMIC_WEB_SEARCH_TOOLS: list[str] = [
        "google_search_tool_json",  # Google Search
        "tavily_search_results_json",  # Tavily Search
        "web_scrapper",  # Web Scraper
    ]

    DYNAMIC_CODE_INTERPRETER_TOOLS: list[str] = [
        "python_repl_code_interpreter",  # Python REPL Code Interpreter
        "code_executor",  # Code Executor (with file upload support)
    ]
    DISABLE_PARALLEL_TOOLS_CALLING_MODELS: list[str] = [
        "gpt-4.1",
        "gpt-5-2025-08-07",
        "gpt-5-mini-2025-08-07",
        "gpt-5-nano-2025-08-07",
        "gpt-5-2-2025-12-11",
        "gpt-5.4-2026-03-05",
    ]
    # LiteLLM args
    LLM_PROXY_MODE: Literal["internal", "lite_llm"] = "internal"
    LLM_PROXY_ENABLED: bool = False
    LLM_PROXY_BUDGET_CHECK_ENABLED: bool = True
    LLM_PROXY_EMBEDDINGS_DISABLED: bool = False  # Bypass LiteLLM for embeddings, use native providers
    LITE_LLM_URL: str = ""
    LITE_LLM_APP_KEY: str = ""
    # Optional key for proxy endpoints used by coding agents; falls back to LITE_LLM_APP_KEY
    LITE_LLM_PROXY_APP_KEY: str = ""
    LITE_LLM_MASTER_KEY: str = ""
    LLM_PROXY_TIMEOUT: int = 300
    LLM_PROXY_LANGFUSE_TRACES: bool = False
    LLM_PROXY_TRACK_USAGE: bool = True
    # LiteLLM model tagging configuration
    # The comma-separated list of project names to be used as tags for "x-litellm-tags" HTTP Header
    LITE_LLM_PROJECTS_TO_TAGS_LIST: str = ""
    # The default value to be used for "x-litellm-tags" HTTP Header when no project is specified or matched
    LITE_LLM_TAGS_HEADER_VALUE: str = "default"
    # LiteLLM Proxy Endpoints Configuration
    # Each endpoint can be configured with specific HTTP methods
    # Based on official spec: https://litellm-api.up.railway.app/
    # Format: List of dicts with 'path' and 'methods' keys
    # Note: Can be overridden via environment variable as JSON string
    LITE_LLM_PROXY_ENDPOINTS: list[dict] = [
        # Chat & Completions (OpenAI-compatible) - both /v1 and non-/v1 versions
        {"path": "/v1/chat/completions", "methods": ["POST"]},
        {"path": "/chat/completions", "methods": ["POST"]},
        {"path": "/v1/completions", "methods": ["POST"]},
        {"path": "/completions", "methods": ["POST"]},
        # Messages (Claude/Anthropic API - required for Claude Code CLI)
        # See: https://docs.anthropic.com/en/api/messages
        {"path": "/v1/messages", "methods": ["POST"]},
        {"path": "/messages", "methods": ["POST"]},
        {"path": "/v1/messages/count_tokens", "methods": ["POST"]},
        {"path": "/messages/count_tokens", "methods": ["POST"]},
        # Responses (required for Codex CLI)
        {"path": "/v1/responses", "methods": ["POST"]},
        {"path": "/responses", "methods": ["POST"]},
        # Embeddings
        {"path": "/v1/embeddings", "methods": ["POST"]},
        {"path": "/embeddings", "methods": ["POST"]},
        # Endpoints that used codemie-cli when connecting via litellm provider
        {"path": "/v1/health", "methods": ["GET"]},
        {"path": "/health", "methods": ["GET"]},
        {"path": "/v1/models", "methods": ["GET"]},
        {"path": "/models", "methods": ["GET"]},
        # Google/Gemini API endpoints (with path parameters)
        # See: https://ai.google.dev/api/generate-content
        {"path": "/v1/models/{model_name}:generateContent", "methods": ["POST"]},
        {"path": "/models/{model_name}:generateContent", "methods": ["POST"]},
        {"path": "/v1/models/{model_name}:streamGenerateContent", "methods": ["POST"]},
        {"path": "/models/{model_name}:streamGenerateContent", "methods": ["POST"]},
        {"path": "/v1/models/{model_name}:countTokens", "methods": ["POST"]},
        {"path": "/models/{model_name}:countTokens", "methods": ["POST"]},
        # v1beta endpoints for Gemini (no /v1 prefix for these)
        {"path": "/v1beta/models/{model_name}:generateContent", "methods": ["POST"]},
        {"path": "/v1beta/models/{model_name}:streamGenerateContent", "methods": ["POST"]},
    ]
    # List of model name aliases for premium/costly model detection (partial match, case-insensitive).
    # A model is considered premium if its name contains any alias (e.g. ["opus", "claude-4"]).
    # Only active when a budget with budget_category="premium_models" is in budgets config.
    LITELLM_PREMIUM_MODELS_ALIASES: list[str] = []
    # Minimum supported CodeMie CLI version for proxy requests.
    CODEMIE_MIN_CLI_VERSION: str = "0.0.47"

    # LiteLLM Cache and Optimization Configuration
    LITELLM_CUSTOMER_CACHE_TTL: int = 300  # 5 minutes - cache customer info TTL
    LITELLM_MODELS_CACHE_TTL: int = 1800  # 30 minutes - cache available models TTL
    LITELLM_REQUEST_TIMEOUT: float = 5.0  # 5 seconds - HTTP request timeout
    LITELLM_FAIL_OPEN_ON_503: bool = True  # Fail open - allow requests on 503 errors

    AI_AGENT_RECURSION_LIMIT: int = 150
    AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED: bool = True
    AI_AGENT_HISTORY_REPLAY_FULL_TOOL_TURNS: int = 4
    AI_AGENT_HISTORY_REPLAY_SUMMARIZED_TOOL_TURNS: int = 6
    AI_AGENT_HISTORY_REPLAY_FULL_TOOL_RESULT_LIMIT: int = 2500
    AI_AGENT_HISTORY_REPLAY_SUMMARY_TOOL_RESULT_LIMIT: int = 600
    AI_AGENT_HISTORY_REPLAY_LOG_CONTENT_LIMIT: int = 800
    AI_AGENT_HISTORY_COMPACTION_ENABLED: bool = False
    AI_AGENT_HISTORY_COMPACTION_TOKEN_LIMIT: int = 120000
    AI_AGENT_HISTORY_COMPACTION_TRIGGER_RATE: float = 0.8
    AI_AGENT_HISTORY_COMPACTION_TARGET_RATE: float = 0.5
    AI_AGENT_HISTORY_COMPACTION_PRESERVE_GROUPS: int = 6
    AI_AGENT_HISTORY_COMPACTION_BATCH_TOKEN_LIMIT: int = 24000
    AI_AGENT_HISTORY_COMPACTION_SUMMARY_PREFIX: str = "[Compacted conversation summary]"

    # AICE integration configuration
    CODE_ANALYSIS_SERVICE_PROVIDER_NAME: str = "CodeAnalysisServiceProvider"
    CODE_EXPLORATION_SERVICE_PROVIDER_NAME: str = "CodeExplorationServiceProvider"

    # TOOLS
    MAX_CODE_TOOLS_OUTPUT_SIZE: int = 50000

    # Smart Tool Selection Configuration
    # Enables both:
    # 1. Dynamic tool selection for agents (selecting subset from available tools)
    # 2. Smart tool lookup when no toolkits configured (finding relevant tools from all available)
    TOOL_SELECTION_ENABLED: bool = False
    # Minimum number of tools required to trigger smart selection (below this uses all tools)
    TOOL_SELECTION_THRESHOLD: int = 3
    # Maximum number of tools to select per query
    TOOL_SELECTION_LIMIT: int = 3
    # Tool search configuration for semantic tool indexing and selection
    TOOLS_INDEX_NAME: str = "codemie_tools"

    # Platform datasources configuration
    PLATFORM_MARKETPLACE_DATASOURCE_NAME: str = "marketplace_assistants"
    PLATFORM_DATASOURCES_SYNC_ENABLED: bool = False

    MARKETPLACE_LLM_VALIDATION_ON_PUBLISH_ENABLED: bool = True

    # Memory profiling configuration
    MEMORY_PROFILING_ENABLED: bool = False  # Enable tracemalloc-based memory profiling
    MEMORY_PROFILING_INTERVAL_MINUTES: int = (
        10  # Interval between automatic snapshots (reduced from 5 to 10 for lower CPU impact)
    )
    MEMORY_PROFILING_DETAIL_LEVEL: str = (
        "file"  # Detail level: "file" (fast, groups by file), "line" (slower, shows exact lines)
    )
    MEMORY_PROFILING_SNAPSHOT_PREFIX: str = "memory_snapshots"  # Prefix path for snapshot storage

    # Conversation Analysis Configuration
    CONVERSATION_ANALYSIS_ENABLED: bool = False
    CONVERSATION_ANALYSIS_SCHEDULE: str = "0 0 * * *"  # Midnight daily (cron format)
    CONVERSATION_ANALYSIS_START_DATE: str = "2025-12-01"  # Only analyze conversations from this date onwards
    CONVERSATION_ANALYSIS_LOOKBACK_DAYS: int = 1  # Analyze conversations older than N days
    CONVERSATION_ANALYSIS_BATCH_SIZE: int = 20  # Conversations per batch per pod
    CONVERSATION_ANALYSIS_MAX_RETRIES: int = 3  # Max retry attempts for failed analyses
    CONVERSATION_ANALYSIS_LLM_MODEL: str = "gemini-3-flash"
    CONVERSATION_ANALYSIS_PROJECTS_FILTER: list[str] = ["demo", "codemie", "epm-cdme"]  # Project filter

    # Leaderboard Configuration
    LEADERBOARD_ENABLED: bool = False  # Enables the leaderboard nightly computation job
    LEADERBOARD_SCHEDULE: str = "0 2 * * *"  # Cron schedule (UTC) — 2 AM daily
    LEADERBOARD_PERIOD_DAYS: int = 30  # Rolling period for scoring window
    LEADERBOARD_KEEP_ROLLING_SNAPSHOTS: int = 30  # Number of rolling snapshots to retain
    LEADERBOARD_KEEP_ADHOC_SNAPSHOTS: int = 10  # Number of non-final adhoc/manual snapshots to retain

    # LiteLLM Spend Collector Configuration
    LITELLM_SPEND_COLLECTOR_ENABLED: bool = False  # Enables the spend collector APScheduler job
    LITELLM_SPEND_COLLECTOR_SCHEDULE: str = (
        "0 23 * * *"  # Cron schedule (UTC) for the spend collector — nightly at 11 PM
    )

    model_config = SettingsConfigDict(env_file=find_dotenv(".env", raise_error_if_not_found=False), extra="ignore")

    GLOBAL_FALLBACK_MSG: str = "External Service Exception"
    # ===========================================
    # LiteLLM custom error responses to user
    # ===========================================
    LITELLM_MSG_BUDGET_EXCEEDED: str = (
        "Your LLM usage budget has been exceeded. Please contact your administrator to increase the limit."
    )
    LITELLM_MSG_RATE_LIMITED: str = (
        "The LLM service is temporarily overloaded due to rate limiting. Please wait a moment and try again."
    )
    LITELLM_MSG_TPM_LIMIT: str = (
        "The tokens-per-minute limit for the LLM has been reached. Please wait a moment and try again."
    )
    LITELLM_MSG_RPM_LIMIT: str = (
        "The requests-per-minute limit for the LLM has been reached. Please wait a moment and try again."
    )
    LITELLM_MSG_UNAVAILABLE: str = (
        "The LLM service is currently unavailable. Please try again later or contact support."
    )
    LITELLM_MSG_INTERNAL_ERROR: str = (
        "An internal error occurred in the LLM service. Please try again later or contact support."
    )
    LITELLM_MSG_CONTEXT_LENGTH: str = (
        "The input is too long for the selected model's context window. Please reduce the input size and try again."
    )
    LITELLM_MSG_CONTENT_POLICY: str = (
        "The request was rejected due to the LLM provider's content policy. Please modify your input and try again."
    )
    LITELLM_MSG_AUTHENTICATION: str = "LLM authentication failed. Please verify your credentials or contact support."
    LITELLM_MSG_PERMISSION_DENIED: str = (
        "Access to the LLM model was denied. Please check your permissions or contact support."
    )
    LITELLM_MSG_TIMEOUT: str = "The LLM request timed out. Please try again."
    LITELLM_MSG_TRANSITIVE_ERROR: str = (
        "A transient connectivity error occurred with the LLM service. Please try again."
    )
    LITELLM_MSG_INVALID_REQUEST: str = (
        "The LLM request was invalid or could not be processed. Please check your input and try again."
    )
    LITELLM_MSG_UNKNOWN_ERROR: str = "An unexpected LLM error occurred. Please try again or contact support."
    # ===========================================
    # Agent custom error responses to user
    # ===========================================
    AGENT_MSG_TIMEOUT: str = "The agent request timed out. Please try again."
    AGENT_MSG_TOKEN_LIMIT: str = (
        "The configured output token limit was reached. Please try a shorter conversation or reduce context."
    )
    AGENT_MSG_BUDGET_EXCEEDED: str = "Budget limit has been reached. Please contact your administrator."
    AGENT_MSG_CALLBACK_FAILURE: str = "An agent callback failed. Please try again or contact support."
    AGENT_MSG_NETWORK_ERROR: str = "A network error occurred during agent execution. Please try again."
    AGENT_MSG_CONFIGURATION_ERROR: str = "Agent configuration is invalid. Please contact support."
    AGENT_MSG_INTERNAL_ERROR: str = "An internal agent error occurred. Please try again or contact support."
    AGENT_MSG_FALLBACK: str = "An agent error occurred. Please try again or contact support."

    # MDDA-AIAD feature flags
    HIDE_AGENT_STREAMING_EXCEPTIONS: bool = False

    # HHTP requests configuration
    HTTPS_VERIFY_SSL: bool = True  # verify SSL context of request, for development and testing configure to `False`

    @property
    def verbose(self) -> bool:
        """Verbose setting used for LLM logging"""
        return False

    def to_safe_dict(self):
        """
        Convert the config to a dictionary and exclude sensitive information.
        """
        sensitive_keywords = ['key', 'password', 'secret', 'token']
        sensitive_keys = [
            "AZURE_STORAGE_CONNECTION_STRING",
            "PG_URL",
            "ELASTIC_URL",
        ]
        config_dict = self.model_dump()
        safe_dict = {}

        for k, v in config_dict.items():
            if isinstance(v, Path):
                v = str(v)
            if not any(k.lower().endswith(keyword) for keyword in sensitive_keywords) and k not in sensitive_keys:
                safe_dict[k] = v
            else:
                safe_dict[k] = "******"  # Mask sensitive information

        return safe_dict

    @property
    def is_local(self) -> bool:
        """Check if the environment is local"""
        return self.ENV == ENV_LOCAL


class HealthCheckFilter(logging.Filter):
    """
    Filter healthcheck logs from logs to avoid spamming.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.args[2].endswith("healthcheck")


load_dotenv(find_dotenv(".env", raise_error_if_not_found=False))
config = Config()  # type: ignore
logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())
