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

"""Which companion files of a skill bundle can be made available in an agent workspace, and why the rest cannot.

The seeder and the upload warning both use this module, so the numbers a person is told are the numbers applied.
"""

from __future__ import annotations

from dataclasses import dataclass

from codemie.rest_api.models.skill import Skill, SkillCompanionFilePayload, SkillCompanionFileRef
from codemie.service.dynamic_config_service import DynamicConfigService
from codemie.service.file_service.workspace_path_rules import (
    MAX_SKILL_BUNDLE_PATH_LENGTH,
    MAX_WORKSPACE_PATH_LENGTH,
    SKILL_FOLDER_ID_LENGTH,
    skill_workspace_folder,
    validate_relative_path,
)

# Ceilings for one skill bundle in one workspace. Not a hard rejection: a bundle over a ceiling is still accepted at
# upload (and warned); individual files stay readable through the skill tool regardless of these ceilings.
DEFAULT_SKILL_WORKSPACE_SEED_MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
SKILL_WORKSPACE_SEED_MAX_FILE_SIZE_BYTES_CONFIG_KEY = "SKILL_WORKSPACE_SEED_MAX_FILE_SIZE_BYTES"

DEFAULT_SKILL_WORKSPACE_SEED_MAX_FILE_COUNT = 100
SKILL_WORKSPACE_SEED_MAX_FILE_COUNT_CONFIG_KEY = "SKILL_WORKSPACE_SEED_MAX_FILE_COUNT"

# Independent of the per-file and per-count ceilings: a bundle can hit this one well before either of them.
DEFAULT_SKILL_WORKSPACE_SEED_MAX_TOTAL_SIZE_BYTES = 50 * 1024 * 1024
SKILL_WORKSPACE_SEED_MAX_TOTAL_SIZE_BYTES_CONFIG_KEY = "SKILL_WORKSPACE_SEED_MAX_TOTAL_SIZE_BYTES"

SEED_SKIP_INLINE = "inline_content"
SEED_SKIP_INVALID_PATH = "invalid_path"
SEED_SKIP_TOO_LARGE = "too_large"
SEED_SKIP_COUNT_EXCEEDED = "count_exceeded"
SEED_SKIP_TOTAL_EXCEEDED = "total_exceeded"

# A previewed archive has no skill id yet; the folder only needs the right length for the path check.
PREVIEW_SKILL_ID = "0" * SKILL_FOLDER_ID_LENGTH


def _int_setting(key: str, default: int, minimum: int = 1) -> int:
    """Read an integer setting; a missing, non-integer or below-``minimum`` value falls back to the default."""
    value = DynamicConfigService.get_typed_value_safe(key, int, default=default)
    # bool is a subclass of int; a bool-valued entry is always a misconfiguration and must not become a limit.
    if isinstance(value, bool) or value < minimum:
        return default
    return value


@dataclass(frozen=True)
class SeedLimits:
    """The ceilings a bundle is checked against, read once so a plan and its messages always agree."""

    max_count: int
    max_file_size: int
    max_total_size: int

    @classmethod
    def current(cls) -> SeedLimits:
        return cls(
            max_count=_int_setting(
                SKILL_WORKSPACE_SEED_MAX_FILE_COUNT_CONFIG_KEY, DEFAULT_SKILL_WORKSPACE_SEED_MAX_FILE_COUNT
            ),
            max_file_size=_int_setting(
                SKILL_WORKSPACE_SEED_MAX_FILE_SIZE_BYTES_CONFIG_KEY, DEFAULT_SKILL_WORKSPACE_SEED_MAX_FILE_SIZE_BYTES
            ),
            max_total_size=_int_setting(
                SKILL_WORKSPACE_SEED_MAX_TOTAL_SIZE_BYTES_CONFIG_KEY, DEFAULT_SKILL_WORKSPACE_SEED_MAX_TOTAL_SIZE_BYTES
            ),
        )


@dataclass(frozen=True)
class BundleSeedPlan:
    """Which companion files can be seeded and why the rest are skipped, with the limits the plan was made with."""

    to_seed: list[SkillCompanionFileRef]
    skipped: list[tuple[SkillCompanionFileRef, str]]
    limits: SeedLimits

    def skipped_count(self, reason: str) -> int:
        return sum(1 for _, skip_reason in self.skipped if skip_reason == reason)


def plan_bundle_seed(files: list[SkillCompanionFileRef], folder: str, limits: SeedLimits) -> BundleSeedPlan:
    """Classify bundle files for seeding into ``folder``; the seeder and the upload warning both use this.

    Files are taken in path order, so which files fit under a ceiling does not depend on the upload order.
    """
    to_seed: list[SkillCompanionFileRef] = []
    skipped: list[tuple[SkillCompanionFileRef, str]] = []
    total_size = 0
    for file_ref in sorted(files, key=lambda ref: ref.path):
        if not file_ref.storage_key:
            # Legacy inline files have no stored blob to point at; they stay readable through the skill tool.
            skipped.append((file_ref, SEED_SKIP_INLINE))
            continue
        try:
            # The bundle path must be one a read accepts (relative, within the bundle budget), and the final
            # workspace path must fit the workspace limit.
            bundle_path = validate_relative_path(file_ref.path, MAX_SKILL_BUNDLE_PATH_LENGTH)
            validate_relative_path(f"{folder}/{bundle_path}", MAX_WORKSPACE_PATH_LENGTH)
            if bundle_path != file_ref.path:
                # The seeder joins the stored path as is, so what was checked must be what is used.
                raise ValueError("path is not normalized")
        except ValueError:
            skipped.append((file_ref, SEED_SKIP_INVALID_PATH))
            continue
        if file_ref.size_bytes > limits.max_file_size:
            skipped.append((file_ref, SEED_SKIP_TOO_LARGE))
        elif len(to_seed) >= limits.max_count:
            skipped.append((file_ref, SEED_SKIP_COUNT_EXCEEDED))
        elif total_size + file_ref.size_bytes > limits.max_total_size:
            skipped.append((file_ref, SEED_SKIP_TOTAL_EXCEEDED))
        else:
            to_seed.append(file_ref)
            total_size += file_ref.size_bytes
    return BundleSeedPlan(to_seed=to_seed, skipped=skipped, limits=limits)


MAX_LISTED_SKIPPED_PATHS = 5


def _skipped_paths_suffix(paths: list[str]) -> str:
    """The skipped file names for a message, capped so a huge bundle cannot flood the prompt."""
    listed = ", ".join(paths[:MAX_LISTED_SKIPPED_PATHS])
    extra = len(paths) - MAX_LISTED_SKIPPED_PATHS
    return f" ({listed}{f', ... and {extra} more' if extra > 0 else ''})"


def bundle_seed_skip_messages(plan: BundleSeedPlan) -> list[str]:
    """One sentence per skip reason present in the plan, naming the limit and the files it was applied to."""
    limits = plan.limits
    templates = {
        SEED_SKIP_INLINE: "{n} companion file(s) are not available in the workspace; read them through the skill tool.",
        SEED_SKIP_INVALID_PATH: "{n} file(s) have paths that cannot be used in an assistant workspace.",
        SEED_SKIP_TOO_LARGE: f"{{n}} file(s) exceed the {limits.max_file_size}-byte per-file limit.",
        SEED_SKIP_COUNT_EXCEEDED: f"{{n}} file(s) exceed the {limits.max_count}-file limit.",
        SEED_SKIP_TOTAL_EXCEEDED: f"{{n}} file(s) exceed the {limits.max_total_size}-byte total size limit.",
    }
    messages = []
    for reason, template in templates.items():
        paths = [file_ref.path for file_ref, skip_reason in plan.skipped if skip_reason == reason]
        if paths:
            sentence = template.format(n=len(paths))
            messages.append(sentence[:-1] + _skipped_paths_suffix(paths) + sentence[-1])
    return messages


def _warning_for(plan: BundleSeedPlan) -> str | None:
    messages = bundle_seed_skip_messages(plan)
    if not messages:
        return None
    return " ".join(messages) + " These files will not be available in an assistant workspace."


def compute_bundle_warning(skill: Skill) -> str | None:
    """Return an advisory warning listing files a workspace seed would skip, else None."""
    folder = skill_workspace_folder(skill.name, skill.id)
    plan = plan_bundle_seed(skill.get_companion_file_refs(), folder, SeedLimits.current())
    return _warning_for(plan)


def compute_preview_bundle_warning(skill_name: str, files: list[SkillCompanionFilePayload]) -> str | None:
    """Same advisory as ``compute_bundle_warning`` for an archive that is only previewed, before anything is stored."""
    refs = [
        SkillCompanionFileRef(
            path=file.path,
            mime_type=file.mime_type,
            encoding=file.encoding,
            size_bytes=file.size_bytes,
            storage_key="preview",  # not stored yet; a non-empty key keeps the files out of the legacy-inline bucket
        )
        for file in files
    ]
    plan = plan_bundle_seed(refs, skill_workspace_folder(skill_name, PREVIEW_SKILL_ID), SeedLimits.current())
    return _warning_for(plan)
