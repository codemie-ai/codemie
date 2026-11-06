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

from unittest.mock import MagicMock

import pytest

from codemie.service.iac_export.document import ExportError
from codemie.service.iac_export.references import ExportScope, NameEntry, NameResolver, resolve_name

SCOPE = ExportScope(project="proj", author="u1")


def _lookup(entries):
    return MagicMock(return_value=entries)


def test_unique_name_resolves():
    resolver = NameResolver(_lookup([NameEntry("a1", "Alpha"), NameEntry("a2", "Beta")]))

    assert resolver.resolve("assistant", "a1", SCOPE) == "Alpha"


def test_duplicate_name_raises_with_name_in_message():
    resolver = NameResolver(_lookup([NameEntry("a1", "Alpha"), NameEntry("a2", "Alpha")]))

    with pytest.raises(ExportError) as exc:
        resolver.resolve_many("assistant", ["a1", "a2"], SCOPE)

    assert "Alpha" in exc.value.message
    assert exc.value.status_code == 422


def test_resolve_many_uses_one_lookup_per_kind():
    lookup = _lookup([NameEntry("a1", "Alpha"), NameEntry("a2", "Beta")])
    resolver = NameResolver(lookup)

    result = resolver.resolve_many("assistant", ["a1", "a2"], SCOPE)

    assert result == {"a1": "Alpha", "a2": "Beta"}
    resolver.resolve("assistant", "a1", SCOPE)
    lookup.assert_called_once_with("assistant", SCOPE)


def test_unknown_id_raises():
    resolver = NameResolver(_lookup([NameEntry("a1", "Alpha")]))

    with pytest.raises(ExportError, match="missing"):
        resolver.resolve("skill", "missing", SCOPE)


def test_resolve_name_function_uses_given_lookup():
    lookup = _lookup([NameEntry("i1", "my-alias")])

    assert resolve_name("integration", "i1", SCOPE, lookup=lookup) == "my-alias"


# CR-008 (final review)
def test_name_shared_with_an_unreferenced_entity_in_scope_fails():
    resolver = NameResolver(_lookup([NameEntry("a1", "Alpha"), NameEntry("a2", "Alpha"), NameEntry("a3", "Beta")]))

    with pytest.raises(ExportError, match="Alpha") as exc:
        resolver.resolve("assistant", "a1", SCOPE)

    assert exc.value.status_code == 422


def test_default_path_queries_scope_by_the_resolved_names_only():
    seen = []

    def names_lookup(kind, names, scope):
        seen.append((kind, tuple(names), scope))
        return [NameEntry("a1", "Alpha"), NameEntry("a9", "Alpha")]

    resolver = NameResolver(ids_lookup=lambda k, ids, s: [NameEntry("a1", "Alpha")], names_lookup=names_lookup)

    with pytest.raises(ExportError, match="Alpha"):
        resolver.resolve("assistant", "a1", SCOPE)
    assert seen == [("assistant", ("Alpha",), SCOPE)]


def test_default_path_accepts_a_name_unique_in_scope_and_caches_the_query():
    calls = []

    def names_lookup(kind, names, scope):
        calls.append(tuple(names))
        return [NameEntry("a1", "Alpha")]

    resolver = NameResolver(ids_lookup=lambda k, ids, s: [NameEntry("a1", "Alpha")], names_lookup=names_lookup)

    assert resolver.resolve("assistant", "a1", SCOPE) == "Alpha"
    assert resolver.resolve("assistant", "a1", SCOPE) == "Alpha"
    assert calls == [("Alpha",)]


def test_db_names_lookup_filters_by_names_in_scope():
    from unittest.mock import patch

    from codemie.service.iac_export.references import db_names_lookup

    session = MagicMock()
    session.exec.return_value.all.return_value = [("i1", "N1"), ("i2", None)]
    with patch("codemie.clients.postgres.get_session") as gs:
        gs.return_value.__enter__.return_value = session
        for kind in ("assistant", "integration", "category"):
            assert db_names_lookup(kind, ["N1"], SCOPE) == [NameEntry("i1", "N1")]
        assert db_names_lookup("assistant", [], SCOPE) == []
    query = str(session.exec.call_args_list[0].args[0])
    assert "assistants.project" in query and "assistants.name IN" in query


# CR-009
def test_unreadable_reference_is_rejected_before_any_name_is_looked_up():
    lookup = _lookup([NameEntry("a1", "Alpha")])

    def access(kind, id_):
        raise ExportError(f"Referenced {kind} '{id_}' is not accessible to the current user", 403)

    resolver = NameResolver(lookup, access=access)

    with pytest.raises(ExportError) as exc:
        resolver.resolve("assistant", "a1", SCOPE)

    assert exc.value.status_code == 403
    assert "'a1'" in exc.value.message and "Alpha" not in exc.value.message
    lookup.assert_not_called()


def test_readable_references_are_checked_once_each():
    seen = []
    resolver = NameResolver(
        _lookup([NameEntry("a1", "Alpha"), NameEntry("a2", "Beta")]), access=lambda k, i: seen.append((k, i))
    )

    resolver.resolve_many("assistant", ["a1", "a2"], SCOPE)
    resolver.resolve("assistant", "a1", SCOPE)

    assert seen == [("assistant", "a1"), ("assistant", "a2")]


def test_reference_access_assistant_requires_read_ability():
    from unittest.mock import patch

    from codemie.service.iac_export.references import reference_access

    check = reference_access(MagicMock())
    with (
        patch("codemie.rest_api.routers.utils.get_assistant_by_id_or_raise", return_value=MagicMock()),
        patch("codemie.core.ability.Ability") as ability,
    ):
        ability.return_value.can.return_value = True
        check("assistant", "a1")
        ability.return_value.can.return_value = False
        with pytest.raises(ExportError) as exc:
            check("assistant", "a1")
    assert exc.value.status_code == 403


def test_reference_access_integration_maps_forbidden_and_missing():
    from unittest.mock import patch

    from codemie.core.exceptions import ExtendedHTTPException
    from codemie.service.iac_export.references import reference_access

    check = reference_access(MagicMock())
    module = "codemie.service.iac_export.exporters.integration"
    with patch(f"{module}.load_setting", return_value=MagicMock()):
        with patch(f"{module}.check_usable", side_effect=ExtendedHTTPException(code=403, message="no")):
            with pytest.raises(ExportError) as exc:
                check("integration", "i1")
        assert exc.value.status_code == 403
        with patch(f"{module}.check_usable"):
            check("integration", "i1")
    with patch(f"{module}.load_setting", side_effect=ExtendedHTTPException(code=404, message="gone")):
        with pytest.raises(ExportError, match="not found"):
            check("integration", "i1")


def test_reference_access_leaves_categories_unchecked():
    from codemie.service.iac_export.references import reference_access

    reference_access(MagicMock())("category", "c1")


# MR 4545 review: personal integrations of other users are outside the uniqueness scope
def test_integration_scope_filter_covers_project_integrations_and_the_requesters_own():
    from sqlmodel import select

    from codemie.rest_api.models.settings import Settings
    from codemie.service.iac_export.references import _scope_filter

    def sql(scope):
        return str(
            select(Settings.id)
            .where(_scope_filter("integration", scope))
            .compile(compile_kwargs={"literal_binds": True})
        )

    mine = sql(ExportScope(project="P", user_id="u1"))
    assert "setting_type" in mine and "'u1'" in mine and "'P'" in mine
    assert "user_id" not in sql(ExportScope(project="P"))


def test_resolver_passes_requester_into_scope():
    seen = []

    def names(kind, names, scope):
        seen.append(scope.user_id)
        return []

    resolver = NameResolver(
        ids_lookup=lambda kind, ids, scope: [NameEntry("i1", "jira")], names_lookup=names, user_id="u1"
    )
    assert resolver.resolve("integration", "i1", ExportScope(project="P")) == "jira"
    assert seen == ["u1"]


@pytest.mark.parametrize(
    "setting_type,is_global,member,allowed",
    [("project", False, True, True), ("project", False, False, False), ("project", True, False, True)],
)
def test_check_usable_lets_project_members_name_a_project_integration(setting_type, is_global, member, allowed):
    from unittest.mock import patch

    from codemie.core.exceptions import ExtendedHTTPException
    from codemie.rest_api.models.settings import SettingType
    from codemie.service.iac_export.exporters.integration import check_usable

    setting = MagicMock(setting_type=SettingType.PROJECT, is_global=is_global, project_name="P")
    user = MagicMock()
    user.has_access_to_application.return_value = member
    with patch(
        "codemie.service.iac_export.exporters.integration.SettingsService.is_budget_integration_hidden_from",
        return_value=False,
    ):
        if allowed:
            check_usable(setting, user)
        else:
            with pytest.raises(ExtendedHTTPException):
                check_usable(setting, user)


# Skills and datasources referenced by workflow agents (exported as names)
def test_skill_and_datasource_scope_filters_compile_for_the_requester():
    from sqlmodel import select

    from codemie.rest_api.models.index import IndexInfo
    from codemie.rest_api.models.skill import Skill
    from codemie.service.iac_export.references import _scope_filter

    def sql(model, kind, scope):
        return str(select(model.id).where(_scope_filter(kind, scope)).compile(compile_kwargs={"literal_binds": True}))

    skills = sql(Skill, "skill", ExportScope(project="P", user_id="u1"))
    assert "'P'" in skills and "'u1'" in skills and "visibility" in skills
    assert "u1" not in sql(Skill, "skill", ExportScope(project="P"))
    datasources = sql(IndexInfo, "datasource", ExportScope(project="P", user_id="u1"))
    assert "'P'" in datasources and "'u1'" in datasources and "project_space_visible" in datasources
    assert "u1" not in sql(IndexInfo, "datasource", ExportScope(project="P"))


# MR 4545 review (CR 26bf9077): an unfiltered assistant/datasource peer set let a 422 "not unique" leak the name
# of an entity the caller cannot read; same defect already fixed for integrations (OC-3, `_integration_scope`).
def test_assistant_scope_filter_covers_shared_global_and_the_requesters_own():
    from sqlmodel import select

    from codemie.rest_api.models.assistant import Assistant
    from codemie.service.iac_export.references import _scope_filter

    def sql(scope):
        return str(
            select(Assistant.id)
            .where(_scope_filter("assistant", scope))
            .compile(compile_kwargs={"literal_binds": True})
        )

    mine = sql(ExportScope(project="P", user_id="u1"))
    assert "'P'" in mine and "shared" in mine and "is_global" in mine and "'u1'" in mine
    assert "u1" not in sql(ExportScope(project="P"))


def test_skill_and_datasource_names_resolve_and_clashes_fail():
    resolver = NameResolver(lambda kind, scope: [NameEntry("s1", "Triage"), NameEntry("s2", "Triage")])
    with pytest.raises(ExportError, match="Triage"):
        resolver.resolve("skill", "s1", ExportScope(project="P"))
    ok = NameResolver(lambda kind, scope: [NameEntry("d1", "Docs")])
    assert ok.resolve("datasource", "d1", ExportScope(project="P")) == "Docs"


@pytest.mark.parametrize("kind", ["skill", "datasource"])
def test_reference_access_maps_unreadable_and_missing_skills_and_datasources(kind):
    from unittest.mock import patch

    from codemie.core.exceptions import ExtendedHTTPException
    from codemie.service.iac_export.references import reference_access

    check = reference_access(MagicMock())
    target = (
        (
            "codemie.service.skill_service.SkillService._get_skill_or_raise",
            "codemie.service.skill_service.SkillService._raise_if_no_access",
        )
        if kind == "skill"
        else ("codemie.rest_api.models.index.IndexInfo.get_by_id", "codemie.core.ability.Ability.can")
    )
    if kind == "skill":
        with patch(target[0], side_effect=ExtendedHTTPException(code=404, message="gone")):
            with pytest.raises(ExportError, match="not found"):
                check(kind, "x")
        with (
            patch(target[0], return_value=MagicMock()),
            patch(target[1], side_effect=ExtendedHTTPException(code=403, message="no")),
        ):
            with pytest.raises(ExportError) as exc:
                check(kind, "x")
        assert exc.value.status_code == 403
    else:
        with patch(target[0], side_effect=KeyError("x")):
            with pytest.raises(ExportError, match="not found"):
                check(kind, "x")
        with patch(target[0], return_value=MagicMock()), patch("codemie.core.ability.Ability") as ability:
            ability.return_value.can.return_value = False
            with pytest.raises(ExportError) as exc:
                check(kind, "x")
        assert exc.value.status_code == 403
