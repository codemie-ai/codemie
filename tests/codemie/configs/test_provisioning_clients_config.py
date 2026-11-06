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

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from codemie.configs.config import config
from codemie.configs.provisioning_clients_config import (
    PROVISIONING_CLIENTS_CONFIG_FILE,
    ProvisioningClientsConfig,
    ProvisioningPermission,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

ALPHA_ID = "0d9e8f7a-6b5c-4d3e-8f2a-1b0c9d8e7f6a"
BETA_ID = "7c6b5a4f-3e2d-4c1b-a09f-8e7d6c5b4a39"
TEAMS_ID = "3a2b1c0d-9e8f-4a7b-b6c5-d4e3f2a1b0c9"

ALPHA = {"name": "alpha", "user_id": ALPHA_ID, "permissions": ["provisioning:read-projects"]}
BETA = {"name": "beta", "user_id": BETA_ID, "permissions": []}


def _write_registry(tmp_path: Path, text: str) -> Path:
    path = tmp_path / PROVISIONING_CLIENTS_CONFIG_FILE
    path.write_text(text, encoding="utf-8")
    return path


def _write_clients(tmp_path: Path, entries: list[dict[str, object]]) -> Path:
    return _write_registry(tmp_path, yaml.safe_dump({"provisioning_clients": entries}))


def test_loads_clients_and_finds_by_user_id(tmp_path):
    registry = ProvisioningClientsConfig.load(_write_clients(tmp_path, [{**ALPHA, "user_id": ALPHA_ID.upper()}]))

    client = registry.find_client(ALPHA_ID)

    assert client is not None
    assert client.name == "alpha"
    assert client.user_id == ALPHA_ID
    assert client.permissions == frozenset({ProvisioningPermission.READ_PROJECTS})
    assert registry.find_client(ALPHA_ID.upper()) == client
    assert registry.find_client(BETA_ID) is None
    assert not registry.is_empty


@pytest.mark.parametrize(
    "caller_id",
    [ALPHA_ID.replace("-", ""), ALPHA_ID.upper().replace("-", "")],
    ids=["no_hyphens", "uppercase_no_hyphens"],
)
def test_finds_client_by_non_canonical_form_of_its_id(tmp_path, caller_id):
    registry = ProvisioningClientsConfig.load(_write_clients(tmp_path, [ALPHA]))

    assert registry.find_client(caller_id).name == "alpha"


def test_non_uuid_caller_id_finds_no_client(tmp_path):
    registry = ProvisioningClientsConfig.load(_write_clients(tmp_path, [ALPHA]))

    assert registry.find_client("alpha") is None


def test_empty_permissions_list_is_valid(tmp_path):
    registry = ProvisioningClientsConfig.load(_write_clients(tmp_path, [BETA]))

    assert registry.find_client(BETA_ID).permissions == frozenset()


def test_explicit_empty_clients_list_is_valid(tmp_path):
    assert ProvisioningClientsConfig.load(_write_registry(tmp_path, "provisioning_clients: []\n")).is_empty


def test_two_clients_resolve_each_to_its_own_grants(tmp_path):
    budget_client = {**BETA, "permissions": ["provisioning:manage-budgets"]}
    registry = ProvisioningClientsConfig.load(_write_clients(tmp_path, [ALPHA, budget_client]))

    assert registry.find_client(ALPHA_ID).permissions == frozenset({ProvisioningPermission.READ_PROJECTS})
    assert registry.find_client(BETA_ID).name == "beta"
    assert registry.find_client(BETA_ID).permissions == frozenset({ProvisioningPermission.MANAGE_BUDGETS})


def test_client_name_of_35_characters_loads(tmp_path):
    registry = ProvisioningClientsConfig.load(_write_clients(tmp_path, [{**ALPHA, "name": "a" * 35}]))

    assert registry.find_client(ALPHA_ID).name == "a" * 35


def test_non_ascii_client_name_loads(tmp_path):
    registry = ProvisioningClientsConfig.load(_write_clients(tmp_path, [{**ALPHA, "name": "портал-підтримки"}]))

    assert registry.find_client(ALPHA_ID).name == "портал-підтримки"


@pytest.mark.parametrize(
    "config_dir",
    [REPO_ROOT / "config/provisioning_clients", type(config).model_fields["PROVISIONING_CLIENTS_CONFIG_DIR"].default],
    ids=["repository_file", "declared_default_dir"],
)
def test_shipped_registry_is_empty(config_dir):
    assert ProvisioningClientsConfig.load(config_dir / PROVISIONING_CLIENTS_CONFIG_FILE).is_empty


def test_default_dir_is_anchored_to_repo_root():
    assert config.PROVISIONING_CLIENTS_CONFIG_DIR.resolve() == REPO_ROOT / "config/provisioning_clients"


@pytest.mark.parametrize(
    "entries,label",
    [
        pytest.param([ALPHA, {"user_id": BETA_ID}], "<unnamed>", id="missing_name"),
        pytest.param([ALPHA, {"name": "  ", "user_id": BETA_ID}], "<unnamed>", id="empty_name"),
        pytest.param([ALPHA, {**BETA, "name": "b" * 36}], "b" * 36, id="name_over_35_characters"),
        pytest.param([ALPHA, {"name": "beta"}], "beta", id="missing_user_id"),
        pytest.param([ALPHA, {"name": "beta", "user_id": ""}], "beta", id="empty_user_id"),
        pytest.param([ALPHA, {"name": "beta", "user_id": "beta-client"}], "beta", id="user_id_not_uuid"),
        pytest.param([ALPHA, {**BETA, "permissions": ["provisioning:delete-all"]}], "beta", id="unknown_permission"),
        pytest.param([ALPHA, {**BETA, "account_id": BETA_ID}], "beta", id="unknown_key"),
        pytest.param([{**ALPHA, "name": "beta"}, BETA], "beta", id="duplicate_name"),
        pytest.param([ALPHA, {**BETA, "user_id": ALPHA_ID.upper()}], "beta", id="duplicate_user_id_other_case"),
        pytest.param([ALPHA, {**BETA, "user_id": TEAMS_ID}], "beta", id="teams_service_account_id"),
    ],
)
def test_invalid_entry_fails_naming_it(tmp_path, monkeypatch, entries, label):
    monkeypatch.setattr(config, "TEAMS_SERVICE_ACCOUNT_ID", TEAMS_ID)
    path = _write_clients(tmp_path, entries)

    with pytest.raises(ValueError, match=rf"^\[ProvisioningClientsConfig\] provisioning_clients\[1\] \({label}\): "):
        ProvisioningClientsConfig.load(path)


@pytest.mark.parametrize(
    "teams_setting",
    [TEAMS_ID, TEAMS_ID.replace("-", ""), TEAMS_ID.upper()],
    ids=["canonical", "compact", "uppercase"],
)
def test_teams_service_account_is_refused_in_any_form(tmp_path, monkeypatch, teams_setting):
    monkeypatch.setattr(config, "TEAMS_SERVICE_ACCOUNT_ID", teams_setting)
    path = _write_clients(tmp_path, [{**BETA, "user_id": TEAMS_ID}])

    with pytest.raises(ValueError, match="user_id must not be TEAMS_SERVICE_ACCOUNT_ID"):
        ProvisioningClientsConfig.load(path)


def test_ids_distinct_from_the_teams_service_account_are_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TEAMS_SERVICE_ACCOUNT_ID", TEAMS_ID.replace("-", ""))

    registry = ProvisioningClientsConfig.load(_write_clients(tmp_path, [ALPHA, BETA]))

    assert [client.name for client in registry.clients] == ["alpha", "beta"]


@pytest.mark.parametrize(
    "text,reason",
    [
        pytest.param("other: []\n", "root must hold only provisioning_clients", id="missing_clients_key"),
        pytest.param("provisioning_client: []\n", "root must hold only provisioning_clients", id="misspelled_key"),
        pytest.param(
            "provisioning_clients: []\nextra: 1\n", "root must hold only provisioning_clients", id="extra_root_key"
        ),
        pytest.param("provisioning_clients:\n", "provisioning_clients must be a list", id="null_clients"),
        pytest.param(
            "provisioning_clients: []\nprovisioning_clients: []\n", "found duplicate key", id="duplicate_root_key"
        ),
        pytest.param(
            f"provisioning_clients:\n  - name: alpha\n    user_id: {ALPHA_ID}\n    user_id: {BETA_ID}\n",
            "found duplicate key",
            id="duplicate_entry_key",
        ),
    ],
)
def test_malformed_registry_fails_naming_the_problem(tmp_path, text, reason):
    path = _write_registry(tmp_path, text)

    with pytest.raises(ValueError, match=rf"(?s)^\[ProvisioningClientsConfig\] .*{reason}"):
        ProvisioningClientsConfig.load(path)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("provisioning_clients: [unclosed", id="invalid_yaml"),
        pytest.param(None, id="missing_file"),
        pytest.param("- name: alpha\n", id="root_not_mapping"),
        pytest.param("provisioning_clients:\n  name: alpha\n", id="clients_not_list"),
    ],
)
def test_unreadable_registry_fails(tmp_path, text):
    path = tmp_path / PROVISIONING_CLIENTS_CONFIG_FILE if text is None else _write_registry(tmp_path, text)

    with pytest.raises(ValueError, match=r"^\[ProvisioningClientsConfig\] "):
        ProvisioningClientsConfig.load(path)
