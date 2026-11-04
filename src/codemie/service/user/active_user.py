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

"""Single home of the rule: a deactivated user cannot be granted anything new.

Decided for EPMCDME-13928:
- Refused with 409 "User is deactivated": project access and project role, knowledge base access,
  default project, project ownership transfer, budgets (single and bulk) and platform roles
  (admin, maintainer, auditor set to true).
- Always allowed, because admins need them for cleanup: removing project or KB access, clearing a
  budget, revoking a platform role, and reactivating the user.
- An unknown user is 404 "User not found" everywhere.
"""

from __future__ import annotations

from sqlmodel import Session

from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.user_repository import UserRepository, user_repository
from codemie.rest_api.models.user_management import UserDB

USER_NOT_FOUND = "User not found"
USER_DEACTIVATED = "User is deactivated"


def require_active_user(session: Session, user_id: str, repo: UserRepository = user_repository) -> UserDB:
    """Load a grant target: 404 when unknown, 409 when deactivated.

    Cleanup paths (removing access, clearing budgets) and reactivation must not use this.
    Callers pass their own repository reference so each service keeps a single mock seam.
    """
    user = repo.get_active_by_id(session, user_id)
    if user:
        return user
    if repo.get_by_id(session, user_id) is None:
        raise ExtendedHTTPException(
            code=404,
            message=USER_NOT_FOUND,
            details=f"No user found with ID '{user_id}'",
            help="Verify the user ID and try again",
        )
    raise ExtendedHTTPException(code=409, message=USER_DEACTIVATED)
