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

"""What the API and the validator agree on besides the policy: time budgets and outcome codes."""

from __future__ import annotations

from codemie.core.hosted_applications.validation import ValidationLimits

_MIB = 1024 * 1024
BASE_SECONDS = 120
SECONDS_PER_FILE = 0.03
SECONDS_PER_10_MIB = 5.0
MAXIMUM_SECONDS = 900
# The validator keeps a finished result this long beyond the job budget, then reclaims it.
RESULT_GRACE_SECONDS = 30
# Parser findings above this count fail validation deterministically (the candidate schema bound).
MAX_WARNINGS = 10000

# The artifact exceeds what validation can process; retrying cannot help, and nothing unsafe was found.
LIMIT_REJECTION_CODES = frozenset({"SCAN_LIMIT_EXCEEDED", "VALIDATION_LIMIT_EXCEEDED"})
# The validator never accepted the job (no free slot, or not reachable): requeue without consuming a retry.
NOT_ACCEPTED_CODES = frozenset({"VALIDATOR_BUSY", "VALIDATOR_UNREACHABLE"})


def validation_budget(kind: str, size: int) -> float:
    """Seconds one validation job may take end to end, sized for the worst case of this upload.

    The bound is 120 s plus 30 ms per file and 5 s per 10 MiB of source plus twice the expanded bytes,
    capped at 15 minutes. ClamAV scans both the archive's expanded contents and every extracted file.
    The throughput allowance covers two admitted jobs sharing the deployment's scanner CPU. A ZIP
    is budgeted for the most files and expansion its size allows, before parsing reveals real counts.
    """
    limits = ValidationLimits()
    if kind == "html":
        files, expanded = 1, size
    else:
        files, expanded = limits.max_files, min(limits.max_expanded_bytes, size * limits.max_ratio)
    seconds = BASE_SECONDS + SECONDS_PER_FILE * files + SECONDS_PER_10_MIB * (size + 2 * expanded) / (10 * _MIB)
    return min(float(MAXIMUM_SECONDS), seconds)
