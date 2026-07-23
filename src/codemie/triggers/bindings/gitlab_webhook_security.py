# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

from __future__ import annotations

import hmac
import json

from fastapi import HTTPException, Request, status


class GitLabWebhookSecurity:
    """GitLab webhook detection, token verification, and MR event validation.

    Unlike GitHub (which signs the payload with HMAC-SHA256 and sends the digest
    in X-Hub-Signature-256), GitLab sends the configured *secret token verbatim*
    in the X-Gitlab-Token header. Verification is therefore a constant-time
    comparison of the header value against the stored token — there is no HMAC
    and the request body is not involved in authentication.
    """

    HEADER_EVENT = "X-Gitlab-Event"
    HEADER_DELIVERY = "X-Gitlab-Delivery"
    HEADER_TOKEN = "X-Gitlab-Token"
    USER_AGENT_PREFIX = "GitLab/"

    # The closed set of merge-request actions the event filter recognizes.
    MR_ACTIONS = {"open", "close", "merge", "update", "reopen", "approved", "unapproved"}

    INVALID_TOKEN_DETAIL = "Invalid GitLab token"

    @classmethod
    def is_gitlab_webhook(cls, request: Request) -> bool:
        """Detect a GitLab webhook from its headers / user agent."""
        headers = request.headers
        has_event = cls.HEADER_EVENT in headers
        has_token = cls.HEADER_TOKEN in headers
        has_gitlab_agent = headers.get("User-Agent", "").startswith(cls.USER_AGENT_PREFIX)
        return has_event or has_token or has_gitlab_agent

    @classmethod
    def verify_token(cls, request: Request, expected_token: str) -> bool:
        """Verify the plaintext GitLab secret token (constant-time compare)."""
        if not expected_token:
            return False
        received = request.headers.get(cls.HEADER_TOKEN, "")
        if not received:
            return False
        return hmac.compare_digest(received, expected_token)

    @classmethod
    def extract_mr_action(cls, body: bytes | str | dict[str, object]) -> str | None:
        """Return the MR action for merge_request events, else None.

        Accepts a dict, a JSON str, or raw bytes.
        """
        if isinstance(body, (bytes, bytearray)):
            try:
                body = body.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                return None
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except (json.JSONDecodeError, TypeError):
                return None
        if not isinstance(body, dict):
            return None
        if body.get("object_kind") != "merge_request":
            return None
        action = body.get("action")
        return action if action in cls.MR_ACTIONS else None

    @classmethod
    def _parse_body(cls, body: bytes | str | dict[str, object]) -> dict[str, object]:
        """Best-effort parse of a webhook body into a dict; return ``{}`` on failure.

        A parse failure or non-dict payload is treated as "not a merge_request event"
        so downstream filter logic can pass it through unchanged.
        """
        if isinstance(body, dict):
            return body
        if isinstance(body, (bytes, bytearray)):
            try:
                body = body.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                return {}
        if isinstance(body, str):
            if not body:
                return {}
            try:
                parsed = json.loads(body)
            except (json.JSONDecodeError, TypeError):
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @classmethod
    def is_mr_event(cls, body: bytes | str | dict[str, object]) -> bool:
        """True iff the parsed body is a merge_request event (object_kind check)."""
        return cls._parse_body(body).get("object_kind") == "merge_request"

    @classmethod
    def validate_mr_event_type(cls, body: bytes | str | dict[str, object], allowed_actions: list[str]) -> bool:
        """Whether the payload passes the MR-action filter.

        Non-MR events (``object_kind`` != ``merge_request`` — push, pipeline, tag,
        note, …) are outside the filter's scope and always pass through: a single
        webhook URL may legitimately receive multiple event types, and the user
        can restrict event *types* on the GitLab side. The filter constrains
        merge_request actions only.

        Returns True when:
          - ``allowed_actions`` is empty (no filter configured), OR
          - the payload is not a merge_request event (outside scope), OR
          - the payload is a merge_request event whose action is in the allowlist.

        Returns False only when the payload is a merge_request event whose action
        is missing or not in the allowlist.
        """
        if not allowed_actions:
            return True
        if not cls.is_mr_event(body):
            return True
        action = cls.extract_mr_action(body)
        return action is not None and action in allowed_actions

    @classmethod
    def apply_mr_action_filter(
        cls,
        raw_payload: bytes | str | dict[str, object],
        event_filter: str | None,
    ) -> tuple[bool, str | None]:
        """Apply the MR-action filter and return (dispatch, filtered_action).

        - ``(True, None)`` — dispatch the event (no filter, non-MR event, or allowed
          MR action).
        - ``(False, action)`` — merge_request event whose action is not in the
          allowlist; caller ACKs with 200 and skips the resource handler. ``action``
          is the extracted MR action (or ``None`` for MR events with a missing /
          unrecognized action).
        """
        if not event_filter:
            return True, None
        allowed_actions = [a.strip() for a in event_filter.split(",") if a.strip()]
        if not allowed_actions:
            return True, None
        if not cls.is_mr_event(raw_payload):
            return True, None
        action = cls.extract_mr_action(raw_payload)
        if action is not None and action in allowed_actions:
            return True, None
        return False, action

    @classmethod
    def verify_and_filter(
        cls,
        request: Request,
        expected_token: str,
        event_filter: str | None,
        raw_payload: bytes,
    ) -> tuple[bool, str | None]:
        """End-to-end GitLab verification: token check + MR-action filter.

        Combines the two GitLab-specific steps a caller previously had to wire by
        hand so that all validation lives inside the GitLab-specific class.

        - Raises ``HTTPException(401)`` on a token mismatch (the only auth failure).
        - Returns ``(True, None)`` when the request is authenticated and should be
          dispatched (no filter, non-MR event, or allowed MR action).
        - Returns ``(False, action)`` when the request authenticated but its MR
          action is filtered out; caller ACKs with 200 (see ``apply_mr_action_filter``).
        """
        if not cls.verify_token(request, expected_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=cls.INVALID_TOKEN_DETAIL)
        return cls.apply_mr_action_filter(raw_payload, event_filter)

    @classmethod
    def extract_gitlab_metadata(cls, request: Request) -> dict[str, str]:
        """Extract GitLab metadata from webhook request headers."""
        headers = request.headers
        return {
            "event_type": headers.get(cls.HEADER_EVENT, ""),
            "delivery_id": headers.get(cls.HEADER_DELIVERY, ""),
            "user_agent": headers.get("User-Agent", ""),
        }
