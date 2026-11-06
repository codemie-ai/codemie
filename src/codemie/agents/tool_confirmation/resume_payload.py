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

"""The LangGraph payload of a pause (rule 4).

The payload holds what the resume must restore: the request as received and the carried ids.
A resume runs no structural check: it rebuilds the agent as an ordinary turn does (rule 2, AC20).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def build_resume_payload(root: Any, carried_ids: Sequence[str] = ()) -> dict[str, Any]:
    """The payload of a pause of ``root``'s tree: what a resume restores."""
    request = getattr(root, "received_request", None) or root.request
    policy_override = getattr(request, "tool_call_policy", None)
    return {
        "carried_ids": list(carried_ids),
        "root_version": getattr(request, "version", None),
        "sub_assistants_versions": getattr(request, "sub_assistants_versions", None),
        "skill_ids": getattr(request, "skill_ids", None),
        "llm_model": getattr(request, "llm_model", None),
        "request_tool_call_policy": getattr(policy_override, "value", policy_override),
        "file_names": list(getattr(request, "file_names", None) or []),
    }
