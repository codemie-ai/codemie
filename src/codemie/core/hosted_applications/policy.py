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

"""The validation policy this code attests to, and the policy versions under which app versions may be served."""

from __future__ import annotations

import hashlib
import hmac

from codemie.core.hosted_applications.protocol import canonical_json
from codemie.core.hosted_applications.validation import MIME_TYPES, ValidationLimits

# Any change to POLICY_DEFINITION needs a new POLICY_VERSION (a unit test pins the digest of each version).
POLICY_VERSION = "static-v4"
POLICY_DEFINITION = {
    "version": POLICY_VERSION,
    "profile": "self-contained-static",
    "formats": ["html", "zip"],
    "mime_types": MIME_TYPES,
    "limits": vars(ValidationLimits()),
    "execution": {"server": False, "build": False, "eval": False, "workers": False, "remote_resources": False},
    "secret_rules": "high-confidence-v1",
    "manifest_version": 1,
    "malware_scan": "mandatory-original-and-files",
    "artifact_rules": "bounded-static-binary-signatures-v3",
    # No finding holds a version for manual review. The key stays because it is part of the frozen digest.
    "review_findings": [],
    "parser_sandbox": "rlimit-no-network-no-child-process-v1",
    "scanner": {
        "engine_minors": ["1.4", "1.5"],
        "maximum_signature_age_seconds": 172800,
        "configuration": {
            "StreamMaxLength": "100M",
            "MaxFileSize": "100M",
            "MaxScanSize": "600M",
            "MaxRecursion": "20",
            "MaxFiles": "5102",
            "AlertExceedsMax": "yes",
            "ScanArchive": "yes",
            "ScanHTML": "yes",
            "ScanPE": "yes",
            "MaxScanTime": "300000",
            "MaxThreads": "2",
            "MaxQueue": "8",
            "ConcurrentDatabaseReload": "no",
        },
    },
}
POLICY_DIGEST = hashlib.sha256(canonical_json(POLICY_DEFINITION)).digest()


# Accepted app versions are served only while their policy version is listed here with the exact definition
# digest it was validated under. Ship a new version together with the code that validates it; keep an older
# entry only while its versions may still be served, and remove it to withdraw them at the next deploy.
PERMITTED_POLICIES = {
    POLICY_VERSION: POLICY_DIGEST,
}


def permits(version, digest) -> bool:
    """True when app versions validated under this policy version and definition digest may be served."""
    expected = PERMITTED_POLICIES.get(version)
    return expected is not None and isinstance(digest, bytes) and hmac.compare_digest(expected, digest)
