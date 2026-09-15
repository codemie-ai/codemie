# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Coverage for FileToolMixin._filter_requested_files — EPMCDME-12227 secondary locus."""

from codemie_tools.base.file_tool_mixin import FileToolMixin


class _Subject(FileToolMixin):
    """Minimal subclass — the mixin has no __init__ and no state we need."""


def _fake_files(*names):
    return {name: (b"content-of-" + name.encode(), "text/plain") for name in names}


def test_returns_all_when_no_file_key_in_params():
    """The 'no LLM intent' path is preserved — 12 subclasses rely on this defensive behavior."""
    subj = _Subject()
    result = subj._filter_requested_files(_fake_files("a.txt", "b.txt"), params_dict={})
    assert set(result.keys()) == {"a.txt", "b.txt"}


def test_returns_all_when_file_key_present_but_empty_list():
    """Empty ``files: []`` is treated as 'no names requested', same as key absent."""
    subj = _Subject()
    result = subj._filter_requested_files(_fake_files("a.txt", "b.txt"), params_dict={"files": []})
    assert set(result.keys()) == {"a.txt", "b.txt"}


def test_returns_only_matched_names_when_llm_specifies_matching_names():
    subj = _Subject()
    result = subj._filter_requested_files(_fake_files("a.txt", "b.txt"), params_dict={"files": ["a.txt"]})
    assert set(result.keys()) == {"a.txt"}


def test_returns_empty_when_llm_specifies_names_but_none_match():
    """EPMCDME-12227 secondary fix: no more silent fallback to all_files on mismatch.

    Previously the mixin returned every available file when the LLM's names did not match
    (e.g. wrong extension casing, URL-encoding drift). That fallback masked mismatches and
    contributed to attaching stale files to Jira. The tool should now receive an empty
    result and surface the mismatch instead of guessing.
    """
    subj = _Subject()
    result = subj._filter_requested_files(_fake_files("a.txt", "b.txt"), params_dict={"files": ["nonexistent.txt"]})
    assert result == {}, (
        "Expected empty result when requested names did not match any available file; "
        "silent fallback to all files masks mismatches and leaks unrelated files."
    )


def test_returns_partial_match_only_when_some_names_match():
    subj = _Subject()
    result = subj._filter_requested_files(
        _fake_files("a.txt", "b.txt"), params_dict={"files": ["a.txt", "missing.txt"]}
    )
    assert set(result.keys()) == {"a.txt"}


def test_empty_string_file_param_treated_as_no_names_requested():
    """CR-001: ``{"file": ""}`` must collapse into the no-names branch (return all files),
    not bypass the guard and match nothing (which would fall through the tool's attachment path)."""
    subj = _Subject()
    result = subj._filter_requested_files(_fake_files("a.txt", "b.txt"), params_dict={"file": ""})
    assert set(result.keys()) == {"a.txt", "b.txt"}


def test_null_file_param_treated_as_no_names_requested():
    """CR-001: ``{"file": null}`` (equivalent to Python None) must collapse into the no-names
    branch, not be coerced into a truthy [None] list that bypasses the guard."""
    subj = _Subject()
    result = subj._filter_requested_files(_fake_files("a.txt", "b.txt"), params_dict={"file": None})
    assert set(result.keys()) == {"a.txt", "b.txt"}


def test_files_list_of_only_falsy_values_treated_as_no_names_requested():
    """CR-001: ``{"files": [null, ""]}`` must collapse into no-names branch after falsy filter."""
    subj = _Subject()
    result = subj._filter_requested_files(_fake_files("a.txt", "b.txt"), params_dict={"files": [None, ""]})
    assert set(result.keys()) == {"a.txt", "b.txt"}
