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

"""The model-facing reference of the runtime SDK, shown in the script tool's description.

The text lives beside the SDK so that they change together. It is never injected into the sandbox: the launcher reads
only ``codemie_runtime_sdk.py`` and the bootstrap function.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache
from pathlib import Path

from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import (
    CALL_TIMEOUT_SECONDS,
    MAX_BATCH_CALLS,
)
from codemie_tools.data_management.code_executor.tool_calling_limits import ToolCallingSettings

REFERENCE_PATH: Path = Path(__file__).resolve().parent / "runtime_sdk" / "codemie_runtime_sdk_reference.md"


@cache
def _read_template() -> str:
    return REFERENCE_PATH.read_text(encoding="utf-8").strip()


def read_sdk_reference(settings: ToolCallingSettings | None = None) -> str:
    """The reference text with its numbers (``<<name>>`` placeholders) filled from the constants and ``settings``.

    The numbers come from the same values the bridge enforces, so the text cannot drift from the behaviour.
    """
    effective = settings or ToolCallingSettings()
    values = {
        "max_payload_kib": f"{effective.max_payload_bytes // 1024}",
        "default_wait_seconds": f"{CALL_TIMEOUT_SECONDS:g} seconds",
        "max_parallel_calls": f"{effective.max_parallel_calls}",
        "max_batch_calls": f"{MAX_BATCH_CALLS}",
    }
    text = _read_template()
    for name, value in values.items():
        text = text.replace(f"<<{name}>>", value)
    return text


def compose_description(base: str, extra_sections: Sequence[str]) -> str:
    """The tool description: the base text followed by each non-empty extra section, separated by blank lines."""
    parts = [base.strip(), *(section.strip() for section in extra_sections if section.strip())]
    return "\n\n".join(parts)
