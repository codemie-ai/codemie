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

"""Skill companion-file integrity: exporting a skill that has companion files and updating it from the export
document through the real update path (router -> SkillService.update_skill) must leave the companion files untouched.

Only the persistence layer is faked; the exporter, update request, router and service are real, so the companion
handling in `SkillService.update_skill` (upload / replace only when `companion_files` is sent) is exercised.
"""

import copy
from unittest.mock import patch

from codemie.rest_api.models.skill import SkillUpdateRequest
from codemie.rest_api.routers import skill as skill_router
from codemie.service.iac_export.exporters.skill import SkillExporter
from codemie.service.skill_service import SkillService
from tests.codemie.rest_api.routers.test_export_roundtrip import (
    _admin,
    _client,
    _skill,
    _skill_persistence,
    _skill_update_body,
    _spec,
)

COMPANIONS = [
    {
        "path": "references/guide.md",
        "mime_type": "text/markdown",
        "encoding": "text",
        "size_bytes": 12,
        "content": "# Guide text",
    },
    {
        "path": "assets/logo.bin",
        "mime_type": "application/octet-stream",
        "encoding": "base64",
        "size_bytes": 4,
        "content": "AAECAw==",
    },
]


def test_update_from_export_document_keeps_companion_files_unchanged():
    skill = _skill()
    skill.companion_files = copy.deepcopy(COMPANIONS)
    user = _admin()

    with _skill_persistence(skill):
        metadata_before = SkillService.list_companion_files(skill.id, user)
        stored_before = copy.deepcopy(skill.companion_files)

        document = SkillExporter().export(skill.id, user)
        body = _skill_update_body(_spec(document))
        assert "companion_files" not in body
        SkillUpdateRequest(**body)

        with patch.object(SkillService, "_upload_companion_files") as upload:
            response = _client(skill_router.router).put(f"/v1/skills/{skill.id}", json=body)

        assert response.status_code == 200, response.text
        upload.assert_not_called()
        assert SkillService.list_companion_files(skill.id, user) == metadata_before

    assert metadata_before  # guard: the skill really had companion files
    assert skill.companion_files == stored_before == COMPANIONS
    assert [f["path"] for f in response.json()["companion_files"]] == [f["path"] for f in COMPANIONS]
