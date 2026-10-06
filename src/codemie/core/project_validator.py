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

"""Project validation utilities.

Provides validation logic to ensure member-driven requests carry a real project.
Background consumers identified by integration headers are exempt.
"""

from typing import Any

from codemie.core.constants import HEADER_CODEMIE_INTEGRATION
from codemie.core.exceptions import ExtendedHTTPException


class ProjectRequiredException(ExtendedHTTPException):
    """Raised when a real project is required but not provided."""

    def __init__(self, message: str = "A real project is required for this operation"):
        """Initialize the exception with a message."""
        super().__init__(
            code=400,
            message=message,
            details="Member-driven requests must specify a real project identifier.",
        )


def require_valid_project(project: str | None) -> str:
    """Validate that a project is present and not empty.

    Args:
        project: The project identifier to validate.

    Returns:
        The validated project identifier.

    Raises:
        ProjectRequiredException: If project is None or empty.
    """
    if not project or not project.strip():
        raise ProjectRequiredException("A real project is required for this operation")

    return project


def is_background_consumer(headers: dict[str, Any] | None) -> bool:
    """Check if request is from a background consumer.

    Background consumers (datasource indexing, skill generators, etc.) are
    identified by the presence of HEADER_CODEMIE_INTEGRATION with a non-empty value.

    Args:
        headers: Request headers dict (case-insensitive).

    Returns:
        True if request has a valid integration header, False otherwise.
    """
    if headers is None:
        return False

    target = HEADER_CODEMIE_INTEGRATION.casefold()
    integration_id = next((v for k, v in headers.items() if k.casefold() == target), None)

    return bool(integration_id and str(integration_id).strip())
