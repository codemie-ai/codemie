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

"""How one CLI analytics ingest request is gated (EPMCDME-16440): classification policy settings and the
route that picks the legacy project gate or the evidence gate. Used by the ingest routes of cli_analytics.py."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException, Request, status

from codemie.configs.customer_config import customer_config
from codemie.core.constants import (
    HEADER_CODEMIE_CLI_PROJECT,
    HEADER_CODEMIE_ORGANIZATION,
    HEADER_CODEMIE_ORGANIZATION_ENCODED,
    HEADER_CODEMIE_POLICY_VERSION,
    HEADER_CODEMIE_PROJECT_SOURCE,
    HEADER_CODEMIE_REPO_REMOTE,
    HEADER_CODEMIE_ROUTED_VIA_CODEMIE,
    HEADER_CODEMIE_SESSION_ID,
    HEADER_CODEMIE_SUBSCRIPTION_TYPE,
)
from codemie.repository.cli_analytics.ports import IngestGate
from codemie.rest_api.security.user import User
from codemie.service.analytics.cli_analytics_project_gate import build_evidence_gate
from codemie.service.analytics.cli_classification import classify, decode_organization, policy_version

FEATURE = "cliAnalytics"
REASON_SESSION_ID_REQUIRED = "session_id_required"
REASON_ORGANIZATION_CONFLICT = "organization_conflict"
REASON_ORGANIZATION_INVALID = "organization_invalid"
REASON_ORGANIZATION_REQUIRED = "organization_required"


def corporate_organizations() -> list[str]:
    """Configured corporate organization names (one per line, blank lines dropped)."""
    raw = customer_config.get_feature_setting(FEATURE, "corporateOrganizations", default="")
    return [line.strip() for line in str(raw or "").splitlines() if line.strip()]


EVIDENCE_HEADERS = (
    HEADER_CODEMIE_SESSION_ID,
    HEADER_CODEMIE_ROUTED_VIA_CODEMIE,
    HEADER_CODEMIE_ORGANIZATION,
    HEADER_CODEMIE_ORGANIZATION_ENCODED,
    HEADER_CODEMIE_PROJECT_SOURCE,
    HEADER_CODEMIE_SUBSCRIPTION_TYPE,
    HEADER_CODEMIE_POLICY_VERSION,
    HEADER_CODEMIE_REPO_REMOTE,
)


def _organization(headers, routed: bool, client_type: str) -> str | None:
    """The organization text of an evidence request, from the plain header or the encoded one (never both).

    A malformed envelope is a 400 before any classification, binding or write; the CLI keeps its range on a 400.
    The plain header is used as received (no percent-decoding). CodeMie routing needs no organization, so a missing
    one never blocks it. For a non-routed request whose client claims Personal or Corporate, a missing organization
    means the header was lost on the way (a proxy, or an old CLI that could not represent the name): answered
    `organization_required` instead of being classified Unknown. The client class is used for this check only.
    """
    plain = headers.get(HEADER_CODEMIE_ORGANIZATION)
    encoded = headers.get(HEADER_CODEMIE_ORGANIZATION_ENCODED)
    if plain is not None and encoded is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=REASON_ORGANIZATION_CONFLICT)
    if encoded is not None:
        try:
            return decode_organization(encoded)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=REASON_ORGANIZATION_INVALID) from exc
    if not routed and not (plain or "").strip() and client_type in {"personal", "corporate"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=REASON_ORGANIZATION_REQUIRED)
    return plain


class IngestRoute:
    """How one ingest request is gated: a legacy project gate, or the evidence gate with its session id.

    `stale_policy_version` is the current policy version when the evidence request's policy is out of date; the
    ingestor answers it (409) after the session boundary check, as spec 4 orders (nothing may be stored).
    """

    def __init__(self, request: Request, build_legacy_gate: Callable[[User, str | None], IngestGate]) -> None:
        self.user: User = request.state.user
        self.evidence_session_id: str | None = None
        self.stale_policy_version: str | None = None
        headers = request.headers
        if all(headers.get(name) is None for name in EVIDENCE_HEADERS):
            self.gate: IngestGate = build_legacy_gate(self.user, headers.get(HEADER_CODEMIE_CLI_PROJECT))
            return
        session_id = (headers.get(HEADER_CODEMIE_SESSION_ID) or "").strip()
        if not session_id:
            # A malformed envelope, not a session mismatch: answered 400 before any decode or write. The CLI does not
            # acknowledge a 400 (its range stays in the spool), whereas the 200 discard it used to get deleted it.
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=REASON_SESSION_ID_REQUIRED)
        organizations = corporate_organizations()
        routed = (headers.get(HEADER_CODEMIE_ROUTED_VIA_CODEMIE) or "").strip().lower() in {"1", "true", "yes"}
        client_type = (headers.get(HEADER_CODEMIE_SUBSCRIPTION_TYPE) or "").strip().lower()
        organization = _organization(headers, routed, client_type)
        derived = classify(routed, organization, organizations)
        client_version = (headers.get(HEADER_CODEMIE_POLICY_VERSION) or "").strip()
        current = policy_version(organizations)
        # Only a client that states both its classification and its policy version can be out of date.
        if client_type and client_version and client_version != current and client_type != derived:
            self.stale_policy_version = current
        self.evidence_session_id = session_id
        self.gate = build_evidence_gate(
            self.user,
            header_project=headers.get(HEADER_CODEMIE_CLI_PROJECT),
            project_source=(headers.get(HEADER_CODEMIE_PROJECT_SOURCE) or "").strip().lower() or None,
            repo_remote=headers.get(HEADER_CODEMIE_REPO_REMOTE),
            subscription_type=derived,
            client_subscription_type=client_type,
            client_policy_version=client_version,
        )

    @property
    def sender(self) -> str:
        return self.user.email or ""
