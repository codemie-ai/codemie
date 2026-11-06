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

"""Shared helpers for tests that need a registered provisioning client."""

from __future__ import annotations

from contextlib import AbstractContextManager
from unittest.mock import patch

from codemie.configs.provisioning_clients_config import (
    ProvisioningClient,
    ProvisioningPermission,
    provisioning_clients_config,
)

CLIENT_USER_ID = "5b0c4f7e-2d1a-4c3b-9e8f-7a6b5c4d3e2f"


def make_client(
    name: str = "support-portal",
    user_id: str = CLIENT_USER_ID,
    permissions: frozenset[ProvisioningPermission] = frozenset({ProvisioningPermission.READ_PROJECTS}),
) -> ProvisioningClient:
    return ProvisioningClient(name=name, user_id=user_id, permissions=permissions)


def registered_clients(*clients: ProvisioningClient) -> AbstractContextManager[list[ProvisioningClient]]:
    """Replace the loaded registry with `clients` for the duration of the context."""
    return patch.object(provisioning_clients_config, "clients", list(clients))
