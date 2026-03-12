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

import hashlib
import hmac
from typing import Optional

from fastapi import HTTPException, Request, status

from codemie.configs import logger


class GitHubWebhookSecurity:
    """
    GitHub webhook signature verification service.

    Implements GitHub's webhook security standard using HMAC-based signatures
    to verify the authenticity and integrity of webhook payloads.
    """

    HEADER_SIGNATURE_256 = "X-Hub-Signature-256"
    HEADER_SIGNATURE_1 = "X-Hub-Signature"
    HEADER_EVENT = "X-GitHub-Event"
    HEADER_DELIVERY = "X-GitHub-Delivery"
    HEADER_HOOK_ID = "X-GitHub-Hook-ID"

    PREFIX_SHA256 = "sha256="
    PREFIX_SHA1 = "sha1="

    ERROR_MISSING_SIGNATURE = "Missing GitHub webhook signature header"
    ERROR_INVALID_SIGNATURE = "Invalid GitHub webhook signature"
    ERROR_MISSING_SECRET = "GitHub webhook secret not configured"
    ERROR_EVENT_NOT_ALLOWED = "GitHub event type '{}' is not allowed for this webhook"

    @classmethod
    def is_github_webhook(cls, request: Request) -> bool:
        """
        Determine if the request is a GitHub webhook.

        Checks for presence of GitHub-specific headers to identify
        if the request originates from GitHub webhooks.

        Args:
            request: FastAPI Request object

        Returns:
            bool: True if request appears to be from GitHub webhooks
        """
        has_signature = cls.HEADER_SIGNATURE_256 in request.headers or cls.HEADER_SIGNATURE_1 in request.headers
        has_event = cls.HEADER_EVENT in request.headers
        has_github_agent = "GitHub-Hookshot" in request.headers.get("User-Agent", "")

        return has_signature or has_event or has_github_agent

    @classmethod
    def verify_signature(cls, request: Request, secret: str, payload: bytes, require_sha256: bool = True) -> None:
        """
        Verify GitHub webhook signature.

        Verifies the HMAC signature sent by GitHub to ensure the webhook
        request is authentic and has not been tampered with.

        Args:
            request: FastAPI Request object containing headers
            secret: The webhook secret configured in GitHub
            payload: Raw request body as bytes
            require_sha256: If True, require SHA-256 signature (recommended)

        Returns:
            None: Returns nothing on successful verification

        Raises:
            HTTPException: If signature is missing, invalid, or secret is not configured

        Security:
            - Uses constant-time comparison to prevent timing attacks
            - Verifies SHA-256 signature by default (more secure than SHA-1)
            - Supports SHA-1 for backward compatibility
        """
        if not secret:
            logger.error("GitHub webhook secret is not configured")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=cls.ERROR_MISSING_SECRET)

        delivery_id = request.headers.get(cls.HEADER_DELIVERY, "unknown")

        # Try SHA-256 first (recommended by GitHub)
        signature_256 = request.headers.get(cls.HEADER_SIGNATURE_256)
        if signature_256:
            if cls._verify_sha256_signature(signature_256, secret, payload):
                logger.info(f"GitHub webhook signature verified (SHA-256). Delivery ID: {delivery_id}")
                return
            else:
                logger.warning(f"GitHub webhook signature verification failed (SHA-256). Delivery ID: {delivery_id}")
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=cls.ERROR_INVALID_SIGNATURE)

        # Fall back to SHA-1 if SHA-256 not present (legacy support)
        if not require_sha256:
            signature_1 = request.headers.get(cls.HEADER_SIGNATURE_1)
            if signature_1:
                if cls._verify_sha1_signature(signature_1, secret, payload):
                    logger.warning(
                        f"GitHub webhook using legacy SHA-1 signature. "
                        f"Delivery ID: {delivery_id}. "
                        "Consider updating to SHA-256 for better security."
                    )
                    return
                else:
                    logger.warning(f"GitHub webhook signature verification failed (SHA-1). Delivery ID: {delivery_id}")
                    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=cls.ERROR_INVALID_SIGNATURE)

        logger.error(
            f"Missing GitHub webhook signature header. "
            f"Expected: {cls.HEADER_SIGNATURE_256} or {cls.HEADER_SIGNATURE_1}. "
            f"Delivery ID: {delivery_id}"
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=cls.ERROR_MISSING_SIGNATURE)

    @classmethod
    def _verify_sha256_signature(cls, signature_header: str, secret: str, payload: bytes) -> bool:
        """
        Verify HMAC-SHA256 signature.

        Args:
            signature_header: The X-Hub-Signature-256 header value
            secret: The webhook secret
            payload: Raw request body

        Returns:
            bool: True if signature is valid, False otherwise
        """
        if not signature_header.startswith(cls.PREFIX_SHA256):
            logger.warning(f"Invalid SHA-256 signature format: {signature_header[:20]}...")
            return False

        received_signature = signature_header[len(cls.PREFIX_SHA256) :]

        expected_signature = cls._calculate_signature(secret, payload, hashlib.sha256)

        return hmac.compare_digest(received_signature, expected_signature)

    @classmethod
    def _verify_sha1_signature(cls, signature_header: str, secret: str, payload: bytes) -> bool:
        """
        Verify HMAC-SHA1 signature (legacy GitHub webhook support).

        Security Note:
            SHA-1 is used here ONLY for backward compatibility with legacy GitHub webhooks
            that still send X-Hub-Signature (SHA-1) instead of X-Hub-Signature-256 (SHA-256).

            This is considered ACCEPTABLE for webhook verification because:
            1. GitHub officially still supports SHA-1 for legacy webhooks
            2. HMAC-SHA1 is more secure than plain SHA-1 (adds secret key)
            3. We default to SHA-256 (only use SHA-1 as fallback when require_sha256=False)
            4. Only used for external webhook verification, not password hashing or token generation

            Reference: https://docs.github.com/webhooks/using-webhooks/validating-webhook-deliveries

        Args:
            signature_header: The X-Hub-Signature header value
            secret: The webhook secret
            payload: Raw request body

        Returns:
            bool: True if signature is valid, False otherwise
        """
        if not signature_header.startswith(cls.PREFIX_SHA1):
            logger.warning(f"Invalid SHA-1 signature format: {signature_header[:20]}...")
            return False

        received_signature = signature_header[len(cls.PREFIX_SHA1) :]

        # SonarQube: SHA-1 is acceptable here for GitHub legacy webhook compatibility
        # This is HMAC-SHA1 for webhook verification, not password/token hashing
        # GitHub officially supports this: https://docs.github.com/webhooks
        expected_signature = cls._calculate_signature(secret, payload, hashlib.sha1)  # noqa: S324

        return hmac.compare_digest(received_signature, expected_signature)

    @classmethod
    def _calculate_signature(cls, secret: str, payload: bytes, hash_algorithm) -> str:
        """
        Calculate HMAC signature for the payload.

        Args:
            secret: The webhook secret
            payload: Raw request body
            hash_algorithm: Hash algorithm (hashlib.sha256 or hashlib.sha1)

        Returns:
            str: Hexadecimal representation of the HMAC signature
        """
        mac = hmac.new(key=secret.encode('utf-8'), msg=payload, digestmod=hash_algorithm)
        return mac.hexdigest()

    @classmethod
    def validate_event_type(cls, request: Request, allowed_events: Optional[list] = None) -> None:
        """
        Validate that the GitHub event type is allowed.

        Args:
            request: FastAPI Request object
            allowed_events: List of allowed event types (e.g., ['push', 'pull_request'])
                           If None or empty, all events are allowed

        Returns:
            None: Returns nothing on successful validation

        Raises:
            HTTPException: If event type is not allowed
        """
        if allowed_events is None or len(allowed_events) == 0:
            return

        event_type = request.headers.get(cls.HEADER_EVENT)

        if not event_type:
            logger.warning("GitHub webhook request missing X-GitHub-Event header")
            return

        if event_type not in allowed_events:
            logger.warning(
                f"GitHub webhook event '{event_type}' not in allowed list: {allowed_events}. "
                f"Delivery ID: {request.headers.get(cls.HEADER_DELIVERY, 'unknown')}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=cls.ERROR_EVENT_NOT_ALLOWED.format(event_type)
            )

    @classmethod
    def extract_github_metadata(cls, request: Request) -> dict:
        """
        Extract GitHub-specific metadata from webhook request headers.

        Args:
            request: FastAPI Request object

        Returns:
            dict: GitHub metadata including event type, delivery ID, and hook ID
        """
        return {
            "event": request.headers.get(cls.HEADER_EVENT),
            "delivery_id": request.headers.get(cls.HEADER_DELIVERY),
            "hook_id": request.headers.get(cls.HEADER_HOOK_ID),
            "user_agent": request.headers.get("User-Agent"),
        }
