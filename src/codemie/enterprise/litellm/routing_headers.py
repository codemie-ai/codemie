# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""Header-extraction helpers for LiteLLM complexity-router (x-litellm-*) response headers.

Used by ``LiteLLMRouter.extract`` (``codemie/enterprise/litellm/router.py``) to locate the
headers mapping on an LLM response, wherever LangChain happened to stash it for the given call
path.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult


def _headers_of(container: object) -> Mapping[str, object] | None:
    if isinstance(container, Mapping):
        h = container.get("headers")
        if isinstance(h, Mapping):
            return h
    return None


def _iter_header_maps(response: LLMResult | AIMessage) -> Iterator[Mapping[str, object]]:
    if isinstance(response, AIMessage):
        h = _headers_of(getattr(response, "response_metadata", None))
        if h:
            yield h
        return
    for gen_list in getattr(response, "generations", []):
        for gen in gen_list:
            for source in (
                getattr(gen, "generation_info", None),
                getattr(getattr(gen, "message", None), "response_metadata", None),
            ):
                h = _headers_of(source)
                if h:
                    yield h
