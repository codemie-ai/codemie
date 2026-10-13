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

"""Tokens of the text a script run exchanges, counted the way the platform counts the output of a direct tool call."""

from __future__ import annotations

from typing import Final

from codemie.service.llm_service.llm_service import LLMService
from codemie_tools.base.utils import get_encoding

#: Used when the run has no model of its own (an unknown model also falls back inside ``get_encoding``).
DEFAULT_MODEL: Final[str] = LLMService.BASE_NAME_GPT_41_MINI


def count_tokens(text: str, llm_model: str | None = None) -> int:
    """Tokens of ``text`` under the encoding of ``llm_model`` (``o200k_base`` when the model is unknown)."""
    if not text:
        return 0
    return len(get_encoding(llm_model or DEFAULT_MODEL).encode(text, disallowed_special=()))
