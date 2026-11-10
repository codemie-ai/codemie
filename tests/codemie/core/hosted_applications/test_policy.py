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

"""The served validation policies are a code constant; a definition edit cannot ship without a new version."""

from codemie.core.hosted_applications import policy
from codemie.core.hosted_applications.policy import PERMITTED_POLICIES, POLICY_DIGEST, POLICY_VERSION, permits

# Each released policy version with the digest of its frozen definition.
FROZEN = {
    "static-v4": "a484afcd12a1d8f4def0892ca297c3edde9398eeb6ce125b976ebacd9444a11b",
}


def test_policy_definition_is_frozen_for_its_version():
    assert (
        FROZEN.get(POLICY_VERSION) == POLICY_DIGEST.hex()
    ), "POLICY_DEFINITION changed: give it a new POLICY_VERSION and record its digest here"


def test_only_listed_versions_with_their_exact_digest_are_served():
    assert permits(POLICY_VERSION, POLICY_DIGEST)
    assert not permits("unknown", POLICY_DIGEST)
    assert not permits(POLICY_VERSION, b"x" * 32)
    assert not permits(POLICY_VERSION, POLICY_DIGEST.hex())
    assert not permits(POLICY_VERSION, None)
    assert all(FROZEN.get(version) == digest.hex() for version, digest in PERMITTED_POLICIES.items())


def test_withdrawing_a_version_stops_serving_its_versions(monkeypatch):
    monkeypatch.delitem(policy.PERMITTED_POLICIES, POLICY_VERSION)
    assert not permits(POLICY_VERSION, POLICY_DIGEST)


def test_the_first_release_serves_only_its_own_policy():
    assert POLICY_VERSION == "static-v4"
    assert list(PERMITTED_POLICIES) == [POLICY_VERSION]
    assert not permits("static-v3", POLICY_DIGEST)


def test_large_archive_budget_covers_original_and_expanded_file_scanning():
    from codemie.hosted_apps_validator.contract import validation_budget

    # Two admitted near-limit archives share one scanner CPU. At the observed
    # throughput, original plus extracted-file scans need more than five minutes.
    assert 600 <= validation_budget("zip", 100 * 1024**2) <= 900
    assert validation_budget("html", 20 * 1024**2) < 180
