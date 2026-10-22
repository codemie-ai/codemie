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

"""The single place this backend names the target A2A protocol version.

CodeMie's A2A layer (`src/codemie/rest_api/a2a/`) is fully in-house; its
wire shapes are A2A spec v1.0 as defined by `a2a.proto` / ProtoJSON (confirmed against
https://a2a-protocol.org/latest/specification/ and the a2aproject/A2A GitHub
releases — v1.0.0 released 2026-03-12, v1.0.1 released 2026-05-28 as a
bug-fix-only patch with no further wire-shape changes), identified by these
wire-shape markers:

  - Discovery moves to `.well-known/agent-card.json` (renamed from
    `agent.json` back in v0.3.0);
  - No `kind`/`type` discriminator on the wire — v1.0.0's own breaking-change
    notes call this out explicitly ("Kind Discriminator Removed"); each
    binding uses its own typing instead;
  - JSON-RPC method names `SendMessage`, `SendStreamingMessage`, `GetTask`,
    `ListTasks`, `CancelTask`, `SubscribeToTask`, the push-notification-config
    CRUD methods, and `GetExtendedAgentCard` (not `message/send` /
    `message/stream` — that was an intermediate, pre-1.0 naming);
  - `AgentCard.url` is gone, replaced by `supportedInterfaces[]` (one entry
    per transport binding: JSON-RPC / gRPC / REST) plus `signatures` (Agent
    Card signing) and `capabilities.extendedAgentCard`;
  - Enum values are proto names (`TASK_STATE_*`, `ROLE_*`), `Task.id` (not
    `taskId`), `messageId` / `artifactId`, `Part` as one of `text` / `raw` /
    `url` / `data`, and `StreamResponse` / `SendMessageResponse` objects
    (`{"task"}`, `{"message"}`, `{"statusUpdate"}`, `{"artifactUpdate"}`);
  - Version negotiation via the `A2A-Version` request header (or query
    parameter): the server rejects anything but `Major.Minor == 1.0` with
    JSON-RPC `-32009`, the client always sends it. Per spec §3.6 patch
    numbers are not part of negotiation, so the negotiated value is `"1.0"`
    even though the spec document implemented against is v1.0.1.

See `.ai-run/guides/integration/a2a-integration.md` for the full decision and
rationale (in-house upgrade, not `a2a-sdk` migration).

This module — not a literal re-typed elsewhere — is the one place the target
A2A protocol version is named.
"""

#: Target A2A protocol version for negotiation (`A2A-Version` header), per
#: spec §3.6 which excludes patch numbers from negotiation. Implemented
#: against spec document v1.0.1 (latest patch of the v1.0.0 breaking-change
#: release); see this module's docstring for the wire-shape markers.
A2A_PROTOCOL_VERSION: str = "1.0"

#: Version assumed for a pre-1.0 card that declares none.
LEGACY_PROTOCOL_VERSION: str = "0.3.0"


def is_supported_protocol_version(version: str | None) -> bool:
    """Versions match on Major.Minor; the patch component is ignored."""
    parts = (version or "").split(".")
    return len(parts) >= 2 and ".".join(parts[:2]) == A2A_PROTOCOL_VERSION
