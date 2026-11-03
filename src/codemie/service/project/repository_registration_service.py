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

"""Repository remote -> shared project registration (EPMCDME-16440).

Remotes are stored and looked up only in normalized form, so credentials never reach the table or logs.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.rest_api.models.repository_project_registration import RepositoryProjectRegistration

_DEFAULT_PORTS = {"ssh": 22, "https": 443, "http": 80}
_SCP = re.compile(r"^(?:[^@/\s]+@)?(?P<host>[^:/\s]+):(?P<path>[^/].*)$")
_INVALID = "Unparseable repository remote"


def normalize_remote(remote: str) -> str:
    """Collapse transport spellings of one repository to ``host[:port]/path``; raise ValueError if unparseable.

    The error never contains the input (it may carry credentials).
    """
    text = (remote or "").strip()
    try:
        host, port, path = _split(text)
    except ValueError:
        raise ValueError(_INVALID) from None
    return _join(host, port, path)


def _join(host: str, port: int | None, path: str) -> str:
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4].rstrip("/")
    if not host or not path:
        raise ValueError(_INVALID)
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    return f"{host}:{port}/{path}" if port else f"{host}/{path}"


def canonical_identity(text: str) -> str | None:
    """`text` when it is already a stored identity (what the API returns), else None. Such a string is read as
    scheme-less ``host[:port]/path`` and must rebuild to itself, so a URL, an scp form or any spelling with
    credentials, a query or a ``.git`` suffix is never one."""
    try:
        parts = urlsplit("//" + text)
        return text if _join(parts.hostname or "", parts.port, parts.path) == text else None
    except ValueError:
        return None


def registration_key(remote: str | None, key: str | None) -> str:
    """The stored key a request names: exactly one of a raw `remote` (normalized) or a `key` (the identity the API
    returned, taken exactly as is). Never guessed from which rows exist. ValueError (no input echoed) otherwise."""
    if (remote is None) == (key is None):
        raise ValueError(_INVALID)
    if remote is not None:
        return normalize_remote(remote)
    if (canonical := canonical_identity((key or "").strip())) is None:
        raise ValueError(_INVALID)
    return canonical


def _split(text: str) -> tuple[str, int | None, str]:
    if "://" in text:
        parts = urlsplit(text)
        port = parts.port
        if port == _DEFAULT_PORTS.get(parts.scheme.lower()):
            port = None
        return (parts.hostname or ""), port, parts.path
    match = _SCP.match(text)
    if not match:
        raise ValueError
    return match["host"].lower(), None, match["path"]


class RepositoryRegistrationRepository:
    @staticmethod
    def find_matches(session: Session, remote: str) -> list[RepositoryProjectRegistration]:
        """0, 1 or many registrations for a remote; an unparseable remote matches nothing."""
        try:
            key = normalize_remote(remote)
        except ValueError:
            return []
        stmt = select(RepositoryProjectRegistration).where(RepositoryProjectRegistration.normalized_remote == key)
        return list(session.exec(stmt).all())

    @staticmethod
    def list_for_project(session: Session, project_name: str) -> list[RepositoryProjectRegistration]:
        stmt = (
            select(RepositoryProjectRegistration)
            .where(RepositoryProjectRegistration.project_name == project_name)
            .order_by(RepositoryProjectRegistration.normalized_remote)
        )
        return list(session.exec(stmt).all())

    @staticmethod
    def lock_project(session: Session, project_name: str) -> Application | None:
        """The live project row, locked to the end of the caller's transaction (the project name is not a foreign
        key). Registration, rename and delete all take it, so none works from a stale read of the project: a
        registration either follows a project that still has this name or finds None and writes nothing."""
        stmt = select(Application).where(Application.name == project_name).with_for_update()
        project = session.exec(stmt).first()
        return None if project is None or project.deleted_at is not None else project

    @staticmethod
    def register(
        session: Session, project_name: str, user_id: str, *, remote: str | None = None, key: str | None = None
    ) -> RepositoryProjectRegistration:
        """Idempotent for the same project; 400 if unparseable; 404 if the project is gone or renamed meanwhile;
        409 if registered to another project. Commits (releasing the project lock)."""
        try:
            key = registration_key(remote, key)
        except ValueError:
            raise ExtendedHTTPException(code=400, message=_INVALID) from None
        project = RepositoryRegistrationRepository.lock_project(session, project_name)
        if project is None:
            session.rollback()
            raise ExtendedHTTPException(code=404, message="Project not found")
        if project.project_type == Application.ProjectType.PERSONAL:
            session.rollback()
            raise ExtendedHTTPException(code=400, message="Personal projects cannot have registered repositories")
        existing = session.get(RepositoryProjectRegistration, key)
        if existing is not None:
            session.rollback()
            return RepositoryRegistrationRepository._same_project_or_409(existing, project_name)
        row = RepositoryProjectRegistration(normalized_remote=key, project_name=project_name, created_by=user_id)
        try:
            session.add(row)
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = session.get(RepositoryProjectRegistration, key)
            if existing is None:
                raise
            return RepositoryRegistrationRepository._same_project_or_409(existing, project_name)
        return row

    @staticmethod
    def _same_project_or_409(
        existing: RepositoryProjectRegistration, project_name: str
    ) -> RepositoryProjectRegistration:
        if existing.project_name != project_name:
            raise ExtendedHTTPException(
                code=409,
                message="Repository already registered to another project",
                details="A repository can be registered to one project only.",
            )
        return existing

    @staticmethod
    def unregister(
        session: Session, project_name: str, *, remote: str | None = None, key: str | None = None
    ) -> str | None:
        """Delete exactly the project's registration that `remote` or `key` names; its key, or None when the project
        has none (or is gone). ValueError when the input names nothing parseable."""
        key = registration_key(remote, key)
        if RepositoryRegistrationRepository.lock_project(session, project_name) is None:
            session.rollback()
            return None
        row = session.get(RepositoryProjectRegistration, key)
        if row is None or row.project_name != project_name:
            session.rollback()
            return None
        session.delete(row)
        session.commit()
        return key

    @staticmethod
    def rename_project(session: Session, old_name: str, new_name: str) -> None:
        """Follow a project rename in the caller's transaction (not committed here)."""
        session.execute(
            update(RepositoryProjectRegistration)
            .where(RepositoryProjectRegistration.project_name == old_name)
            .values(project_name=new_name)
        )

    @staticmethod
    def delete_for_project(session: Session, project_name: str) -> None:
        """Drop a deleted project's registrations in the caller's transaction (not committed here)."""
        session.execute(
            delete(RepositoryProjectRegistration).where(RepositoryProjectRegistration.project_name == project_name)
        )
