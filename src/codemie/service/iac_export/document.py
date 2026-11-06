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

"""Canonical export document envelope and byte-stable serialiser."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

SortKeys = dict[str, Callable[[Any], Any]]


class ExportError(Exception):
    """Raised when an entity cannot be exported."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# Envelope version tag (`Document.version` of the IaC document envelope); the exporters emit the current one.
ENVELOPE_VERSION = "v1"


class ExportMeta(BaseModel):
    """Shared `meta` block: the entity kind and its human-readable name (never an id)."""

    kind: str
    name: str


class ExportDocument(BaseModel):
    """Export envelope: `version`, `meta` and `spec`, i.e. a document the IaC envelope parses as is.

    `sort_keys` maps a list field name (at any depth) to a natural-key function
    used to sort that list. `verbatim` names top-level `spec` fields holding free-form
    payloads, which are serialised exactly as stored (no null stripping, no sorting).
    Neither is serialised.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    version: str = ENVELOPE_VERSION
    meta: BaseModel
    spec: BaseModel
    sort_keys: SortKeys = Field(default_factory=dict, exclude=True)
    verbatim: frozenset[str] = Field(default_factory=frozenset, exclude=True)


class Exporter(Protocol):
    def export(self, id: str, user: Any) -> ExportDocument: ...


def _total_key(key: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """A total, input-order independent ordering: items without a usable key sort last, ties break on content."""

    def wrapped(item: Any) -> tuple:
        try:
            natural = key(item)
        except (KeyError, TypeError, AttributeError):
            natural = None
        tiebreak = json.dumps(item, sort_keys=True, default=str, ensure_ascii=False)
        if natural is None:
            return (1, "", 0, "", tiebreak)
        if isinstance(natural, (int, float)) and not isinstance(natural, bool):
            return (0, "n", natural, "", tiebreak)
        if isinstance(natural, str):
            return (0, "s", 0, natural, tiebreak)
        return (0, "j", 0, json.dumps(natural, sort_keys=True, default=str, ensure_ascii=False), tiebreak)

    return wrapped


def _sorted(items: list[Any], key: Callable[[Any], Any]) -> list[Any]:
    return sorted(items, key=_total_key(key))


def _normalise(value: Any, sort_keys: SortKeys, field: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: _normalise(v, sort_keys, k) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        items = [_normalise(v, sort_keys, field) for v in value if v is not None]
        key = sort_keys.get(field) if field else None
        return _sorted(items, key) if key else items
    return value


def _normalise_spec(spec: dict[str, Any], doc: ExportDocument) -> dict[str, Any]:
    return {k: v if k in doc.verbatim else _normalise(v, doc.sort_keys, k) for k, v in spec.items() if v is not None}


def _find_literal_credential(value: Any, path: str) -> str | None:
    """Path (keys only, never the value) of the first string that looks like a literal credential."""
    from codemie.service.iac_export.redaction import has_literal_credential  # redaction imports this module

    if isinstance(value, str):
        return path if has_literal_credential(value) else None
    if isinstance(value, dict):
        children = ((f"{path}.{k}", v) for k, v in value.items())
    elif isinstance(value, list):
        children = ((f"{path}[{i}]", v) for i, v in enumerate(value))
    else:
        return None
    return next((hit for hit in (_find_literal_credential(v, p) for p, v in children) if hit), None)


def to_canonical_bytes(doc: ExportDocument) -> bytes:
    """Serialise to compact UTF-8 JSON: declared key order, no nulls, sorted lists.

    Last line of defence: a string anywhere in the document that still looks like a literal credential (token
    shape, URL userinfo) fails the export instead of leaving the service.
    """
    spec = doc.spec.model_dump(mode="json", by_alias=True)
    data = {
        "version": doc.version,
        "meta": _normalise(doc.meta.model_dump(mode="json", by_alias=True), doc.sort_keys),
        "spec": _normalise_spec(spec, doc),
    }
    leak = _find_literal_credential(data, "$")
    if leak:
        raise ExportError(f"Export document holds what looks like a credential at {leak}; refusing to emit it")
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
