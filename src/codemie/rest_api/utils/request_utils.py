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

"""Utility functions for processing FastAPI requests.

This module provides shared utilities for extracting and processing request data,
including custom header extraction for MCP server propagation.
"""

from fastapi import Request

from codemie.configs import config, logger


def extract_custom_headers(raw_request: Request, propagate: bool) -> dict[str, str] | None:
    """
    Extract X-* headers from incoming request for MCP propagation.

    This function extracts custom headers (prefixed with X-) from the FastAPI request
    and filters out blocked headers for security reasons. The blocked headers list is
    configured via the MCP_BLOCKED_HEADERS configuration setting.

    Args:
        raw_request: The FastAPI Request object containing headers
        propagate: Whether to propagate headers (typically from request.propagate_headers)

    Returns:
        Dictionary of filtered headers or None if propagation is disabled or no headers found

    Example:
        >>> request = Request(...)
        >>> headers = extract_custom_headers(request, propagate=True)
        >>> # Returns: {"X-Tenant-ID": "abc123", "X-Auth-Token": "xyz789"}
    """
    if not propagate:
        return None

    # Extract X-* headers (case-insensitive)
    custom_headers = {key: value for key, value in raw_request.headers.items() if key.lower().startswith('x-')}

    if not custom_headers:
        return None

    # Parse blocked headers configuration (comma-separated string)
    blocked_headers = {h.strip().lower() for h in config.MCP_BLOCKED_HEADERS.split(',')}

    # Filter out blocked headers
    filtered_headers = {key: value for key, value in custom_headers.items() if key.lower() not in blocked_headers}

    logger.debug(
        f"Extracted {len(filtered_headers)} custom headers for MCP propagation",
        extra={
            "header_count": len(filtered_headers),
            "propagate_headers": propagate,
        },
    )

    return filtered_headers if filtered_headers else None
