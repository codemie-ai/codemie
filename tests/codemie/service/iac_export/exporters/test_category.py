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
from pathlib import Path
from unittest.mock import patch

import pytest

from codemie.rest_api.models.category import Category
from codemie.service.iac_export.document import ExportError, to_canonical_bytes
from codemie.service.iac_export.exporters.category import CategoryExporter
from tests.codemie.service.iac_export.envelope.conformance import assert_conforms

GOLDEN = Path(__file__).parent / "category_golden.json"


def _category() -> Category:
    return Category(id="dev_tools", name="Dev Tools", description="Tools for developers")


def test_export_matches_golden_without_ids_or_timestamps():
    with patch.object(Category, "find_by_id", return_value=_category()):
        doc = CategoryExporter().export("dev_tools", user=None)
    body = to_canonical_bytes(doc)
    assert json.loads(body) == json.loads(GOLDEN.read_text())
    assert_conforms(json.loads(body))
    assert body == to_canonical_bytes(doc)
    assert b"dev_tools" not in body and b"date" not in body


def test_unknown_category_raises_404():
    with patch.object(Category, "find_by_id", return_value=None):
        with pytest.raises(ExportError) as exc:
            CategoryExporter().export("missing", user=None)
    assert exc.value.status_code == 404


def test_a_non_admin_user_can_export_a_category():
    # The category list endpoint is open to every authenticated user, so the export needs no admin right.
    from codemie.rest_api.security.user import User

    member = User(id="member-1", username="member-1")
    member.is_admin = False
    with patch.object(Category, "find_by_id", return_value=_category()):
        doc = CategoryExporter().export("dev_tools", user=member)
    assert doc.meta.name == "Dev Tools"
