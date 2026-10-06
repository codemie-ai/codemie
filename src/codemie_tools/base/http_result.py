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


from __future__ import annotations

import json
from typing import Literal, Self

from codemie_tools.base.script_result import JsonValue, ScriptResult, ScriptResultTooLarge

type HttpLayout = Literal["spaced", "compact", "body_on_new_line"]


class HttpResult(str):
    """The legacy `HTTP: ...` result string of an HTTP tool that also keeps status and body.

    `str(result)` is byte-identical to the historic f-string of the tool, so LLM-facing output is
    unchanged; scripts read the structured result via `to_script_result()`.
    """

    method: str
    url: str
    status: int
    reason: str | None
    body: str
    layout: HttpLayout

    def __new__(
        cls,
        method: str,
        url: str,
        status: int,
        reason: str | None,
        body: str,
        layout: HttpLayout,
        rendered: str | None = None,
    ) -> Self:
        text = rendered if rendered is not None else cls._render(method, url, status, reason, body, layout)
        instance = super().__new__(cls, text)
        instance.method = method
        instance.url = url
        instance.status = status
        instance.reason = reason
        instance.body = body
        instance.layout = layout
        return instance

    @staticmethod
    def _render(method: str, url: str, status: int, reason: str | None, body: str, layout: HttpLayout) -> str:
        match layout:
            case "spaced":
                return f"HTTP: {method} {url} -> {status} {reason} {body}"
            case "compact":
                return f"HTTP: {method}{url} -> {status}{reason}{body}"
            case "body_on_new_line":
                return f"HTTP: {method} {url} -> {status} {reason}\n{body}"
        raise ValueError(f"Unknown HTTP result layout: {layout!r}")

    def __getnewargs__(self) -> tuple[str, str, int, str | None, str, HttpLayout, str]:
        return self.method, self.url, self.status, self.reason, self.body, self.layout, str(self)

    def with_suffix(self, text: str) -> HttpResult:
        """Return a copy whose string is extended by `text`; the structured fields are unchanged."""
        return HttpResult(
            self.method, self.url, self.status, self.reason, self.body, self.layout, rendered=str(self) + text
        )

    def to_script_result(self, max_bytes: int | None = None) -> ScriptResult:
        """The parsed body (JSON for an object/array body, otherwise the body string) with the HTTP status.

        A body longer than ``max_bytes`` characters is refused before it is parsed: it cannot fit in that many bytes.
        """
        if max_bytes is not None and len(self.body) > max_bytes:
            raise ScriptResultTooLarge(len(self.body))
        return ScriptResult(result=self._parsed_body(), http_status=self.status, http_reason=self.reason)

    def _parsed_body(self) -> JsonValue:
        stripped = self.body.strip()
        if stripped.startswith(("{", "[")):
            try:
                parsed: JsonValue = json.loads(stripped)
            except ValueError:
                return self.body
            return parsed
        return self.body
