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

"""Truth table of the viewer scope (spec item 1) and the SQL conditions that mirror it."""

import itertools

import pytest

from codemie.repository.cli_analytics.scope import (
    ViewerScope,
    allowed,
    normalise_email,
    sql_detail_condition,
    sql_list_condition,
)

VIEWER = "Me@Example.com "
OWNERS = {"me": " me@example.COM", "other": "other@example.com", "none": None, "blank": "  "}
PROJECTS = {"shared": "proj", "elsewhere": "zzz", "unattributed": ""}
ROLES = {
    "super": {"unrestricted": True, "admin_projects": frozenset()},
    "admin": {"unrestricted": False, "admin_projects": frozenset({"proj"})},
    "ordinary": {"unrestricted": False, "admin_projects": frozenset()},
    # A regular user (EPMCDME-17657): no admin project, but their own shared sessions are readable.
    "regular": {"unrestricted": False, "admin_projects": frozenset(), "own_shared": True},
}


def _expected(mode, role, cls, owner, project, viewer):
    owned = (
        cls == "P"
        and viewer is not None
        and normalise_email(OWNERS[owner]) is not None
        and normalise_email(OWNERS[owner]) == normalise_email(viewer)
    )
    unrestricted = ROLES[role]["unrestricted"]
    is_owner = (
        viewer is not None
        and normalise_email(OWNERS[owner]) is not None
        and normalise_email(OWNERS[owner]) == normalise_email(viewer)
    )
    auth = (
        unrestricted
        or (PROJECTS[project] != "" and PROJECTS[project] in ROLES[role]["admin_projects"])
        or (ROLES[role].get("own_shared", False) and is_owner)
    )
    non_personal = cls in ("N", "M") and auth
    return {
        "exclude": cls == "N" and auth,
        "own_only": owned,
        "detail": owned or non_personal,
    }[mode]


CASES = list(
    itertools.product(["exclude", "own_only", "detail"], ROLES, ["P", "N", "M"], OWNERS, PROJECTS, [VIEWER, None])
)


@pytest.mark.parametrize("mode,role,cls,owner,project,viewer", CASES)
def test_truth_table(mode, role, cls, owner, project, viewer):
    scope = ViewerScope(viewer_email=viewer, mode=mode, **ROLES[role])
    assert allowed(scope, cls, OWNERS[owner], PROJECTS[project]) is _expected(mode, role, cls, owner, project, viewer)


def test_normalise_email():
    assert normalise_email("  A@B.c ") == "a@b.c"
    assert normalise_email("   ") is None
    assert normalise_email(None) is None
    assert normalise_email("\t A@B.c\n") == "a@b.c"  # the same ASCII whitespace set as the SQL btrim
    assert normalise_email("\u00a0a@b.c") == "\u00a0a@b.c"  # not in the set: left alone, as in SQL
    assert ViewerScope(" X@y.z", True, frozenset(), "detail").viewer_email == "x@y.z"
    assert ViewerScope("", True, frozenset(), "detail").viewer_email is None


@pytest.mark.parametrize("bad", [None, object(), "scope", {"unrestricted": True}])
def test_absent_or_malformed_scope_denies(bad):
    assert allowed(bad, "N", None, "proj") is False
    assert allowed(bad, "P", "a@b.c", "proj") is False
    assert sql_list_condition(bad) == ("false", {})
    assert sql_detail_condition(bad) == ("false", {})


def test_invalid_mode_or_class_denies():
    scope = ViewerScope("a@b.c", True, frozenset(), "bogus")  # type: ignore[arg-type]
    assert allowed(scope, "N", None, "p") is False
    assert sql_list_condition(scope) == ("false", {})
    ok = ViewerScope("a@b.c", True, frozenset(), "detail")
    assert allowed(ok, "X", None, "p") is False  # type: ignore[arg-type]


def test_sql_is_parameterised():
    scope = ViewerScope("evil'; drop table x;--@a.b", False, frozenset({"p'; --"}), "detail")
    for build in (sql_list_condition, sql_detail_condition):
        sql, params = build(scope)
        assert "evil" not in sql and "drop" not in sql and "p'" not in sql
        assert params["scope_viewer"] == "evil'; drop table x;--@a.b"
        assert params["scope_projects"] == ["p'; --"]


def test_sql_structure_per_mode():
    def sql(mode, build=sql_list_condition, **kw):
        base = {"viewer_email": "a@b.c", "unrestricted": False, "admin_projects": frozenset({"p"})}
        base.update(kw)
        return build(ViewerScope(mode=mode, **base))[0]

    excl, own, det = sql("exclude"), sql("own_only"), sql("detail")
    assert "NOT" in excl and "$scope_viewer" not in excl and "$scope_projects" in excl
    assert "$scope_viewer" in own and "$scope_projects" not in own
    assert "$scope_viewer" in det and "$scope_projects" in det
    assert "x.owner_email" in det and "x.subscription_type" in det and "x.project_name" in det
    d = sql("detail", sql_detail_condition)
    assert "d.owner_email" in d and "x." not in d
    # unrestricted: no project param, authNP is a constant true; no viewer: Personal never allowed
    assert "$scope_projects" not in sql("exclude", unrestricted=True)
    assert "$scope_viewer" not in sql("detail", viewer_email=None)
    assert sql("own_only", viewer_email=None) == "false"
    # nothing to match: no admin projects and not unrestricted
    assert sql("exclude", admin_projects=frozenset()) == "false"


def test_personal_detection_and_vocabulary_share_one_normalisation():
    """CR-001: is_p trims exactly like the response vocabulary, for both columns."""
    from codemie.repository.cli_analytics.scope import sql_norm, sql_project_origin, sql_subscription_type

    cond = sql_list_condition(ViewerScope("a@b.c", False, frozenset({"p"}), "exclude"))[0]
    for col in ("subscription_type", "project_origin"):
        assert f"{sql_norm(f'x.{col}')} = 'personal'" in cond
    assert sql_norm("x.subscription_type") in sql_subscription_type("x.subscription_type")
    assert sql_norm("x.project_origin") in sql_project_origin("x.project_origin")


@pytest.mark.parametrize(
    ("version", "consent", "expected"),
    [
        (None, None, {"personal"}),
        (None, "explicit-folder", {"personal"}),
        ("1", None, {"personal", "unknown"}),
        ("1", "explicit-folder", set()),
    ],
)
def test_private_classes_truth_table(version, consent, expected):
    from codemie.repository.cli_analytics.scope import private_classes

    assert private_classes(version, consent) == expected


def _private_scope(mode, sees_private, **kw):
    base = {"viewer_email": "aud@x.io", "unrestricted": True, "admin_projects": frozenset()}
    base.update(kw)
    return ViewerScope(mode=mode, sees_private=sees_private, **base)


@pytest.mark.parametrize("mode", ["own_only", "detail"])
def test_sees_private_reads_foreign_personal_rows(mode):
    assert allowed(_private_scope(mode, True), "P", "other@x.io", "") is True
    assert allowed(_private_scope(mode, False), "P", "other@x.io", "") is False


def test_sees_private_does_not_widen_exclude_or_non_personal_own_only():
    assert allowed(_private_scope("exclude", True), "P", "other@x.io", "p") is False
    assert allowed(_private_scope("own_only", True), "N", "other@x.io", "p") is False
    assert allowed(_private_scope("detail", True), "N", "other@x.io", "p") is True


@pytest.mark.parametrize("bad", [1, "yes", None])
def test_non_bool_sees_private_fails_closed(bad):
    scope = _private_scope("detail", bad)
    assert allowed(scope, "N", None, "p") is False
    assert sql_list_condition(scope) == ("false", {})


def test_sql_sees_private_conditions():
    from codemie.repository.cli_analytics.scope import sql_private

    own, params = sql_list_condition(_private_scope("own_only", True, viewer_email=None))
    assert own == sql_private("x") and params == {}
    det, _ = sql_list_condition(_private_scope("detail", True))
    assert det == f"({sql_private('x')} OR (NOT {sql_private('x')} AND true))"
    excl, _ = sql_list_condition(_private_scope("exclude", True))
    assert excl == sql_list_condition(_private_scope("exclude", False))[0]
    plain = sql_list_condition(_private_scope("own_only", False))
    assert "$scope_viewer" in plain[0]


def _shared_scope(mode, **kw):
    base = {"viewer_email": "me@x.io", "unrestricted": False, "admin_projects": frozenset(), "own_shared": True}
    base.update(kw)
    return ViewerScope(mode=mode, **base)


@pytest.mark.parametrize("mode", ["exclude", "detail"])
def test_own_shared_reads_only_the_viewers_own_shared_rows(mode):
    scope = _shared_scope(mode)
    assert allowed(scope, "N", "me@x.io", "any-project") is True
    assert allowed(scope, "N", "me@x.io", "") is True  # a session without a project is still the viewer's own
    assert allowed(scope, "N", "other@x.io", "any-project") is False
    assert allowed(scope, "N", None, "any-project") is False  # a missing owner never matches a viewer


def test_own_shared_never_reads_private_rows_of_others_or_widens_own_only():
    assert allowed(_shared_scope("detail"), "P", "other@x.io", "p") is False
    assert allowed(_shared_scope("exclude"), "P", "me@x.io", "p") is False  # exclude never lists Personal
    assert allowed(_shared_scope("own_only"), "N", "me@x.io", "p") is False


def test_own_shared_without_a_viewer_email_allows_nothing():
    scope = _shared_scope("exclude", viewer_email=None)
    assert allowed(scope, "N", "me@x.io", "p") is False
    assert sql_list_condition(scope) == ("false", {})


def test_own_shared_combines_with_admin_projects():
    scope = _shared_scope("exclude", admin_projects=frozenset({"p1"}))
    assert allowed(scope, "N", "other@x.io", "p1") is True
    assert allowed(scope, "N", "me@x.io", "p2") is True
    assert allowed(scope, "N", "other@x.io", "p2") is False


@pytest.mark.parametrize("bad", [1, "yes", None])
def test_non_bool_own_shared_fails_closed(bad):
    scope = _shared_scope("exclude", own_shared=bad)
    assert allowed(scope, "N", "me@x.io", "p") is False
    assert sql_list_condition(scope) == ("false", {})


def test_sql_own_shared_conditions():
    excl, params = sql_list_condition(_shared_scope("exclude"))
    assert "NOT" in excl and "x.owner_email" in excl and "$scope_viewer" in excl
    assert "$scope_projects" not in excl and params == {"scope_viewer": "me@x.io"}
    both, params = sql_list_condition(_shared_scope("exclude", admin_projects=frozenset({"p1"})))
    assert "$scope_projects" in both and "$scope_viewer" in both and params["scope_projects"] == ["p1"]
    own, _ = sql_list_condition(_shared_scope("own_only"))
    assert sql_list_condition(_shared_scope("own_only", own_shared=False))[0] == own  # own_only ignores the flag
    # without the flag a regular user selects nothing in exclude mode
    assert sql_list_condition(_shared_scope("exclude", own_shared=False)) == ("false", {})
