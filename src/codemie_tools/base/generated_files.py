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

"""Read-only access to files an agent generated in the current conversation, and attachment name matching.

Confluence-independent: the workspace-backed implementation lives in the service layer and is injected.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from langchain_core.tools import ToolException
from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema


@dataclass(frozen=True)
class GeneratedFileInfo:
    """One workspace file. `name` is the basename, `path` the workspace-relative path.

    `blob` is the storage identity (owner, name, mime type) used to recognise an uploaded file that is also
    registered in the workspace; `checksum` is the content hash used to recognise identical copies of one file.
    """

    name: str
    size: int
    updated: datetime | None = None
    path: str = ""
    blob: tuple[str, str, str] | None = None
    checksum: str | None = None


class GeneratedFileSource(Protocol):
    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: GetCoreSchemaHandler) -> core_schema.CoreSchema:
        # Lets pydantic tools declare this type on their fields; the source is a service, not data.
        return core_schema.any_schema()

    def list_generated(self) -> list[GeneratedFileInfo]: ...

    def read_generated(self, info: GeneratedFileInfo) -> bytes: ...


@dataclass(frozen=True)
class AttachmentCandidate:
    source: Literal["generated", "uploaded"]
    name: str
    load: Callable[[], bytes]
    path: str | None = None  # workspace-relative path; defaults to the bare name
    checksum: str | None = None  # content hash of a generated file; equal name, checksum and size = same file
    size: int | None = None

    def __post_init__(self) -> None:
        if not self.path:
            object.__setattr__(self, "path", self.name)

    def describe(self) -> str:
        if self.source == "uploaded":
            return f"'{self.path}' (uploaded)"
        return f"'{self.path}' (generated in the workspace)"

    def _identity(self) -> tuple[Any, ...]:
        # Copies of one export (for example the code executor's root file and its artifacts/ copy) are one file.
        if self.checksum:
            return (self.name, self.checksum, self.size)
        return (self.path,)


def _distinct_generated(generated: list[AttachmentCandidate], requested: str) -> list[AttachmentCandidate]:
    """One candidate per distinct file: the exact-path copy, else the shortest path (then alphabetical)."""
    groups: dict[tuple[Any, ...], AttachmentCandidate] = {}
    for cand in sorted(generated, key=lambda c: (c.path != requested, len(str(c.path)), str(c.path))):
        groups.setdefault(cand._identity(), cand)
    return list(groups.values())


def match_attachment(name: str, candidates: list[AttachmentCandidate]) -> AttachmentCandidate:
    """Pick the candidate by exact workspace path first, then by bare file name; never guess."""
    hits = [c for c in candidates if c.path == name] or [c for c in candidates if c.name == name]
    if not hits:
        available = ", ".join(sorted({str(c.path) for c in candidates})) or "none"
        raise ToolException(f"File '{name}' was not found. Available files: {available}.")

    uploads = [c for c in hits if c.source == "uploaded"]
    generated = _distinct_generated([c for c in hits if c.source == "generated"], name)
    ordered = uploads + generated
    if len(ordered) > 1:
        listing = "; ".join(c.describe() for c in ordered)
        shown = [str(c.path) for c in ordered]
        if len(set(shown)) < len(shown):
            advice = (
                "At least two of them have the same path, so retrying with a path cannot disambiguate; "
                "rename one of the files."
            )
        else:
            advice = "Retry with the full path of one of them, or rename one of the files."
        raise ToolException(f"File name '{name}' is ambiguous: {listing}. {advice}")
    return ordered[0]
