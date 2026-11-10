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

"""Deployment manifests: the chart, the standalone objects and the runbook describe one topology and one set of names."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]
CHART = ROOT / "deploy-templates"
STANDALONE = ROOT / "hosted-applications"
VALIDATOR = "codemie-hosted-apps-validator"
CLAMD = "codemie-hosted-apps-clamd"
VALIDATOR_URL = "http://codemie-hosted-apps-validator.codemie-hosted-apps.svc:8091"


def hosted_values():
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    return values["features"]["tools"]["hosted-apps"]


def standalone_objects():
    return [item for item in yaml.safe_load_all((STANDALONE / "kubernetes.yaml").read_text()) if item]


def named(kind, name):
    (found,) = [item for item in standalone_objects() if item["kind"] == kind and item["metadata"]["name"] == name]
    return found


def container(deployment, name):
    (found,) = [item for item in deployment["spec"]["template"]["spec"]["containers"] if item["name"] == name]
    return found


def env(container_spec):
    return {item["name"]: item.get("value") for item in container_spec.get("env", [])}


def test_chart_network_policies_are_on_by_default():
    """CR-001: with chart defaults, no pod but the validator may reach clamd's unauthenticated port."""
    assert hosted_values()["networkPolicy"]["enabled"] is True


def test_chart_clamd_ingress_policy_does_not_depend_on_the_network_policy_switch():
    """CR-001: the clamd ingress lock is rendered whenever hosted apps are enabled."""
    template = (CHART / "templates" / "hosted-apps" / "clamd-networkpolicy.yaml").read_text()
    guard = next(line for line in template.splitlines() if line.startswith("{{- if"))
    assert "networkPolicy" not in guard and "$hostedApps.enabled" in guard
    assert 'include "codemie.hostedValidator.selectorLabels"' in template


def test_standalone_objects_use_the_chart_names_and_topology():
    """CR-001, CR-002: separate validator and clamd Deployments, named as the chart names them."""
    deployments = {item["metadata"]["name"] for item in standalone_objects() if item["kind"] == "Deployment"}
    services = {item["metadata"]["name"] for item in standalone_objects() if item["kind"] == "Service"}
    assert deployments == {VALIDATOR, CLAMD}
    assert services == {VALIDATOR, CLAMD}
    assert env(container(named("Deployment", VALIDATOR), "validator"))["CLAMD_HOST"] == CLAMD
    assert env(hosted_values()["validator"])["CLAMD_HOST"] == CLAMD


def test_standalone_clamd_admits_only_the_validator():
    """CR-001: clamd listens on the pod network, so a NetworkPolicy must admit the validator pods only."""
    policy = named("NetworkPolicy", CLAMD)
    validator_labels = named("Deployment", VALIDATOR)["spec"]["selector"]["matchLabels"]
    assert policy["spec"]["podSelector"]["matchLabels"] == named("Deployment", CLAMD)["spec"]["selector"]["matchLabels"]
    assert "Ingress" in policy["spec"]["policyTypes"]
    (rule,) = policy["spec"]["ingress"]
    assert rule["from"] == [{"podSelector": {"matchLabels": validator_labels}}]


def test_clamd_runs_in_utc_everywhere():
    """CR-007: the scanner reads clamd's signature date as UTC, so clamd must format it in UTC."""
    assert env(hosted_values()["clamd"])["TZ"] == "UTC"
    assert env(container(named("Deployment", CLAMD), "clamd"))["TZ"] == "UTC"


def test_runbook_and_values_name_the_namespace_qualified_validator_url():
    """CR-002: the API runs in another namespace than the validator, so a short Service name does not resolve."""
    readme = (STANDALONE / "README.md").read_text()
    values = (CHART / "values.yaml").read_text()
    for text in (readme, values):
        assert VALIDATOR_URL in text
    for stale in ("codemie-hosted-validator:8091", "http://codemie-hosted-apps-validator:8091", "codemie-hosted-clamd"):
        assert stale not in readme and stale not in values
