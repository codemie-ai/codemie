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

"""Tests for the shared workspace path rules (EPMCDME-15558)."""

from __future__ import annotations

import pytest

from codemie.service.file_service.workspace_path_rules import (
    MAX_SKILL_BUNDLE_PATH_LENGTH,
    MAX_WORKSPACE_PATH_LENGTH,
    is_reserved_workspace_path,
    validate_relative_path,
)


def test_bundle_budget_leaves_room_for_skill_folder():
    assert MAX_SKILL_BUNDLE_PATH_LENGTH == MAX_WORKSPACE_PATH_LENGTH - len("skills/") - 64 - 9 - 1 == 943


@pytest.mark.parametrize(
    "path",
    [
        "",
        "   ",
        "/abs.py",
        "C:/x.py",
        "c:x.py",
        "../x.py",
        "a/../../x.py",
        "a/../b.py",
        ".",
        "..",
        "a\nb.py",
        "a.py\n",
        "a\x00b",
        "a\x7fb",
        "a\u0085b",
        "a\u2028b",
        "a\u2029b",
        " a.py",
        "a.py ",
    ],
)
def test_rejects_invalid_paths(path):
    with pytest.raises(ValueError):
        validate_relative_path(path, MAX_WORKSPACE_PATH_LENGTH)


@pytest.mark.parametrize("path", ["a\u200eb.txt", "\U0001f468\u200d\U0001f469.txt", "\u202eb.txt"])
def test_format_characters_in_names_are_accepted(path):
    assert validate_relative_path(path, MAX_WORKSPACE_PATH_LENGTH) == path


def test_rejects_over_length():
    with pytest.raises(ValueError, match="too long"):
        validate_relative_path("a" * 11, 10)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("scripts/run.py", "scripts/run.py"),
        ("scripts\\run.py", "scripts/run.py"),
        ("./a//b.md", "a/b.md"),
        ("a" * 10, "a" * 10),
    ],
)
def test_normalizes_valid_paths(raw, expected):
    assert validate_relative_path(raw, 1024) == expected


@pytest.mark.parametrize(
    "path, reserved",
    [
        (".codemie_bridge", True),
        (".codemie_bridge/req.1.json", True),
        ("codemie_runtime_sdk.py", True),
        ("skills/demo/codemie_runtime_sdk.py", False),
        ("a/.codemie_bridge/x", False),
        ("codemie_runtime_sdk.py.bak", False),
        ("notes.md", False),
    ],
)
def test_is_reserved_workspace_path(path, reserved):
    assert is_reserved_workspace_path(path) is reserved


def test_skill_workspace_folder_is_the_prefix_name_and_short_id():
    from codemie.service.file_service.workspace_path_rules import skill_workspace_folder

    assert skill_workspace_folder("foo", "0123456789abcdef") == "skills/foo-01234567"


def test_the_skill_folder_name_is_built_in_one_place():
    import pathlib

    offenders = [
        str(path)
        for path in pathlib.Path("src/codemie").rglob("*.py")
        if "SKILL_FOLDER_PREFIX}" in path.read_text() and path.name != "workspace_path_rules.py"
    ]

    assert offenders == []


def test_every_skill_name_limit_uses_the_shared_constant():
    from codemie.rest_api.models.skill import Skill, SkillCreateRequest, SkillUpdateRequest
    from codemie.service.file_service.workspace_path_rules import MAX_SKILL_NAME_LENGTH

    def max_lengths(field) -> set:
        return {getattr(m, "max_length", None) for m in field.metadata} - {None}

    assert max_lengths(SkillCreateRequest.model_fields["name"]) == {MAX_SKILL_NAME_LENGTH}
    assert max_lengths(SkillUpdateRequest.model_fields["name"]) == {MAX_SKILL_NAME_LENGTH}
    assert Skill.__table__.c.name.type.length == MAX_SKILL_NAME_LENGTH
    import pathlib

    # equal by value is not enough: the literal must be gone, or changing one silently breaks the other
    assert "max_length=64" not in pathlib.Path("src/codemie/rest_api/models/skill.py").read_text()
