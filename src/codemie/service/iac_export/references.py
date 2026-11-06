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

"""Resolve entity ids to names for export, refusing names that are ambiguous in scope."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Literal

from codemie.service.iac_export.document import ExportError

ReferenceKind = Literal["assistant", "category", "integration"]


@dataclass(frozen=True)
class ExportScope:
    """Where names must be unique: a project (and, for skills, the author)."""

    project: str | None
    author: str | None = None
    user_id: str | None = None  # the requester: other users' personal integrations are not in their scope


@dataclass(frozen=True)
class NameEntry:
    id: str
    name: str


NameLookup = Callable[[str, ExportScope], list[NameEntry]]


def _id_columns(kind: str):
    """(id column, name column) of a kind's table."""
    if kind == "assistant":
        from codemie.rest_api.models.assistant import Assistant

        return Assistant.id, Assistant.name
    if kind == "integration":
        from codemie.rest_api.models.settings import Settings

        return Settings.id, Settings.alias
    if kind == "category":
        from codemie.rest_api.models.category import Category

        return Category.id, Category.name
    if kind == "skill":
        from codemie.rest_api.models.skill import Skill

        return Skill.id, Skill.name
    if kind == "datasource":
        from codemie.rest_api.models.index import IndexInfo

        return IndexInfo.id, IndexInfo.repo_name
    raise ExportError(f"Unsupported reference kind '{kind}'")


def _project_column(kind: str):
    """The project column of a kind's table; None for a kind with no project (category: global)."""
    if kind == "assistant":
        from codemie.rest_api.models.assistant import Assistant

        return Assistant.project
    if kind == "integration":
        from codemie.rest_api.models.settings import Settings

        return Settings.project_name
    if kind == "skill":
        from codemie.rest_api.models.skill import Skill

        return Skill.project
    if kind == "datasource":
        from codemie.rest_api.models.index import IndexInfo

        return IndexInfo.project_name
    return None


def _refuse_outside_target_project(kind: str, rows: list[tuple], scope: ExportScope) -> None:
    """The engine's own lookup on apply searches only the target project (`plan_apply/lookup.py`); a reference
    resolved from elsewhere would export with 200 and then fail there as unresolved (CR 26bf9077)."""
    if scope.project is None:
        return
    outside = sorted({str(i) for i, n, p in rows if n and p != scope.project})
    if outside:
        raise ExportError(
            f"Referenced {kind} {outside} is outside target project '{scope.project}'; apply cannot resolve it there"
        )


def db_ids_lookup(kind: str, ids: list[str], scope: ExportScope) -> list[NameEntry]:
    """Default lookup: the (id, name) of exactly the referenced ids, refusing one that lives outside the target
    project (the engine's own apply-time lookup only searches there). A kind with no project (category) is exempt.
    """
    from sqlmodel import select

    from codemie.clients.postgres import get_session

    if not ids:
        return []
    id_col, name_col = _id_columns(kind)
    project_col = _project_column(kind)
    with get_session() as session:
        if project_col is None:
            rows = session.exec(select(id_col, name_col).where(id_col.in_(ids))).all()
            return [NameEntry(str(i), n) for i, n in rows if n]
        rows = session.exec(select(id_col, name_col, project_col).where(id_col.in_(ids))).all()
    _refuse_outside_target_project(kind, rows, scope)
    return [NameEntry(str(i), n) for i, n, _project in rows if n]


IdsLookup = Callable[[str, list[str], ExportScope], list[NameEntry]]
# Entries of the scope that carry any of the given names (a targeted query, not a scan of the whole scope).
NamesLookup = Callable[[str, list[str], ExportScope], list[NameEntry]]
# Raises ExportError when the requesting user may not read the referenced entity.
AccessCheck = Callable[[str, str], None]


def _integration_scope(scope: ExportScope):
    from sqlalchemy import and_, or_

    from codemie.rest_api.models.settings import Settings, SettingType

    visible = Settings.setting_type == SettingType.PROJECT
    if scope.user_id is not None:
        visible = or_(visible, Settings.user_id == scope.user_id)
    return and_(Settings.project_name == scope.project, visible)


def _skill_scope(scope: ExportScope):
    """Skills of the project that are not another user's private ones (names are unique per author)."""
    from sqlalchemy import and_, or_, text

    from codemie.rest_api.models.skill import Skill, SkillVisibility

    visible = Skill.visibility != SkillVisibility.PRIVATE
    if scope.user_id is not None:
        visible = or_(visible, text("(skills.created_by->>'id') = :uid").bindparams(uid=scope.user_id))
    return and_(Skill.project == scope.project, visible)


def _assistant_scope(scope: ExportScope):
    """Assistants of the project that are shared, global (marketplace), or the requester's own; approximates
    Assistant.is_shared_with / is_owned_by for a peer-uniqueness query (CR 26bf9077, same defect as OC-3's
    `_integration_scope`: an unfiltered peer set let a 422 leak the name of an assistant the caller cannot read).
    """
    from sqlalchemy import and_, or_

    from codemie.rest_api.models.assistant import Assistant

    visible = or_(Assistant.shared.is_(True), Assistant.is_global.is_(True))
    if scope.user_id is not None:
        visible = or_(visible, Assistant.creator == scope.user_id)
    return and_(Assistant.project == scope.project, visible)


def _datasource_scope(scope: ExportScope):
    """Datasources of the project that are project-space-visible or the requester's own; approximates
    IndexInfo.is_shared_with / is_owned_by for a peer-uniqueness query (CR 26bf9077, same defect as OC-3)."""
    from sqlalchemy import and_, or_, text

    from codemie.rest_api.models.index import IndexInfo

    visible = IndexInfo.project_space_visible.is_(True)
    if scope.user_id is not None:
        visible = or_(visible, text("(index_info.created_by->>'id') = :uid").bindparams(uid=scope.user_id))
    return and_(IndexInfo.project_name == scope.project, visible)


def _scope_filter(kind: str, scope: ExportScope):
    """The column restricting a kind's table to the scope's project (None: the kind is global)."""
    if scope.project is None:
        return None
    if kind == "assistant":
        return _assistant_scope(scope)
    if kind == "integration":
        return _integration_scope(scope)
    if kind == "skill":
        return _skill_scope(scope)
    if kind == "datasource":
        return _datasource_scope(scope)
    return None


def db_names_lookup(kind: str, names: list[str], scope: ExportScope) -> list[NameEntry]:
    """Default lookup: every entity of the kind in scope whose name is one of `names`."""
    from sqlmodel import select

    from codemie.clients.postgres import get_session

    if not names:
        return []
    id_col, name_col = _id_columns(kind)
    query = select(id_col, name_col).where(name_col.in_(names))
    restriction = _scope_filter(kind, scope)
    if restriction is not None:
        query = query.where(restriction)
    with get_session() as session:
        rows = session.exec(query).all()
    return [NameEntry(str(i), n) for i, n in rows if n]


def _fail_unreadable(kind: str, id_: str) -> ExportError:
    return ExportError(f"Referenced {kind} '{id_}' is not accessible to the current user", 403)


def _check_assistant_readable(id_: str, user) -> None:
    from codemie.core.ability import Ability, Action
    from codemie.core.exceptions import ExtendedHTTPException
    from codemie.rest_api.routers.utils import get_assistant_by_id_or_raise

    try:
        assistant = get_assistant_by_id_or_raise(id_)
    except ExtendedHTTPException as e:
        raise ExportError(f"Referenced assistant '{id_}' not found") from e
    if not Ability(user).can(Action.READ, assistant):
        raise _fail_unreadable("assistant", id_)


def _check_integration_readable(id_: str, user) -> None:
    from codemie.core.exceptions import ExtendedHTTPException
    from codemie.service.iac_export.exporters.integration import check_usable, load_setting

    try:
        check_usable(load_setting(id_), user)
    except ExtendedHTTPException as e:
        if e.code == 404:
            raise ExportError(f"Referenced integration '{id_}' not found") from e
        raise _fail_unreadable("integration", id_) from e


def _check_skill_readable(id_: str, user) -> None:
    from codemie.core.ability import Action
    from codemie.core.exceptions import ExtendedHTTPException
    from codemie.service.skill_service import SkillService

    try:
        SkillService._raise_if_no_access(user, SkillService._get_skill_or_raise(id_), Action.READ)
    except ExtendedHTTPException as e:
        if e.code == 404:
            raise ExportError(f"Referenced skill '{id_}' not found") from e
        raise _fail_unreadable("skill", id_) from e


def _check_datasource_readable(id_: str, user) -> None:
    from codemie.core.ability import Ability, Action
    from codemie.rest_api.models.index import IndexInfo

    try:
        index = IndexInfo.get_by_id(id_)
    except KeyError as e:
        raise ExportError(f"Referenced datasource '{id_}' not found") from e
    if not Ability(user).can(Action.READ, index):
        raise _fail_unreadable("datasource", id_)


_READ_CHECKS = {
    "assistant": _check_assistant_readable,
    "integration": _check_integration_readable,
    "skill": _check_skill_readable,
    "datasource": _check_datasource_readable,
}


def reference_access(user) -> AccessCheck:
    """READ check on a referenced entity through the same ability / permission rules as its own read path.

    Categories are a global catalog without per-user access control, so they need no check.
    """

    def check(kind: str, id_: str) -> None:
        reader = _READ_CHECKS.get(kind)
        if reader is not None:
            reader(id_, user)

    return check


class NameResolver:
    """Resolves referenced ids to names; a name shared with ANY other entity of the kind in scope fails the export.

    ``lookup`` (a scope-wide listing, kept for callers and tests that supply a fixed list) takes precedence
    over ``ids_lookup`` / ``names_lookup``, which query exactly the referenced ids and the scope entries bearing
    their names. ``access``, when given, rejects references the requesting user may not read, before any name
    is looked up or disclosed.
    """

    def __init__(
        self,
        lookup: NameLookup | None = None,
        ids_lookup: IdsLookup = db_ids_lookup,
        names_lookup: NamesLookup = db_names_lookup,
        access: AccessCheck | None = None,
        user_id: str | None = None,
    ):
        self._user_id = user_id
        self._lookup = lookup
        self._ids_lookup = ids_lookup
        self._names_lookup = names_lookup
        self._access = access
        self._names: dict[tuple[str, str], str] = {}
        self._readable: set[tuple[str, str]] = set()
        self._scope_entries: dict[str, list[NameEntry]] = {}
        self._peers: dict[tuple[str, str], set[str]] = {}

    def _load(self, kind: str, missing: list[str], scope: ExportScope) -> None:
        if self._lookup:
            entries = self._scope_entries[kind] = self._lookup(kind, scope)
        else:
            entries = self._ids_lookup(kind, missing, scope)
        wanted = set(missing)
        self._names.update({(kind, e.id): e.name for e in entries if e.id in wanted})

    def _check_readable(self, kind: str, ids: list[str]) -> None:
        if self._access is None:
            return
        for id_ in ids:
            if (kind, id_) not in self._readable:
                self._access(kind, id_)
                self._readable.add((kind, id_))

    def _ids_named(self, kind: str, names: set[str], scope: ExportScope) -> dict[str, set[str]]:
        """name -> ids of every entity in scope bearing it."""
        if not self._lookup:
            return self._queried_peers(kind, names, scope)
        if kind not in self._scope_entries:
            self._scope_entries[kind] = self._lookup(kind, scope)
        found: dict[str, set[str]] = {n: set() for n in names}
        for e in self._scope_entries[kind]:
            if e.name in found:
                found[e.name].add(e.id)
        return found

    def _queried_peers(self, kind: str, names: set[str], scope: ExportScope) -> dict[str, set[str]]:
        unknown = sorted(n for n in names if (kind, n) not in self._peers)
        for n in unknown:
            self._peers[(kind, n)] = set()
        for e in self._names_lookup(kind, unknown, scope) if unknown else []:
            self._peers[(kind, e.name)].add(e.id)
        return {n: self._peers[(kind, n)] for n in names}

    def _ensure_unique(self, kind: str, result: dict[str, str], scope: ExportScope) -> None:
        named = self._ids_named(kind, set(result.values()), scope)
        for name in sorted(named):
            ids = named[name] | {i for i, n in result.items() if n == name}
            if len(ids) > 1:
                raise ExportError(f"{kind.capitalize()} name '{name}' is not unique in scope; cannot export by name")

    def resolve_many(self, kind: str, ids: Iterable[str], scope: ExportScope) -> dict[str, str]:
        unique = list(dict.fromkeys(ids))
        if scope.user_id is None and self._user_id is not None:
            scope = replace(scope, user_id=self._user_id)
        self._check_readable(kind, unique)
        missing = [i for i in unique if (kind, i) not in self._names]
        if missing:
            self._load(kind, missing, scope)
        result: dict[str, str] = {}
        for id_ in unique:
            name = self._names.get((kind, id_))
            if name is None:
                raise ExportError(f"Referenced {kind} '{id_}' not found")
            result[id_] = name
        if result:
            self._ensure_unique(kind, result, scope)
        return result

    def resolve(self, kind: str, id: str, scope: ExportScope) -> str:
        return self.resolve_many(kind, [id], scope)[id]

    def ensure_alias_unique(self, kind: str, aliases: Iterable[str], scope: ExportScope) -> None:
        """A name already embedded verbatim in the stored entity (not resolved from an id, so `resolve_many`
        never ran the ambiguity check on it) must still be unique among the kind's peers in scope; otherwise apply
        cannot tell which entity it names (CR 0bcb7377: a workflow's literal `integration_alias` bypassed id
        resolution entirely and so bypassed this check too)."""
        names = {a for a in aliases if a}
        if not names:
            return
        if scope.user_id is None and self._user_id is not None:
            scope = replace(scope, user_id=self._user_id)
        for name, ids in self._ids_named(kind, names, scope).items():
            if len(ids) > 1:
                raise ExportError(f"{kind.capitalize()} name '{name}' is not unique in scope; cannot export by name")


def resolve_name(kind: str, id: str, scope: ExportScope, lookup: NameLookup | None = None) -> str:
    return NameResolver(lookup).resolve(kind, id, scope)
