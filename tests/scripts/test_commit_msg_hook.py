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

"""Subprocess tests for scripts/git-hooks/commit_msg.sh (EPMCDME-13747)."""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "scripts" / "git-hooks" / "commit_msg.sh"


def _run(repo: Path, message: str, env_overrides=None):
    msg_file = repo / "COMMIT_EDITMSG"
    msg_file.write_text(message)
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", str(HOOK), str(msg_file)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "scratch"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main"], cwd=repo, check=True)
    return repo


def test_valid_prefix_accepted(tmp_path):
    repo = _git_repo(tmp_path)
    result = _run(repo, "EPMCDME-13747: Add pre-push hook\n")
    assert result.returncode == 0, result.stdout + result.stderr


def test_missing_prefix_rejected(tmp_path):
    repo = _git_repo(tmp_path)
    result = _run(repo, "fix: something without a ticket\n")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "EPMCDME-" in (result.stdout + result.stderr)


def test_prefix_without_colon_rejected(tmp_path):
    repo = _git_repo(tmp_path)
    result = _run(repo, "EPMCDME-13747 missing the colon\n")
    assert result.returncode == 1, result.stdout + result.stderr


def test_merge_commit_message_bypassed(tmp_path):
    repo = _git_repo(tmp_path)
    result = _run(repo, "Merge branch 'main' into feature\n")
    assert result.returncode == 0, result.stdout + result.stderr


def test_fixup_message_bypassed(tmp_path):
    repo = _git_repo(tmp_path)
    result = _run(repo, "fixup! EPMCDME-13747: earlier commit\n")
    assert result.returncode == 0, result.stdout + result.stderr


def test_squash_message_bypassed(tmp_path):
    repo = _git_repo(tmp_path)
    result = _run(repo, "squash! EPMCDME-13747: earlier commit\n")
    assert result.returncode == 0, result.stdout + result.stderr


def test_amend_message_bypassed(tmp_path):
    repo = _git_repo(tmp_path)
    result = _run(repo, "amend! EPMCDME-13747: earlier commit\n")
    assert result.returncode == 0, result.stdout + result.stderr


def test_revert_message_bypassed(tmp_path):
    repo = _git_repo(tmp_path)
    result = _run(repo, 'Revert "EPMCDME-13747: some earlier commit"\n')
    assert result.returncode == 0, result.stdout + result.stderr


def test_merge_head_present_bypasses_validation(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / ".git" / "MERGE_HEAD").write_text("0" * 40 + "\n")
    result = _run(repo, "no ticket here at all\n")
    assert result.returncode == 0, result.stdout + result.stderr


def test_rebase_head_present_bypasses_validation(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / ".git" / "REBASE_HEAD").write_text("0" * 40 + "\n")
    result = _run(repo, "no ticket here at all\n")
    assert result.returncode == 0, result.stdout + result.stderr


def test_cherry_pick_head_present_bypasses_validation(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / ".git" / "CHERRY_PICK_HEAD").write_text("0" * 40 + "\n")
    result = _run(repo, "no ticket here at all\n")
    assert result.returncode == 0, result.stdout + result.stderr


def test_leading_comment_lines_ignored(tmp_path):
    repo = _git_repo(tmp_path)
    result = _run(repo, "# a comment\n\nEPMCDME-13747: real subject\n")
    assert result.returncode == 0, result.stdout + result.stderr
