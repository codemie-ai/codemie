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
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.assistant import MCPServerDetails, ToolDetails, ToolKitDetails
from codemie.rest_api.models.skill import (
    SkillCategory,
    SkillCompanionFileMetadata,
    SkillDetailResponse,
    SkillVisibility,
)
from codemie.service.iac_export.document import to_canonical_bytes
from codemie.service.iac_export.exporters.skill import SkillExporter
from tests.codemie.service.iac_export.envelope.conformance import assert_conforms

GOLDEN = Path(__file__).parent / "skill_golden.json"
SERVICE = "codemie.service.iac_export.exporters.skill.SkillService.get_skill_by_id"


def _detail() -> SkillDetailResponse:
    return SkillDetailResponse(
        id="skill-uuid-1",
        name="code-reviewer",
        description="Reviews code for correctness",
        content="# Review\n" + "Check the diff carefully, line by line, and report every problem you find. " * 2,
        project="demo",
        visibility=SkillVisibility.PROJECT,
        categories=[SkillCategory.CODE_REVIEW],
        created_date=datetime(2026, 1, 1),
        updated_date=datetime(2026, 2, 2),
        assistants_count=3,
        user_abilities=["read", "write"],
        toolkits=[ToolKitDetails(toolkit="git", tools=[ToolDetails(name="diff")])],
        mcp_servers=[MCPServerDetails(name="docs", description="Docs server", mcp_config_id="cfg-id-1")],
        companion_files=[SkillCompanionFileMetadata(path="a/b.md", size_bytes=3, mime_type="text/markdown")],
    )


def test_export_matches_golden_content_only():
    with patch(SERVICE, return_value=_detail()):
        doc = SkillExporter().export("skill-uuid-1", user=object())
    body = to_canonical_bytes(doc)
    assert json.loads(body) == json.loads(GOLDEN.read_text())
    assert_conforms(json.loads(body))
    assert body == to_canonical_bytes(doc)
    for forbidden in (b"skill-uuid-1", b"cfg-id-1", b"a/b.md", b"date", b"assistants_count", b"user_abilities"):
        assert forbidden not in body


def test_toolkits_mcp_servers_and_companion_files_are_not_in_the_document():
    # The envelope's skill spec has no slot for them (it forbids unknown fields); applying leaves them untouched.
    with patch(SERVICE, return_value=_detail()):
        spec = json.loads(to_canonical_bytes(SkillExporter().export("skill-uuid-1", user=object())))["spec"]
    assert not {"toolkits", "mcp_servers", "companion_files"} & set(spec)


def test_forbidden_user_gets_403():
    err = ExtendedHTTPException(code=403, message="denied")
    with patch(SERVICE, side_effect=err):
        with pytest.raises(ExtendedHTTPException) as exc:
            SkillExporter().export("skill-uuid-1", user=object())
    assert exc.value.code == 403


def test_missing_skill_gets_404():
    with patch(SERVICE, side_effect=ExtendedHTTPException(code=404, message="nf")):
        with pytest.raises(ExtendedHTTPException) as exc:
            SkillExporter().export("x", user=object())
    assert exc.value.code == 404
