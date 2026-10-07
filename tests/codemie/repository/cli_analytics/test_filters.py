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

from __future__ import annotations

from datetime import datetime

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter

START = datetime(2026, 9, 1)
END = datetime(2026, 9, 8)


def _flt(**kwargs) -> LocalAnalyticsFilter:
    return LocalAnalyticsFilter(start_dt=START, end_dt=END, **kwargs)


def test_deny_all_defaults_to_false():
    assert _flt().deny_all is False


def test_deny_all_counts_as_a_session_filter():
    # Fact queries restrict themselves to the filtered session set only when a session
    # filter is active; deny_all must take that path so it matches nothing.
    assert _flt(deny_all=True).has_session_filter is True


def test_no_filter_is_not_a_session_filter():
    assert _flt().has_session_filter is False


def test_params_lowercase_users_and_keep_the_other_filters():
    f = _flt(users=["Dev@Example.com"], projects=["p1"], repositories=["repo"], branch="main")

    assert f.params() == {
        "start_dt": START,
        "end_dt": END,
        "users": ["dev@example.com"],
        "projects": ["p1"],
        "repositories": ["repo"],
        "branch": "main",
    }


def test_params_never_carry_a_nul_character_for_deny_all():
    # PostgreSQL rejects NUL in text parameters; the old "\x00__no_project_access__"
    # sentinel made every endpoint fail there (design D5).
    params = _flt(deny_all=True).params()

    assert all("\x00" not in str(value) for value in params.values())
    assert "projects" not in params


def test_empty_values_are_normalised_to_none():
    f = _flt(users=[], projects=[], repositories=[], branch="")

    assert (f.users, f.projects, f.repositories, f.branch) == (None, None, None, None)


def test_params_leave_out_a_branch_that_is_not_set():
    assert "branch" not in _flt().params()


def test_project_unattributed_alone_is_a_session_filter():
    assert _flt(project_unattributed=True).has_session_filter is True


def test_project_unattributed_defaults_to_false():
    assert _flt().project_unattributed is False


def test_params_drop_projects_when_project_unattributed():
    params = _flt(projects=["p1"], project_unattributed=True).params()

    assert "projects" not in params
