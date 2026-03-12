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

import base64
import functools
import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)
PROTECTED_HEADERS = frozenset({'authorization'})


def _merge_custom_headers(custom_headers: dict[str, str]) -> dict[str, str]:
    """
    Merge custom headers while protecting critical authentication headers.

    Args:
        custom_headers: Dictionary of custom headers to merge

    Returns:
        Dictionary of validated custom headers

    Raises:
        None - Protected headers are silently ignored with warning
    """
    if not custom_headers:
        return {}

    merged_headers = {}

    for header_name, header_value in custom_headers.items():
        if header_name.lower() in PROTECTED_HEADERS:
            logger.warning(f"Attempted to override protected header '{header_name}' - ignoring")
            continue

        merged_headers[header_name] = header_value
        logger.debug(f"Added custom header: {header_name}")

    return merged_headers


def _validate_custom_headers(custom_headers: Optional[dict[str, str]]) -> None:
    """
    Validate custom headers for security compliance.

    Args:
        custom_headers: Dictionary of headers to validate

    Raises:
        ValueError: If protected headers are attempted to be overridden
    """
    if not custom_headers:
        return

    for header_name in custom_headers:
        if header_name.lower() in PROTECTED_HEADERS:
            raise ValueError(f"Cannot override protected header: {header_name}")


def _build_headers(
    default_headers: dict[str, str], access_token: str, custom_headers: Optional[dict[str, str]] = None
) -> dict[str, str]:
    """
    Build request headers with optional custom headers.

    Args:
        custom_headers: Optional custom headers to merge

    Returns:
        Complete headers dictionary for the request
    """
    headers = default_headers.copy()
    headers["Authorization"] = f"Bearer {access_token}"

    if custom_headers:
        headers.update(_merge_custom_headers(custom_headers))

    return headers


def file_response_handler(execute_method):
    """
    Decorator to handle responses and only decode Base64-encoded file content.
    Why Calculate Size as `original_size * 1/4`:
    -------------------------------------------
    After decoding Base64, the original content is processed for tokenization. Base64 inflates
    file size by 4/3 (33% larger), so decoding reduces it back to 3/4 of the encoded size.
    To estimate the tokenization size, further adjustments on calculation is required 1/3 and depends on the
    encoding logic (e.g., tiktoken). This calculation ensures efficient handling of large files
    while respecting tokenization limits.

    """

    @functools.wraps(execute_method)
    def wrapper(*args, **kwargs):
        tool_instance = args[0]
        # Execute the original execute method
        response = execute_method(*args, **kwargs)

        if not isinstance(response, dict) or response.get("type") != "file":
            return response  # Return the original response if not a file

        original_size = response.get("size", 0)
        encoding = response.get("encoding", None)

        if encoding != "base64":
            logger.info("File encoding is not Base64. No decoding performed.")
            return response

        # Estimate Base64-encoded size and check against the limit
        estimated_encoded_size = math.floor(original_size * 1 / 4)
        if estimated_encoded_size > tool_instance.tokens_size_limit:
            msg = (
                "File too large for Base64 decoding. "
                f"Estimated Base64 size: {estimated_encoded_size} tokens, limit: {tool_instance.tokens_size_limit}."
            )
            logger.warning(msg)
            response["error"] = msg

            return response

        # Attempt to decode the Base64 content
        try:
            if response.get("content"):
                decoded_content = base64.b64decode(response["content"]).decode("utf-8")
                response["content"] = decoded_content  # Replace encoded content with decoded content
        except UnicodeDecodeError as e:
            logger.error(f"Failed to decode Base64 content: {e}")
            response["error"] = "Failed to decode Base64 content: Invalid UTF-8 encoding"
        except Exception as e:
            logger.error(f"Failed to decode Base64 content: {e}")
            response["error"] = "Failed to decode Base64 content: Incorrect padding"

        return response

    return wrapper
