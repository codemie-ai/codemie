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

"""Tests for scripts/check_alembic_heads.py (EPMCDME-16155)."""

import importlib.util
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_alembic_heads.py"
_spec = importlib.util.spec_from_file_location("check_alembic_heads", SCRIPT)
chk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chk)


def _mig(tree: Path, name: str, rev: str, down):
    (tree / name).write_text(f'revision = "{rev}"\ndown_revision = {down!r}\n', encoding="utf-8")


def _tree(root: Path) -> Path:
    t = root / "alembic" / "versions"
    t.mkdir(parents=True)
    (root / "alembic" / "env.py").write_text("", encoding="utf-8")
    return t


def test_a_linear_chain_has_one_head(tmp_path):
    t = _tree(tmp_path)
    _mig(t, "a_first.py", "a", None)
    _mig(t, "b_second.py", "b", "a")
    assert chk.main(["--root", str(tmp_path)]) == 0


def test_two_heads_a_missing_parent_and_a_duplicate_id_are_reported(tmp_path, capsys):
    t = _tree(tmp_path)
    _mig(t, "a.py", "a", None)
    _mig(t, "b.py", "b", "a")
    _mig(t, "c.py", "c", "a")  # second head
    _mig(t, "d.py", "d", "zzz")  # parent does not exist
    _mig(t, "e.py", "b", "a")  # duplicate revision id
    assert chk.main(["--root", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert (
        "exactly one head" in out
        and "down_revision zzz does not exist" in out
        and "revision b is defined in 2 files" in out
    )


def test_merge_with_catches_two_mrs_that_revise_the_same_parent(tmp_path):
    t = _tree(tmp_path)
    _mig(t, "a.py", "a", None)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-C", str(tmp_path)]
    subprocess.run(git[:5] + ["init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "base"], check=True)
    _mig(t, "b_other_mr.py", "b", "a")  # merged to the target meanwhile
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "other"], check=True)
    subprocess.run(git + ["checkout", "-q", "-b", "mine", "HEAD~1"], check=True)
    _mig(t, "c_mine.py", "c", "a")  # this MR also revises "a"
    assert chk.main(["--root", str(tmp_path)]) == 0  # alone it is healthy
    assert chk.main(["--root", str(tmp_path), "--merge-with", "main"]) == 1  # merged with the target it is not


def test_no_tree_means_nothing_to_check(tmp_path):
    assert chk.main(["--root", str(tmp_path)]) == 0


def test_heads_are_named_with_their_files(tmp_path, capsys):
    t = _tree(tmp_path)
    _mig(t, "a.py", "a", None)
    _mig(t, "b_one.py", "b", "a")
    _mig(t, "c_two.py", "c", "a")
    assert chk.main(["--root", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "b (b_one.py)" in out and "c (c_two.py)" in out


def test_a_merge_migration_and_a_first_migration_are_not_false_failures(tmp_path):
    t = _tree(tmp_path)
    _mig(t, "a.py", "a", None)
    _mig(t, "b.py", "b", "a")
    _mig(t, "c.py", "c", "a")
    _mig(t, "m.py", "m", ("b", "c"))
    assert chk.main(["--root", str(tmp_path)]) == 0


def test_a_file_that_is_not_python_fails_naming_the_file(tmp_path, capsys):
    t = _tree(tmp_path)
    _mig(t, "a.py", "a", None)
    (t / "broken.py").write_text("revision = (", encoding="utf-8")
    assert chk.main(["--root", str(tmp_path)]) == 1
    assert "broken.py: cannot be read as a migration" in capsys.readouterr().out


def test_an_unresolvable_ref_is_an_error_not_a_pass(tmp_path, capsys):
    t = _tree(tmp_path)
    _mig(t, "a.py", "a", None)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert chk.main(["--root", str(tmp_path), "--merge-with", "origin/main"]) == 2


def test_a_migration_without_a_down_revision_fails_naming_the_file(tmp_path, capsys):
    """Only an explicit `down_revision = None` marks a first migration; a missing line is a broken file."""
    t = _tree(tmp_path)
    (t / "only_rev.py").write_text('revision = "a"\n', encoding="utf-8")
    assert chk.main(["--root", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "only_rev.py: cannot be read as a migration" in out and "down_revision" in out


def test_a_non_literal_down_revision_fails_naming_the_file(tmp_path, capsys):
    t = _tree(tmp_path)
    _mig(t, "a.py", "a", None)
    (t / "b.py").write_text('PARENT = "a"\nrevision = "b"\ndown_revision = PARENT\n', encoding="utf-8")
    assert chk.main(["--root", str(tmp_path)]) == 1
    assert "b.py: cannot be read as a migration" in capsys.readouterr().out


def test_a_down_revision_of_the_wrong_type_fails_naming_the_file(tmp_path, capsys):
    t = _tree(tmp_path)
    _mig(t, "a.py", "a", None)
    _mig(t, "b.py", "b", 42)
    assert chk.main(["--root", str(tmp_path)]) == 1
    assert "b.py: cannot be read as a migration" in capsys.readouterr().out
