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

"""User type validation for IDP attributes (Story 4: EPMCDME-10160)

Implements strict, fail-closed validation of user_type attribute from Identity Providers.
Only 'regular' and 'external' values are accepted (case-insensitive), missing defaults
to 'regular', and invalid values reject authentication with 401.
"""

from typing import Any, Optional
from fastapi import status

from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException


VALID_USER_TYPES = {'regular', 'external'}


def validate_user_type(value: Any, idp_context: Optional[dict] = None) -> str:
    """Validate and normalize user_type attribute from IDP

    Business Rules (FR-6.2, AC-7):
    - Missing attribute: Default to 'regular'
    - Valid values ('regular', 'external'): Accept and normalize to lowercase
    - Invalid values: Reject authentication with 401

    Args:
        value: user_type attribute from IDP JWT claims (can be None, str, or other types)
        idp_context: Optional context for logging (provider, source, subject_id_hash)

    Returns:
        str: Normalized user_type ('regular' or 'external')

    Raises:
        ExtendedHTTPException: 401 if value is invalid

    Examples:
        >>> validate_user_type(None)
        'regular'
        >>> validate_user_type('External')
        'external'
        >>> validate_user_type('REGULAR')
        'regular'
        >>> validate_user_type('unknown')  # Raises 401
    """
    # Extract context for logging (default to empty dict if not provided)
    ctx = idp_context or {}
    provider = ctx.get('provider', 'unknown')
    source = ctx.get('source', 'unknown')
    subject_hash = ctx.get('subject_id_hash', 'unknown')

    # Missing attribute: default to regular (AC-7, FR-6.2)
    if value is None:
        return 'regular'

    # Invalid type: reject (AC-7)
    if not isinstance(value, str):
        # Log with IDP context (no PII) - Story requirement: "relevant IDP claim details"
        logger.error(
            f"Invalid user_type attribute type from IDP: provider={provider}, source={source}, "
            f"subject_hash={subject_hash}, expected=string, got={type(value).__name__}, value={repr(value)}"
        )
        # Story-mandated error message format
        raise ExtendedHTTPException(
            code=status.HTTP_401_UNAUTHORIZED,
            message=f"Invalid user_type attribute from IDP. Expected 'regular' or 'external', got: {repr(value)}",
            details=f"The user_type attribute must be a string, received {type(value).__name__}.",
            help="Contact your administrator to correct the IDP configuration.",
        )

    # Normalize to lowercase for case-insensitive comparison
    normalized = value.strip().lower()

    # Valid values: accept (AC-7, FR-6.2)
    if normalized in VALID_USER_TYPES:
        return normalized

    # Invalid value: reject authentication (AC-7, NFR-3)
    # Log with IDP context (no PII) - Story requirement: "relevant IDP claim details"
    logger.error(
        f"Invalid user_type attribute value from IDP: provider={provider}, source={source}, "
        f"subject_hash={subject_hash}, expected='regular' or 'external', got={repr(value)}"
    )
    # Story-mandated error message format
    raise ExtendedHTTPException(
        code=status.HTTP_401_UNAUTHORIZED,
        message=f"Invalid user_type attribute from IDP. Expected 'regular' or 'external', got: {value}",
        details=f"The user_type attribute value '{value}' is not recognized. "
        "Only 'regular' and 'external' are supported.",
        help="Contact your administrator to correct the IDP configuration.",
    )
