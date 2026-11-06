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

"""Registry of provisioning clients: the third-party systems confined to the /v1/provisioning API.

A client is matched by its CodeMie user id (the token subject). The registry is loaded once at
import and enforced whatever PROVISIONING_API_ENABLED says, so any defect in the file fails
startup. `find_client` is the only lookup.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from codemie.configs.config import config

PROVISIONING_CLIENTS_CONFIG_FILE = "provisioning-clients-config.yaml"

_TAG = "[ProvisioningClientsConfig]"
_CLIENTS_KEY = "provisioning_clients"
_UNNAMED = "<unnamed>"


class ProvisioningPermission(StrEnum):
    READ_PROJECTS = "provisioning:read-projects"
    MANAGE_PROJECTS = "provisioning:manage-projects"
    MANAGE_BUDGETS = "provisioning:manage-budgets"


# A new budget is named "<client name> <change reference>" in a 100-character field, and a change reference has up to
# 64 characters; a longer client name would cut the reference off.
CLIENT_NAME_MAX_LENGTH = 35


class ProvisioningClient(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    user_id: str
    permissions: frozenset[ProvisioningPermission] = frozenset()

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("name must not be empty")
        if len(name) > CLIENT_NAME_MAX_LENGTH:
            raise ValueError(f"name must be at most {CLIENT_NAME_MAX_LENGTH} characters")
        return name

    @field_validator("user_id")
    @classmethod
    def _normalize_user_id(cls, value: str) -> str:
        canonical_id = _canonical_user_id(value)
        if canonical_id is None:
            raise ValueError("user_id must be the client's CodeMie user id (a UUID)")
        return canonical_id


class ProvisioningClientsConfig(BaseModel):
    clients: list[ProvisioningClient] = Field(default_factory=list)

    @classmethod
    def load(cls, config_path: Path) -> ProvisioningClientsConfig:
        """Parse the registry file; raise ValueError naming the first defective entry."""
        clients: list[ProvisioningClient] = []
        for index, entry in enumerate(_read_entries(config_path)):
            client = _parse_entry(index, entry)
            _reject_conflicts(index, client, clients)
            clients.append(client)
        return cls(clients=clients)

    def find_client(self, user_id: str) -> ProvisioningClient | None:
        # Registry ids are stored canonical, so the caller's id must be canonicalized the same way.
        canonical_id = _canonical_user_id(user_id)
        return next((client for client in self.clients if client.user_id == canonical_id), None)

    @property
    def is_empty(self) -> bool:
        return not self.clients


def _canonical_user_id(user_id: str) -> str | None:
    """The canonical lowercase UUID form of `user_id`, or None when it is not a UUID."""
    try:
        return str(UUID(user_id))
    except ValueError:
        return None


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """SafeLoader that rejects a repeated mapping key; plain safe_load silently keeps the last one."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[object, object]:
        mapping = super().construct_mapping(node, deep=deep)
        seen: set[object] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark, f"found duplicate key {key!r}", key_node.start_mark
                )
            seen.add(key)
        return mapping


def _read_entries(config_path: Path) -> list[object]:
    try:
        data = yaml.load(config_path.read_text(encoding="utf-8"), Loader=_UniqueKeySafeLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"{_TAG} cannot read {config_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{_TAG} {config_path}: root must be a mapping")
    if data.keys() != {_CLIENTS_KEY}:
        raise ValueError(f"{_TAG} {config_path}: root must hold only {_CLIENTS_KEY}, found {sorted(map(str, data))}")
    entries = data[_CLIENTS_KEY]
    if not isinstance(entries, list):
        raise ValueError(f"{_TAG} {config_path}: {_CLIENTS_KEY} must be a list")
    return entries


def _parse_entry(index: int, entry: object) -> ProvisioningClient:
    try:
        return ProvisioningClient.model_validate(entry)
    except ValidationError as exc:
        reason = "; ".join(f"{'.'.join(map(str, error['loc'])) or 'entry'}: {error['msg']}" for error in exc.errors())
        raise _entry_error(index, _entry_label(entry), reason) from exc


def _entry_label(entry: object) -> str:
    name = entry.get("name") if isinstance(entry, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else _UNNAMED


def _reject_conflicts(index: int, client: ProvisioningClient, earlier: list[ProvisioningClient]) -> None:
    if any(other.name == client.name for other in earlier):
        raise _entry_error(index, client.name, "duplicate name")
    if any(other.user_id == client.user_id for other in earlier):
        raise _entry_error(index, client.name, "duplicate user_id")
    if client.user_id == _canonical_user_id(config.TEAMS_SERVICE_ACCOUNT_ID):
        # The Teams account's requests run as the message sender, which would take them out of the lock.
        raise _entry_error(index, client.name, "user_id must not be TEAMS_SERVICE_ACCOUNT_ID")


def _entry_error(index: int, label: str, reason: str) -> ValueError:
    return ValueError(f"{_TAG} {_CLIENTS_KEY}[{index}] ({label}): {reason}")


provisioning_clients_config = ProvisioningClientsConfig.load(
    config.PROVISIONING_CLIENTS_CONFIG_DIR / PROVISIONING_CLIENTS_CONFIG_FILE
)
