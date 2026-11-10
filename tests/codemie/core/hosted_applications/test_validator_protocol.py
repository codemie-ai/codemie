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

from datetime import datetime, timezone
import hashlib

import pytest

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.validation import inspect_bundle
from codemie.core.hosted_applications.policy import POLICY_DIGEST, POLICY_VERSION
from codemie.service.hosted_applications.validator_client import candidate_response


def result(tmp_path, content="<html>Safe</html>"):
    source = tmp_path / "source.html"
    source.write_text(content)
    candidate = inspect_bundle(source, "html", tmp_path / "assets").to_dict()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    candidate.update(
        validation_id="a" * 64,
        source_digest=digest,
        policy_version=POLICY_VERSION,
        policy_definition_digest=POLICY_DIGEST.hex(),
        scanner={
            "version": "1.5.4",
            "signature_version": "28143",
            "signature_updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return candidate, digest


def test_real_parser_candidate_is_accepted_at_protocol_boundary(tmp_path):
    value, digest = result(tmp_path)
    identifier, candidate, scanner = candidate_response(value, digest)
    assert identifier == "a" * 64 and candidate.entries == ["index.html"]
    assert candidate.files[0].sha256 == value["files"][0]["sha256"]


def test_missing_resource_advisory_crosses_validator_boundary_safely(tmp_path):
    value, digest = result(tmp_path, '<html><link rel="stylesheet" href="missing.css"></html>')
    assert value["warnings"][0]["code"] == "MISSING_LOCAL_RESOURCE"
    value["warnings"][0]["message"] = "untrusted scanner detail"
    _, candidate, _ = candidate_response(value, digest)
    assert candidate.warnings[0].code == "MISSING_LOCAL_RESOURCE"
    assert candidate.warnings[0].path == "index.html"
    assert candidate.warnings[0].message == "A referenced local resource is missing; include it in the bundle"


@pytest.mark.parametrize(
    "field,invalid",
    [
        ("path", "../secret"),
        ("key", "source"),
        ("mime", "application/octet-stream"),
        ("sha256", "x" * 64),
        ("size", True),
        ("document", False),
    ],
)
def test_candidate_metadata_tampering_fails_closed(tmp_path, field, invalid):
    value, digest = result(tmp_path)
    value["files"][0][field] = invalid
    with pytest.raises(HostedApplicationError) as error:
        candidate_response(value, digest)
    assert error.value.code == "VALIDATION_UNAVAILABLE"


@pytest.mark.parametrize("field", ["policy_version", "policy_definition_digest"])
def test_old_validator_policy_is_never_attested_as_current(tmp_path, field):
    value, digest = result(tmp_path)
    value[field] = "obsolete"
    with pytest.raises(HostedApplicationError):
        candidate_response(value, digest)


def test_runtime_neutralized_capability_crosses_the_boundary_as_information(tmp_path):
    value, digest = result(tmp_path, "<html><script>var g = new Function('return this')()</script></html>")
    assert [item["code"] for item in value["warnings"]] == ["RESTRICTED_CAPABILITY"]
    _, candidate, _ = candidate_response(value, digest)
    assert candidate.warnings[0].message == "Dynamic evaluation and workers are blocked by the runtime policy"
