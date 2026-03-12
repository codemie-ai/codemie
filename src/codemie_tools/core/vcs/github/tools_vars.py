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

from codemie_tools.base.models import ToolMetadata
from .models import GithubConfig

GITHUB_TOOL = ToolMetadata(
    name="github",
    description="""
        Advanced GitHub REST API client tool that provides comprehensive access to GitHub's public API endpoints.

        INPUT FORMAT:
        The tool accepts a `query` parameter containing a JSON with the following structure:
        {"query":
            {
                "method": "GET|POST|PUT|DELETE|PATCH",
                "url": "https://api.github.com/...",
                "method_arguments": {object with request data},
                "custom_headers": {optional dictionary of additional HTTP headers}
            }
        }

        REQUIREMENTS:
        - `method`: HTTP method (GET, POST, PUT, DELETE, PATCH)
        - `url`: Must be a valid HTTPS URL starting with "https://api.github.com"
        - `method_arguments`: Object containing request parameters or body data
        - `custom_headers`: Optional dictionary for additional headers (authorization headers are protected)
        - The entire query must be valid JSON that passes json.loads() validation

        FEATURES:
        - Automatic Base64 file content decoding for GitHub file responses
        - Support for large files up to 70,000 tokens
        - Built-in authentication using PAT or GitHub App (auto-detected)
        - GitHub App token caching (1 hour) with automatic refresh
        - Custom header support for specialized API calls
        - Comprehensive error handling and logging

        RESPONSE FORMAT:
        Returns the raw JSON response from GitHub API, with automatic Base64 decoding for file content.

        SECURITY:
        Authorization headers are automatically managed and cannot be overridden via custom_headers.

        EXAMPLES:
        Get user information:
        {"query": {"method": "GET", "url": "https://api.github.com/user", "method_arguments": {}}}

        Get repository file:
        {"query": {"method": "GET", "url": "https://api.github.com/repos/owner/repo/contents/path", "method_arguments": {}}}

        Create issue with custom headers:
        {"query":
            {
                "method": "POST", "url": "https://api.github.com/repos/owner/repo/issues",
                "method_arguments": {"title": "Issue title", "body": "Issue body"},
                "custom_headers": {"X-GitHub-Media-Type": "github.v3+json"}
            }
        }
        """,
    label="Github",
    user_description="""
        Provides comprehensive access to the GitHub REST API with advanced features including automatic file content decoding and large file support. This tool enables the AI assistant to perform any GitHub operation available through the REST API.

        Key Capabilities:
        - Repository management (create, read, update, delete)
        - Issue and pull request operations
        - File content retrieval with automatic Base64 decoding
        - User and organization management
        - Webhook and deployment operations
        - Search across repositories, issues, and code
        - Support for large files up to 70,000 tokens

        Authentication Methods:

        Option 1: Personal Access Token (PAT)
        - Simple token-based authentication
        - Setup: Generate PAT at https://github.com/settings/tokens
        - Configuration: Provide 'token' field
        - Example config:
          {
            "token": "ghp_abc123...",
            "url": "https://api.github.com"
          }

        Option 2: GitHub App (recommended for organizations)
        - Better rate limits and organization-wide access
        - Setup:
          1. Create GitHub App: https://github.com/settings/apps/new
          2. Generate private key from app settings
          3. Install app to repositories/organization
          4. Get app_id from app settings
          5. Optionally get installation_id (or let tool auto-detect)
        - Configuration: Provide 'app_id' and 'private_key' fields
        - Example config:
          {
            "app_id": 123456,
            "private_key": "-----BEGIN RSA PRIVATE KEY-----\\nMIIE...\\n-----END RSA PRIVATE KEY-----",
            "installation_id": 12345678,  // optional
            "url": "https://api.github.com"
          }
        - Automatic token caching (1 hour) and refresh

        The tool automatically detects which authentication method to use based on provided fields.

        Use this tool when you need direct access to GitHub's REST API endpoints that may not be covered by other specialized GitHub tools.
        """.strip(),
    settings_config=True,
    config_class=GithubConfig,
)
