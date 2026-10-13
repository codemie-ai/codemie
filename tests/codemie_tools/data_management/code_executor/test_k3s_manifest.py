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

"""The local k3s manifest must isolate the namespace the code executor really uses.

The deny-all policy is the only thing keeping a local sandbox script away from postgres and elasticsearch. It is bound to
a namespace by name, so a change of the default name in the executor config without a change here would leave the
sandbox pods without a policy and nothing would fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from codemie_tools.data_management.code_executor.models import CodeExecutorConfig

MANIFEST = Path(__file__).resolve().parents[4] / "docker" / "k3s" / "manifests" / "code-executor.yaml"


@pytest.fixture
def documents() -> list[dict]:
    return [doc for doc in yaml.safe_load_all(MANIFEST.read_text()) if doc]


@pytest.fixture
def default_namespace(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.delenv("CODE_EXECUTOR_NAMESPACE", raising=False)
    return CodeExecutorConfig.from_env().namespace


def _of_kind(documents: list[dict], kind: str) -> list[dict]:
    return [doc for doc in documents if doc.get("kind") == kind]


def test_the_manifest_creates_the_namespace_the_executor_uses_by_default(documents, default_namespace) -> None:
    (namespace,) = _of_kind(documents, "Namespace")

    assert namespace["metadata"]["name"] == default_namespace


def test_a_deny_all_policy_covers_every_pod_of_that_namespace_in_both_directions(documents, default_namespace) -> None:
    (policy,) = _of_kind(documents, "NetworkPolicy")

    assert policy["metadata"]["namespace"] == default_namespace
    assert policy["spec"]["podSelector"] == {}
    assert set(policy["spec"]["policyTypes"]) == {"Ingress", "Egress"}
    assert "ingress" not in policy["spec"]
    assert "egress" not in policy["spec"]


def test_the_healthcheck_of_the_stack_asks_for_the_policy_of_the_same_namespace(default_namespace) -> None:
    compose = (MANIFEST.parents[3] / "docker-compose.yml").read_text()

    assert f"kubectl -n {default_namespace} get networkpolicy deny-all" in compose
