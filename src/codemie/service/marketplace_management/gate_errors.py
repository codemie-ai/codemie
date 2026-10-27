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

"""The one refusal a CRITICAL gate finding produces, shared by every router that enforces the gates."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException

_DEFAULT_HELP = "Fix each rule listed in details.gate_findings and publish again."


def critical_gate_blocked_error(
    assistant_id: str,
    findings: list[dict[str, Any]],
    critical: list[dict[str, Any]],
    *,
    help: Optional[str] = None,  # noqa: A002 - mirrors ExtendedHTTPException's field name
) -> ExtendedHTTPException:
    """The one 422 a CRITICAL gate finding produces, wherever it is detected.

    The publish path, the read-only `gate-check` preflight and the moderator's
    approve all have to answer "this assistant cannot go live, here is why"
    identically -- a client that has to parse two shapes for the same refusal,
    or a preflight that disagrees with the endpoint it previews, is worse than
    none. Built here once so the message and the `details` keys
    (`failed_rules`, `gate_findings`) cannot drift apart (MR !4070 review
    comments #12 and the approve-shape thread). Only the `help` text may differ
    by caller, since what to do next differs (fix and republish vs force-publish).
    """
    return ExtendedHTTPException(
        code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        message=(
            "Assistant cannot be published: " + "; ".join(f"{f.get('rule')}: {f.get('message')}" for f in critical)
        ),
        details={
            "assistant_id": assistant_id,
            "requires_confirmation": False,
            "failed_rules": [f.get("rule") for f in critical],
            "gate_findings": findings,
        },
        help=help or _DEFAULT_HELP,
    )
