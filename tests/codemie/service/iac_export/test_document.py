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

import json

from pydantic import BaseModel

from codemie.service.iac_export.document import ExportDocument, ExportError, to_canonical_bytes


class _Meta(BaseModel):
    kind: str
    version: str


class _Spec(BaseModel):
    name: str
    description: str | None = None
    tags: list[str] = []
    items: list[dict] = []


def _doc(tags, items):
    return ExportDocument(
        meta=_Meta(kind="assistant", version="v1"),
        spec=_Spec(name="a", tags=tags, items=items),
        sort_keys={"tags": lambda t: t, "items": lambda i: i["name"]},
    )


def test_shuffled_lists_serialise_byte_identical():
    a = _doc(["b", "a", "c"], [{"name": "y"}, {"name": "x"}])
    b = _doc(["c", "a", "b"], [{"name": "x"}, {"name": "y"}])
    assert to_canonical_bytes(a) == to_canonical_bytes(b)


def test_nulls_are_omitted_recursively():
    doc = _doc([], [{"name": "x", "extra": None, "nested": {"k": None, "v": 1}}])
    data = json.loads(to_canonical_bytes(doc))
    assert "description" not in data["spec"]
    assert data["spec"]["items"] == [{"name": "x", "nested": {"v": 1}}]


def test_key_order_follows_declaration_and_is_compact():
    raw = to_canonical_bytes(_doc(["a"], [])).decode()
    assert raw.startswith('{"version":"v1","meta":{"kind":"assistant","version":"v1"},"spec":{"name":"a"')
    assert list(json.loads(raw)["spec"]) == ["name", "tags", "items"]


def test_non_ascii_kept_unescaped():
    doc = ExportDocument(meta=_Meta(kind="k", version="é"), spec=_Spec(name="ж"))
    assert "ж".encode() in to_canonical_bytes(doc)


def test_export_error_defaults_to_422():
    err = ExportError("bad")
    assert err.message == "bad"
    assert err.status_code == 422
    assert ExportError("x", status_code=404).status_code == 404
