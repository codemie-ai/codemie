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

"""SkillWorkspaceSeeder: skills are seeded by reference through a store; no blobs are read or written."""

from __future__ import annotations

import inspect
from unittest.mock import patch

from codemie.repository.agent_workspace_repository import SeedOutcome
from codemie.rest_api.models.agent_workspace import SOURCE_KIND_SKILL
from codemie.rest_api.models.skill import Skill
from codemie.service.skill_bundle_seed_policy import SeedLimits
from codemie.service.skill_workspace_seeder import SkillWorkspaceSeeder


class FakeStore:
    def __init__(self, outcomes: dict[str, SeedOutcome] | None = None, fail_for: set[str] | None = None):
        self.calls: list[tuple[str, str, str, list]] = []
        self.source_kinds: list[str] = []
        self._outcomes = outcomes or {}
        self._fail_for = fail_for or set()

    def seed_references(self, workspace_id, source_kind, source_id, folder, files):
        self.source_kinds.append(source_kind)
        self.calls.append((workspace_id, source_id, folder, list(files)))
        if source_id in self._fail_for:
            raise RuntimeError("db down")
        return self._outcomes.get(source_id, SeedOutcome.SEEDED)


def _skill(skill_id: str, name: str, files: list[dict]) -> Skill:
    return Skill(id=skill_id, name=name, description="d", content="c" * 200, project="p", companion_files=files)


def _file(path: str, key: str = "ab12/x", size: int = 3) -> dict:
    return {
        "path": path,
        "mime_type": "text/plain",
        "encoding": "text",
        "size_bytes": size,
        "storage_key": key,
        "checksum": "ff",
    }


def _inline(path: str) -> dict:
    return {"path": path, "mime_type": "text/markdown", "encoding": "text", "size_bytes": 2, "content": "hi"}


def test_seeds_references_under_the_skill_folder():
    store = FakeStore()

    notices = SkillWorkspaceSeeder(store).ensure_seeded("w1", [_skill("sk1", "foo", [_file("a.py", "ab12/a.py")])])

    assert notices == []
    (workspace_id, source_id, folder, files) = store.calls[0]
    assert (workspace_id, source_id, folder) == ("w1", "sk1", "skills/foo-sk1")
    assert store.source_kinds == [SOURCE_KIND_SKILL]
    assert [(f.path, f.blob_name, f.checksum, f.size) for f in files] == [("skills/foo-sk1/a.py", "ab12/a.py", "ff", 3)]


def test_skills_are_processed_in_the_given_order():
    store = FakeStore()
    skills = [_skill("sk2", "bar", [_file("a.py")]), _skill("sk1", "foo", [_file("a.py")])]

    SkillWorkspaceSeeder(store).ensure_seeded("w1", skills)

    assert [call[1] for call in store.calls] == ["sk2", "sk1"]


def test_same_named_skills_get_separate_folders_without_a_collision():
    store = FakeStore()
    skills = [_skill("sk1", "foo", [_file("a.py")]), _skill("sk2", "foo", [_file("a.py")])]

    notices = SkillWorkspaceSeeder(store).ensure_seeded("w1", skills)

    assert notices == []
    assert [call[2] for call in store.calls] == ["skills/foo-sk1", "skills/foo-sk2"]


def test_collision_notice_names_the_skill_and_folder():
    store = FakeStore({"sk2": SeedOutcome.COLLISION})

    notices = SkillWorkspaceSeeder(store).ensure_seeded("w1", [_skill("sk2", "foo", [_file("a.py")])])

    assert len(notices) == 1
    assert "foo" in notices[0] and "skills/foo-sk2/" in notices[0]


def test_already_seeded_gives_no_notice():
    store = FakeStore({"sk1": SeedOutcome.ALREADY_SEEDED})

    assert SkillWorkspaceSeeder(store).ensure_seeded("w1", [_skill("sk1", "foo", [_file("a.py")])]) == []


def test_legacy_inline_files_are_skipped_with_the_agreed_notice_and_no_owner_is_recorded():
    store = FakeStore()
    seeder = SkillWorkspaceSeeder(store)
    skill = _skill("sk1", "foo", [_inline("a.md")])

    first = seeder.ensure_seeded("w1", [skill])
    second = seeder.ensure_seeded("w1", [skill])

    assert store.calls == []
    assert "not available in the workspace; read them through the skill tool" in first[0]
    assert second == first  # the notice repeats every turn because nothing is frozen


def test_skill_without_files_writes_nothing():
    store = FakeStore()

    assert SkillWorkspaceSeeder(store).ensure_seeded("w1", [_skill("sk1", "foo", [])]) == []
    assert store.calls == []


def test_over_limit_notice_appears_when_seeded_and_not_when_already_seeded():
    skill = _skill("sk1", "foo", [_file("a.py"), _file("b.py")])
    with patch.object(
        SeedLimits, "current", return_value=SeedLimits(max_count=1, max_file_size=10, max_total_size=100)
    ):
        first_store = FakeStore()
        first = SkillWorkspaceSeeder(first_store).ensure_seeded("w1", [skill])
        later = SkillWorkspaceSeeder(FakeStore({"sk1": SeedOutcome.ALREADY_SEEDED})).ensure_seeded("w1", [skill])

    assert [f.path for f in first_store.calls[0][3]] == ["skills/foo-sk1/a.py"]
    assert len(first) == 1 and "1-file limit" in first[0]
    assert later == []


def test_store_error_becomes_a_notice_and_other_skills_continue():
    store = FakeStore(fail_for={"sk1"})
    skills = [_skill("sk1", "foo", [_file("a.py")]), _skill("sk2", "bar", [_file("b.py")])]

    notices = SkillWorkspaceSeeder(store).ensure_seeded("w1", skills)

    assert [call[1] for call in store.calls] == ["sk1", "sk2"]
    assert len(notices) == 1 and "foo" in notices[0]


def test_constructor_takes_only_the_store():
    assert list(inspect.signature(SkillWorkspaceSeeder.__init__).parameters) == ["self", "store"]


def test_seeder_depends_only_on_the_store_protocol():
    import ast

    import codemie.service.skill_workspace_seeder as module

    imported = {
        node.module for node in ast.walk(ast.parse(inspect.getsource(module))) if isinstance(node, ast.ImportFrom)
    }

    assert not {
        m for m in imported if m and m.startswith(("codemie.agents", "codemie.service.agent_workspace_service"))
    }
    assert getattr(module.SkillSeedStore, "_is_protocol", False)


class TestSkippedFilesAreLogged:
    def test_over_limit_and_inline_skips_are_logged_with_the_skill_and_the_reasons(self):
        skill = _skill("sk1", "foo", [_file("a.py"), _file("b.py"), _inline("old.md")])
        with (
            patch.object(
                SeedLimits, "current", return_value=SeedLimits(max_count=1, max_file_size=10, max_total_size=100)
            ),
            patch("codemie.service.skill_workspace_seeder.logger") as logger_mock,
        ):
            SkillWorkspaceSeeder(FakeStore()).ensure_seeded("w1", [skill])

        logged = " ".join(str(call.args[0]) for call in logger_mock.warning.call_args_list)
        assert "foo" in logged and "skills/foo-sk1" in logged
        assert "count_exceeded" in logged and "inline_content" in logged

    def test_a_legacy_skill_with_nothing_to_seed_is_logged_every_time(self):
        skill = _skill("sk1", "foo", [_inline("a.md")])
        with patch("codemie.service.skill_workspace_seeder.logger") as logger_mock:
            SkillWorkspaceSeeder(FakeStore()).ensure_seeded("w1", [skill])
            SkillWorkspaceSeeder(FakeStore()).ensure_seeded("w1", [skill])

        assert logger_mock.warning.call_count == 2

    def test_nothing_is_logged_when_nothing_is_skipped(self):
        with patch("codemie.service.skill_workspace_seeder.logger") as logger_mock:
            SkillWorkspaceSeeder(FakeStore()).ensure_seeded("w1", [_skill("sk1", "foo", [_file("a.py")])])

        logger_mock.warning.assert_not_called()
