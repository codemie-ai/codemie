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

import pytest
from langchain_core.tools import ToolException

from codemie_tools.base.generated_files import AttachmentCandidate, match_attachment


def _gen(name, data=b"x", path=None, checksum=None):
    return AttachmentCandidate("generated", name, lambda: data, path=path, checksum=checksum, size=len(data))


def _up(name, data=b"u"):
    return AttachmentCandidate("uploaded", name, lambda: data)


def test_match_exact_bare_name_returns_candidate():
    cand = _gen("report.json")
    assert match_attachment("report.json", [cand, _gen("other.json")]) is cand


def test_match_no_match_lists_available_names():
    with pytest.raises(ToolException) as exc:
        match_attachment("missing.json", [_gen("a.json"), _up("b.txt")])
    assert "a.json" in str(exc.value) and "b.txt" in str(exc.value)


def test_match_upload_and_generated_clash_lists_both():
    with pytest.raises(ToolException) as exc:
        match_attachment("r.json", [_up("r.json"), _gen("r.json")])
    msg = str(exc.value)
    assert "uploaded" in msg and "generated" in msg


@pytest.mark.parametrize("name", ["REPORT.JSON", "report", "port.json", "artifacts/t1/report.json"])
def test_match_case_and_partial_names_do_not_match(name):
    with pytest.raises(ToolException):
        match_attachment(name, [_gen("report.json")])


def _ws(path, data=b"w", checksum=None):
    return AttachmentCandidate(
        "generated", path.rsplit("/", 1)[-1], lambda: data, path=path, checksum=checksum, size=len(data)
    )


def test_match_exact_workspace_path():
    cand = _ws("reports/q1/out.json")
    assert match_attachment("reports/q1/out.json", [cand, _ws("other/x.json")]) is cand


def test_match_unique_bare_name_of_nested_file():
    cand = _ws("reports/q1/out.json")
    assert match_attachment("out.json", [cand, _ws("x.json")]) is cand


def test_match_exact_path_wins_over_bare_name_of_others():
    root = _ws("out.json")
    assert match_attachment("out.json", [_ws("reports/out.json"), root]) is root


def test_match_same_basename_in_different_folders_is_ambiguous_with_paths():
    with pytest.raises(ToolException) as exc:
        match_attachment("out.json", [_ws("a/out.json"), _ws("b/out.json")])
    msg = str(exc.value)
    assert "a/out.json" in msg and "b/out.json" in msg


def test_match_artifact_and_workspace_file_with_same_basename_is_ambiguous():
    art = _gen("r.json", path="artifacts/t1/r.json")
    with pytest.raises(ToolException) as exc:
        match_attachment("r.json", [art, _ws("reports/r.json")])
    assert "artifacts/t1/r.json" in str(exc.value) and "reports/r.json" in str(exc.value)


def test_match_artifact_exact_path_selects_that_file():
    old = _ws("artifacts/t1/r.json", checksum="a")
    new = _ws("artifacts/t5/r.json", checksum="b")
    assert match_attachment("artifacts/t1/r.json", [old, new]) is old


def test_match_same_name_in_two_artifact_turns_is_ambiguous_without_newest_wins():
    old = _ws("artifacts/t1/r.json", checksum="a")
    new = _ws("artifacts/t5/r.json", checksum="b")
    with pytest.raises(ToolException) as exc:
        match_attachment("r.json", [old, new])
    msg = str(exc.value)
    assert "artifacts/t1/r.json" in msg and "artifacts/t5/r.json" in msg and "turn" not in msg


def test_match_same_content_copies_are_one_file_exact_path_preferred():
    root, art = _ws("r.json", checksum="c"), _ws("artifacts/t1/r.json", checksum="c")
    assert match_attachment("r.json", [art, root]) is root
    assert match_attachment("artifacts/t1/r.json", [art, root]) is art


def test_match_same_content_copies_pick_shortest_path_deterministically():
    sub, art = _ws("reports/r.json", checksum="c"), _ws("artifacts/t1/r.json", checksum="c")
    assert match_attachment("r.json", [art, sub]) is sub
    assert match_attachment("r.json", [sub, art]) is sub


def test_match_same_name_same_checksum_but_different_size_is_not_merged():
    a = _ws("a/r.json", data=b"1", checksum="c")
    b = _ws("b/r.json", data=b"22", checksum="c")
    with pytest.raises(ToolException):
        match_attachment("r.json", [a, b])


def test_match_files_without_checksum_are_distinct_by_path():
    with pytest.raises(ToolException):
        match_attachment("r.json", [_ws("sub/r.json"), _ws("artifacts/t1/r.json")])


def test_match_not_found_lists_paths():
    with pytest.raises(ToolException) as exc:
        match_attachment("zzz", [_ws("reports/q1/out.json")])
    assert "reports/q1/out.json" in str(exc.value)


def _up(name, data=b"u"):
    return AttachmentCandidate("uploaded", name, lambda: data)


def test_ambiguity_message_shows_full_workspace_path_and_advises_retry_with_path():
    with pytest.raises(ToolException) as exc:
        match_attachment(
            "json-report.json",
            [
                AttachmentCandidate("uploaded", "json-report.json", lambda: b"u", path="uploads/json-report.json"),
                _ws("generated/json-report.json"),
            ],
        )
    msg = str(exc.value)
    assert "'uploads/json-report.json' (uploaded)" in msg
    assert "'generated/json-report.json' (generated in the workspace)" in msg
    assert "Retry with the full path of one of them" in msg


def test_ambiguity_message_with_identical_displayed_paths_says_rename():
    with pytest.raises(ToolException) as exc:
        match_attachment("json-report.json", [_up("json-report.json"), _ws("json-report.json")])
    msg = str(exc.value)
    assert "cannot disambiguate" in msg and "rename" in msg.lower()
    assert "Retry with the full path" not in msg


def test_ambiguity_message_with_two_uploads_of_same_name_says_rename():
    with pytest.raises(ToolException) as exc:
        match_attachment("a.json", [_up("a.json"), _up("a.json", b"2")])
    msg = str(exc.value)
    assert "cannot disambiguate" in msg
    assert "Retry with the full path" not in msg
