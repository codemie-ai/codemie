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
from .models import KeycloakConfig


KEYCLOAK_TOOL = ToolMetadata(
    name="keycloak",
    description="""
    Generic Keycloak Admin API tool for interacting with Keycloak identity and access management server.

    Supports comprehensive Keycloak Admin API operations including:
    - User management (create, read, update, delete users)
    - Role management (realm roles, client roles, role assignments)
    - Client management (create, configure, delete clients)
    - Group management (create groups, manage memberships)
    - Realm configuration and management
    - Session management and monitoring
    - Identity provider configuration
    - Authentication flow management

    Required parameters:
    1. method: HTTP method (GET, POST, PUT, DELETE, PATCH)
    2. relative_url: Keycloak Admin API endpoint starting with '/' (e.g., '/users', '/roles')
    3. params: Optional JSON parameters for request body

    Important notes:
    - For GET requests, include query parameters in the URL
    - For POST/PUT/PATCH, provide data in the params field as JSON string
    - All endpoints are relative to /admin/realms/{realm}
    - Refer to Keycloak Admin REST API documentation for available endpoints

    Examples:
    - List users: GET /users
    - Get user: GET /users/{user-id}
    - Create user: POST /users with params containing user data
    - Update user: PUT /users/{user-id} with params
    - Delete user: DELETE /users/{user-id}
    - List roles: GET /roles
    - Assign role: POST /users/{user-id}/role-mappings/realm with role data
    """.strip(),
    label="Keycloak",
    user_description="""
    Provides comprehensive access to Keycloak Admin API for identity and access management.

    This tool enables AI assistants to perform various identity and access management operations:
    - **User Management**: Create, update, delete, and search for users
    - **Role Management**: Manage realm and client roles, assign roles to users
    - **Client Management**: Configure OAuth/OIDC clients and service accounts
    - **Group Management**: Organize users into groups and manage group memberships
    - **Authentication**: Configure authentication flows and identity providers
    - **Sessions**: Monitor and manage user sessions
    - **Realm Configuration**: Manage realm settings and policies

    **Configuration Requirements**:
    Before using this tool, configure the Keycloak integration with:
    1. **Base URL**: Your Keycloak server URL (e.g., https://keycloak.example.com)
    2. **Realm**: The realm to manage (e.g., 'master', 'my-realm')
    3. **Client ID**: Service account client ID with admin permissions
    4. **Client Secret**: Client secret for authentication
    5. **Timeout** (optional): Request timeout in seconds (default: 30)

    **Use Cases**:
    - Automate user provisioning and deprovisioning
    - Manage access control and permissions
    - Configure SSO and authentication flows
    - Monitor and manage user sessions
    - Audit and report on identity data
    - Integrate identity management with CI/CD pipelines

    **Security Note**:
    Ensure the service account has appropriate admin permissions in Keycloak.
    The tool uses client credentials grant flow for authentication.
    """.strip(),
    settings_config=True,
    config_class=KeycloakConfig,
)
