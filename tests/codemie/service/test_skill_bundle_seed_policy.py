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

"""Bundle seed policy: limits, the plan that carries them, and the upload warning built on it (EPMCDME-15558)."""

from __future__ import annotations

import pytest

import codemie.service.skill_bundle_seed_policy as policy
from codemie.rest_api.models.skill import Skill, SkillCompanionFileRef
from codemie.service.file_service.workspace_path_rules import MAX_SKILL_BUNDLE_PATH_LENGTH
from codemie.service.skill_bundle_seed_policy import (
    DEFAULT_SKILL_WORKSPACE_SEED_MAX_FILE_COUNT,
    DEFAULT_SKILL_WORKSPACE_SEED_MAX_FILE_SIZE_BYTES,
    DEFAULT_SKILL_WORKSPACE_SEED_MAX_TOTAL_SIZE_BYTES,
    SEED_SKIP_COUNT_EXCEEDED,
    SEED_SKIP_INLINE,
    SEED_SKIP_INVALID_PATH,
    SEED_SKIP_TOO_LARGE,
    SEED_SKIP_TOTAL_EXCEEDED,
    SeedLimits,
    bundle_seed_skip_messages,
    compute_bundle_warning,
    plan_bundle_seed,
)

MB = 1024 * 1024
DEFAULTS = SeedLimits(max_count=100, max_file_size=10 * MB, max_total_size=50 * MB)
FOLDER = "skills/my-skill"


def _ref(path: str, size: int) -> SkillCompanionFileRef:
    return SkillCompanionFileRef(path=path, size_bytes=size, storage_key=f"ab12/{path}")


def _skill(*refs: SkillCompanionFileRef) -> Skill:
    files = [{**ref.model_dump(), "storage_key": ref.storage_key} for ref in refs]
    return Skill(id="sk123456", name="my-skill", description="d", content="c" * 200, project="p", companion_files=files)


class TestSeedLimits:
    def test_defaults(self, monkeypatch):
        monkeypatch.setattr(
            policy.DynamicConfigService, "get_typed_value_safe", lambda key, expected, default=None: default
        )

        assert SeedLimits.current() == DEFAULTS
        assert DEFAULT_SKILL_WORKSPACE_SEED_MAX_FILE_COUNT == 100
        assert DEFAULT_SKILL_WORKSPACE_SEED_MAX_FILE_SIZE_BYTES == 10 * MB
        assert DEFAULT_SKILL_WORKSPACE_SEED_MAX_TOTAL_SIZE_BYTES == 50 * MB

    def test_reads_each_setting_from_its_own_key(self, monkeypatch):
        values = {
            "SKILL_WORKSPACE_SEED_MAX_FILE_COUNT": 7,
            "SKILL_WORKSPACE_SEED_MAX_FILE_SIZE_BYTES": 8,
            "SKILL_WORKSPACE_SEED_MAX_TOTAL_SIZE_BYTES": 9,
        }
        monkeypatch.setattr(
            policy.DynamicConfigService, "get_typed_value_safe", lambda key, expected, default=None: values[key]
        )

        assert SeedLimits.current() == SeedLimits(max_count=7, max_file_size=8, max_total_size=9)

    @pytest.mark.parametrize("bad", [0, -5, True])
    def test_a_misconfigured_value_falls_back_to_the_default(self, monkeypatch, bad):
        monkeypatch.setattr(
            policy.DynamicConfigService, "get_typed_value_safe", lambda key, expected, default=None: bad
        )

        assert SeedLimits.current() == DEFAULTS

    def test_the_minimum_is_a_parameter_of_the_setting_reader(self, monkeypatch):
        monkeypatch.setattr(policy.DynamicConfigService, "get_typed_value_safe", lambda key, expected, default=None: 0)

        assert policy._int_setting("K", 5, minimum=0) == 0
        assert policy._int_setting("K", 5) == 5

    def test_limits_are_frozen(self):
        with pytest.raises(AttributeError):
            DEFAULTS.max_count = 1  # type: ignore[misc]


class TestPlanBundleSeed:
    def test_ten_2mb_files_are_all_seeded(self):
        files = [_ref(f"f{i}.bin", 2 * MB) for i in range(10)]

        plan = plan_bundle_seed(files, FOLDER, DEFAULTS)

        assert plan.to_seed == files
        assert plan.skipped == []

    def test_total_limit_skips_files_that_do_not_fit(self):
        plan = plan_bundle_seed([_ref(f"f{i}.bin", 9 * MB) for i in range(6)], FOLDER, DEFAULTS)

        assert len(plan.to_seed) == 5
        assert plan.skipped_count(SEED_SKIP_TOTAL_EXCEEDED) == 1

    def test_smaller_file_still_fits_after_total_limit_hit(self):
        files = [_ref(f"f{i}.bin", 9 * MB) for i in range(6)] + [_ref("small.txt", 1)]

        assert files[-1] in plan_bundle_seed(files, FOLDER, DEFAULTS).to_seed

    def test_file_over_per_file_limit_is_too_large(self):
        plan = plan_bundle_seed([_ref("big.bin", 11 * MB)], FOLDER, DEFAULTS)

        assert plan.to_seed == []
        assert plan.skipped_count(SEED_SKIP_TOO_LARGE) == 1

    def test_count_limit(self):
        plan = plan_bundle_seed([_ref(f"f{i}", 1) for i in range(3)], FOLDER, SeedLimits(2, 10, 100))

        assert len(plan.to_seed) == 2
        assert plan.skipped_count(SEED_SKIP_COUNT_EXCEEDED) == 1

    @pytest.mark.parametrize("path", ["../escape.py", "a\u2028b.py", "a" * 1010])
    def test_invalid_final_path(self, path):
        assert plan_bundle_seed([_ref(path, 1)], FOLDER, DEFAULTS).skipped_count(SEED_SKIP_INVALID_PATH) == 1

    @pytest.mark.parametrize(
        ("length", "seeded"), [(MAX_SKILL_BUNDLE_PATH_LENGTH, True), (MAX_SKILL_BUNDLE_PATH_LENGTH + 1, False)]
    )
    def test_bundle_path_length_boundary_matches_what_a_read_accepts(self, length, seeded):
        plan = plan_bundle_seed([_ref("a" * length, 1)], FOLDER, DEFAULTS)

        assert bool(plan.to_seed) is seeded

    def test_files_are_planned_in_path_order_whatever_the_stored_order(self):
        files = [_ref("c.md", 1), _ref("a.md", 1), _ref("b.md", 1)]

        plan = plan_bundle_seed(files, FOLDER, SeedLimits(max_count=2, max_file_size=10, max_total_size=100))

        assert [r.path for r in plan.to_seed] == ["a.md", "b.md"]
        assert [r.path for r, _ in plan.skipped] == ["c.md"]

    def test_the_plan_carries_the_limits_it_was_made_with(self):
        limits = SeedLimits(max_count=2, max_file_size=10, max_total_size=15)

        plan = plan_bundle_seed([_ref("a", 8), _ref("b", 8), _ref("c", 8)], FOLDER, limits)

        assert plan.limits is limits
        assert [r.path for r in plan.to_seed] == ["a"]  # b would push the total over 15
        assert any("15-byte total size limit" in m for m in bundle_seed_skip_messages(plan))

    def test_inline_files_are_skipped_with_the_agreed_wording(self):
        inline = SkillCompanionFileRef(path="a.md", size_bytes=1)  # no storage_key: a legacy inline file

        plan = plan_bundle_seed([inline], FOLDER, DEFAULTS)

        assert plan.to_seed == []
        assert plan.skipped == [(inline, SEED_SKIP_INLINE)]
        assert "not available in the workspace; read them through the skill tool" in " ".join(
            bundle_seed_skip_messages(plan)
        )


class TestSkipMessagesNameTheFiles:
    def test_skipped_files_are_named_in_path_order(self):
        plan = plan_bundle_seed([_ref(f"f{i}", 1) for i in range(4)], FOLDER, SeedLimits(2, 10, 100))

        assert bundle_seed_skip_messages(plan) == ["2 file(s) exceed the 2-file limit (f2, f3)."]

    def test_a_long_list_is_capped(self):
        plan = plan_bundle_seed([_ref(f"f{i:02d}", 1) for i in range(9)], FOLDER, SeedLimits(1, 10, 100))

        message = bundle_seed_skip_messages(plan)[0]

        assert "f01, f02, f03, f04, f05, ... and 3 more" in message
        assert "f08" not in message

    def test_a_path_the_seeder_would_not_use_as_is_is_invalid(self):
        assert plan_bundle_seed([_ref("a/./b.py", 1)], FOLDER, DEFAULTS).skipped_count(SEED_SKIP_INVALID_PATH) == 1
        assert plan_bundle_seed([_ref(" a.py", 1)], FOLDER, DEFAULTS).skipped_count(SEED_SKIP_INVALID_PATH) == 1


class TestComputeBundleWarning:
    @pytest.fixture(autouse=True)
    def _default_limits(self, monkeypatch):
        monkeypatch.setattr(SeedLimits, "current", classmethod(lambda cls: DEFAULTS))

    def test_no_warning_for_ten_2mb_files(self):
        assert compute_bundle_warning(_skill(*[_ref(f"f{i}.bin", 2 * MB) for i in range(10)])) is None

    def test_no_warning_for_empty_bundle(self):
        assert compute_bundle_warning(_skill()) is None

    def test_total_limit_warning(self):
        warning = compute_bundle_warning(_skill(*[_ref(f"f{i}.bin", 9 * MB) for i in range(6)]))

        assert warning is not None
        assert "1 file(s)" in warning and f"{50 * MB}-byte total size limit" in warning
        assert "assistant workspace" in warning

    def test_per_file_limit_warning(self):
        warning = compute_bundle_warning(_skill(_ref("big.bin", 11 * MB)))

        assert warning is not None and f"{10 * MB}-byte per-file limit" in warning

    def test_count_limit_warning_uses_the_current_limits(self, monkeypatch):
        monkeypatch.setattr(SeedLimits, "current", classmethod(lambda cls: SeedLimits(2, 10 * MB, 50 * MB)))

        warning = compute_bundle_warning(_skill(*[_ref(f"f{i}.md", 10) for i in range(3)]))

        assert warning is not None and "2-file limit" in warning

    def test_inline_files_are_reported_too(self):
        skill = Skill(
            id="sk123456",
            name="my-skill",
            description="d",
            content="c" * 200,
            project="p",
            companion_files=[{"path": "a.md", "size_bytes": 1, "content": "hi"}],
        )

        assert "read them through the skill tool" in compute_bundle_warning(skill)
