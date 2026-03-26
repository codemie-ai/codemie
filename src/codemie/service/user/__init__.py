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

"""User management services package.

This package provides domain-specific services for user management:
- application_service: Application/project creation with uniqueness enforcement
- authentication_service: User authentication (local, IDP, dev header)
- registration_service: User registration and email verification
- password_management_service: Password operations (change, reset)
- user_management_service: User CRUD and admin operations
- user_access_service: Project and KB access management
- user_profile_service: Profile self-service operations
"""

from codemie.service.user.application_service import (
    ApplicationService,
    application_service,
)
from codemie.service.user.authentication_service import (
    AuthenticationService,
    authentication_service,
)
from codemie.service.user.registration_service import (
    RegistrationService,
    registration_service,
)
from codemie.service.user.password_management_service import (
    PasswordManagementService,
    password_management_service,
)
from codemie.service.user.user_management_service import (
    UserManagementService,
    user_management_service,
)
from codemie.service.user.user_access_service import (
    UserAccessService,
    user_access_service,
)
from codemie.service.user.user_profile_service import (
    UserProfileService,
    user_profile_service,
)

__all__ = [
    # Classes
    "ApplicationService",
    "AuthenticationService",
    "RegistrationService",
    "PasswordManagementService",
    "UserManagementService",
    "UserAccessService",
    "UserProfileService",
    # Singleton instances
    "application_service",
    "authentication_service",
    "registration_service",
    "password_management_service",
    "user_management_service",
    "user_access_service",
    "user_profile_service",
]
