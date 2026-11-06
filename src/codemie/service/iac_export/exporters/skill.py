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


"""Skill exporter: the envelope's SkillSpec fields only.

Companion files, toolkit requirements and MCP requirements have no place in the envelope's skill spec, so they are
never exported (see "Deliberately non-exportable" in the task spec); applying the document leaves them untouched.
"""

from typing import Any

from pydantic import BaseModel, Field

from codemie.rest_api.models.skill import SkillDetailResponse
from codemie.service.iac_export.document import ExportDocument, ExportMeta
from codemie.service.skill_service import SkillService


class SkillSpec(BaseModel):
    name: str
    description: str
    content: str
    project: str
    visibility: str
    categories: list[str] = Field(default_factory=list)
    enabled_builtin_subagents: list[str] = Field(default_factory=list)


def _build_spec(skill: SkillDetailResponse) -> SkillSpec:
    return SkillSpec(
        name=skill.name,
        description=skill.description,
        content=skill.content,
        project=skill.project,
        visibility=skill.visibility.value,
        categories=sorted(c.value for c in skill.categories),
        enabled_builtin_subagents=sorted(s.value for s in skill.enabled_builtin_subagents),
    )


class SkillExporter:
    def export(self, id: str, user: Any) -> ExportDocument:
        # Raises ExtendedHTTPException (404 not found / 403 no read access); the router lets it propagate.
        skill = SkillService.get_skill_by_id(id, user)
        return ExportDocument(meta=ExportMeta(kind="skill", name=skill.name), spec=_build_spec(skill))
