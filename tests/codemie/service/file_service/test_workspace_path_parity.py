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

"""Skill upload, workspace seeding and workspace writes agree on which bundle paths are usable (EPMCDME-15558)."""

from __future__ import annotations

import pytest

from codemie.core.exceptions import ExtendedHTTPException, ValidationException
from codemie.rest_api.models.skill import SkillCompanionFileRef
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie.service.file_service.workspace_path_rules import (
    MAX_SKILL_BUNDLE_PATH_LENGTH,
    MAX_SKILL_NAME_LENGTH,
    SKILL_FOLDER_ID_LENGTH,
    SKILL_FOLDER_PREFIX,
)
from codemie.service.skill_bundle_seed_policy import SeedLimits, plan_bundle_seed
from codemie.service.skill_file_storage import SkillFileStorage

# The longest allowed skill name gives the tightest budget for the bundle path.
FOLDER = f"{SKILL_FOLDER_PREFIX}{'n' * MAX_SKILL_NAME_LENGTH}-{'i' * SKILL_FOLDER_ID_LENGTH}"

# Paths whose verdict must be identical at upload, seeding and the final workspace write.
SHARED_PATHS = [
    ("scripts/run.py", True),
    ("a" * MAX_SKILL_BUNDLE_PATH_LENGTH, True),
    ("a" * (MAX_SKILL_BUNDLE_PATH_LENGTH + 1), False),
    ("../escape.py", False),
    ("a/../b.py", False),
    ("a\nb.py", False),
    ("a\x7fb.py", False),
    ("a\u0085b.py", False),
    ("a\u2028b.py", False),
]
# Shapes only meaningful for a bundle path; joined under the skill folder they would look relative.
BUNDLE_ONLY_PATHS = ["/abs.py", "C:/x.py", "c:x.py"]


def _upload_accepts(path: str) -> bool:
    try:
        SkillFileStorage._normalize_companion_file_path(path)
    except ExtendedHTTPException:
        return False
    return True


def _seeder_accepts(path: str) -> bool:
    plan = plan_bundle_seed(
        [SkillCompanionFileRef(path=path, size_bytes=1, storage_key="k/x")], FOLDER, SeedLimits(100, 100, 100)
    )
    return bool(plan.to_seed)


def _service_accepts(path: str) -> bool:
    try:
        AgentWorkspaceService._normalize_path(f"{FOLDER}/{path}")
    except ValidationException:
        return False
    return True


@pytest.mark.parametrize(("path", "expected"), SHARED_PATHS)
def test_upload_seeder_and_service_agree(path, expected):
    assert (_upload_accepts(path), _seeder_accepts(path), _service_accepts(path)) == (expected,) * 3


@pytest.mark.parametrize("path", BUNDLE_ONLY_PATHS)
def test_upload_and_seeder_reject_absolute_bundle_paths(path):
    assert (_upload_accepts(path), _seeder_accepts(path)) == (False, False)
