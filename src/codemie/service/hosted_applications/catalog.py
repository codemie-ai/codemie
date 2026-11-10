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

"""Application metadata, scoped discovery, and lifecycle state transitions."""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Text, and_, bindparam, func, not_, or_, select, tuple_
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import aliased

from codemie.configs import config
from codemie.core.hosted_applications import access
from codemie.core.hosted_applications.access import is_eligible, manageable, platform_user, readable, suspend_refuses
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.policy import permits
from codemie.core.hosted_applications.protocol import canonical_json, check_etag, decode_cursor, encode_cursor
from codemie.core.models import CreatedByUser
from codemie.repository.hosted_applications.repository import HostedTransaction, as_uuid, is_lock_timeout, not_found
from codemie.rest_api.models.permission import Action, Permission, ResourceType, ShareLink
from codemie.rest_api.security.hosted_applications import HostedActor, account_not_supported
from codemie.rest_api.models.hosted_application import (
    HostedApplicationJobs as Job,
    HostedApplicationVersions as Version,
    HostedApplicationStorageAttempts as Attempt,
    HostedApplications as App,
)
from codemie.service.hosted_applications.covers import CoverServiceMixin
from codemie.service.hosted_applications.receiving import ReceivingServiceMixin
from codemie.service.hosted_applications.versions import VersionServiceMixin
from codemie.service.hosted_applications.publication import PublicationServiceMixin
from codemie.service.hosted_applications.uploads import UploadServiceMixin
from codemie.service.hosted_applications.projections import app_projection, can_manage
from codemie.service.hosted_applications import share_link_descriptor as link_descriptor
from codemie.service.permission import named_grant_service, share_link_service

# Applications one person may keep; deleted ones do not count.
MAX_APPLICATIONS_PER_OWNER = 500

# No moderation scope (S-28): platform admins suspend, reinstate and revoke links from the app's own page, so a
# suspended app outside their projects is reached by its URL only.
SCOPES = ("mine", "project", "marketplace", "shared")
# ``shared`` and ``status`` describe the caller's own apps, so only the ``mine`` scope takes them.
SHARING_FILTERS = ("private", "people", "project")
STATUS_FILTERS = ("draft", "published", "unpublished")
# A link open waits at most this long for a row lock (a revoke of a link with many recipients holds the app row).
# It must stay well below the hosted pool checkout timeout (runtime.DB_POOL_TIMEOUT_SECONDS, 2.0 s): opens waiting
# on one app row hold pool connections, and waiting as long as a checkout would let them starve every other app.
SHARE_LINK_OPEN_LOCK_TIMEOUT_MS = 400
MAX_PROJECT_FILTERS = 50
MAX_FILTER_TEXT = 200

NOT_OWNER = "Only the owner can share or revoke access"
NOT_LINK_REVOKER = "Only the owner or a platform administrator can revoke the share link"
UserPayload = dict[str, str | None]
SharedWith = dict[str, list[UserPayload]]
ShareLinkPayload = dict[str, dict[str, str | None] | None]


def require_project_access(user, project):
    """The caller may place an app in ``project``: an internal user with access to it (as for datasources)."""
    if user.user_type == config.EXTERNAL_USER_TYPE or not user.has_access_to_application(project):
        raise HostedApplicationError(
            "PROJECT_ACCESS_DENIED", f"You don't have permission for the project '{project}'", 403
        )


def list_scope(apps, scope, user):
    """The rows a list scope may return for ``user`` (a ``platform_user`` copy); every term is the access module's."""
    user_id = bindparam("viewer_id", user.id, type_=Text)
    allowed = readable(apps, user_id)  # a manager reads the app too, suspended or not
    active = apps.c.lifecycle_state == "active"
    if scope == "mine":
        return and_(active, apps.c.created_by["id"].as_string() == user.id, allowed)
    if scope == "project":
        if access.is_platform_admin(user):
            # Like skills: a platform admin lists every project's apps, private ones included.
            return and_(active, allowed)
        return and_(active, apps.c.project.in_(user.project_names), allowed)
    if scope == "shared":
        # Granted by name to the caller and made by someone else, whatever the app's project or ``is_global``.
        granted = named_grant_service.grant_exists_term(
            ResourceType.HOSTED_APPLICATION, apps.c.id, user_id, Action.READ
        )
        return and_(active, granted, apps.c.created_by["id"].as_string() != user.id, allowed)
    return and_(active, apps.c.is_global.is_(True), allowed)


def normalize_projects(project):
    """The ``project`` filter as a sorted list of distinct names, or ``None`` when it is malformed."""
    if project is None:
        return []
    if isinstance(project, str):
        project = [project]
    if not isinstance(project, (list, tuple)) or not all(isinstance(name, str) for name in project):
        return None
    names = sorted({name.strip() for name in project if name and name.strip()})
    if len(names) > MAX_PROJECT_FILTERS or any(len(name) > MAX_FILTER_TEXT for name in names):
        return None
    return names


def list_filters(apps, *, project=(), created_by=None, shared=None, status=None):
    """Terms the list adds on top of ``list_scope``; each only narrows the rows the scope already allows."""
    terms = []
    if project:
        terms.append(apps.c.project.in_(list(project)))
    if created_by:
        # The creator's display name, as the skills catalog filters (``created_by.name``).
        terms.append(apps.c.created_by["name"].as_string() == created_by)
    if shared:
        # Any live per-person grant, by name or through a share link: the named-grant rule with any grantee.
        granted = named_grant_service.grant_exists_term(
            ResourceType.HOSTED_APPLICATION, apps.c.id, Permission.principal_id, Action.READ
        )
        terms.append(
            {
                "private": and_(apps.c.shared.is_(False), apps.c.is_global.is_(False), not_(granted)),
                "people": granted,
                "project": apps.c.shared.is_(True),
            }[shared]
        )
    if status:
        terms.append(
            {
                # Draft and Published never overlap: an app published before any version is active is Published.
                "draft": and_(apps.c.active_version_id.is_(None), apps.c.is_global.is_(False)),
                "published": apps.c.is_global.is_(True),
                "unpublished": and_(apps.c.active_version_id.is_not(None), apps.c.is_global.is_(False)),
            }[status]
        )
    return terms


def creators_query(apps, user, scope=None):
    """Distinct creators of the apps ``user`` may list in ``scope`` (every scope when ``None``)."""
    visible = or_(*(list_scope(apps, name, user) for name in ((scope,) if scope else SCOPES)))
    creator_id = apps.c.created_by["id"].as_string().label("id")
    name = func.coalesce(apps.c.created_by["name"].as_string(), "").label("name")
    username = func.coalesce(apps.c.created_by["username"].as_string(), "").label("username")
    return (
        select(creator_id, username, name)
        .where(visible, apps.c.created_by["id"].as_string().is_not(None))
        .distinct()
        .order_by(name, username, creator_id)
    )


LIST_SORTS = ("updated_desc", "created_desc", "name_asc")
ARTIFACT_KINDS = (None, "html", "zip")
VERSION_STATES = (
    None,
    "uploading",
    "queued",
    "validating",
    "awaiting_configuration",
    "accepted",
    "failed",
    "rejected",
    "canceled",
)


def _valid_list_filters(
    *, scope, q, artifact_kind, version_state, projects, created_by, shared, status, sort, limit
) -> bool:
    """Whether the list filters are well formed; mine-only filters are refused on any other scope."""
    return not (
        projects is None
        or (created_by is not None and len(created_by) > MAX_FILTER_TEXT)
        or shared not in (None, *SHARING_FILTERS)
        or status not in (None, *STATUS_FILTERS)
        or ((shared is not None or status is not None) and scope != "mine")
        or scope not in SCOPES
        or sort not in LIST_SORTS
        or not 1 <= limit <= 100
        or len(q) > 200
        or artifact_kind not in ARTIFACT_KINDS
        or version_state not in VERSION_STATES
        or (version_state is not None and scope != "mine")
    )


def _filtered_list_query(query, *, scope, q, artifact_kind, version_state):
    """``query`` narrowed by the latest version's state, the artifact kind and the search text."""
    if version_state:
        # One top-1 probe of the (app_id, sequence DESC) index per candidate app.
        latest = aliased(Version)
        latest_state = (
            select(latest.validation_state)
            .where(latest.app_id == App.id)
            .order_by(latest.sequence.desc())
            .limit(1)
            .scalar_subquery()
        )
        query = query.where(latest_state == version_state)
    if artifact_kind:
        # Viewers filter on the active version's kind; a person's own list shows the working version.
        # ``App.artifact_kind`` follows the working version, so neither reads it for a viewer.
        version = App.selected_version_id if scope == "mine" else App.active_version_id
        kind = select(Version.artifact_kind).where(Version.app_id == App.id, Version.id == version).scalar_subquery()
        if scope == "mine":
            kind = func.coalesce(kind, App.artifact_kind)
        query = query.where(kind == artifact_kind)
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(App.search_text.like(pattern, escape="\\"))
    return query


def _after_cursor(column, ascending, cursor, cursor_scope, secret):
    """The keyset condition that starts a page after the position ``cursor`` encodes."""
    position = decode_cursor(cursor, cursor_scope, secret)
    try:
        value = position["value"] if ascending else datetime.fromisoformat(position["value"])
        if ascending:
            return tuple_(column, App.id) > tuple_(value, as_uuid(position["id"]))
        return tuple_(column, App.id) < tuple_(value, as_uuid(position["id"]))
    except (ValueError, KeyError, TypeError) as exc:
        raise HostedApplicationError("INVALID_CURSOR", "Invalid application page cursor") from exc


def _cursor_value(app, sort):
    """The sort key of ``app`` as a page cursor stores it."""
    if sort == "name_asc":
        return app.name
    if sort == "updated_desc":
        return app.updated_at.isoformat()
    return app.created_at.isoformat()


class HostedApplicationCatalog(
    UploadServiceMixin,
    ReceivingServiceMixin,
    PublicationServiceMixin,
    VersionServiceMixin,
    CoverServiceMixin,
):
    def __init__(self, repository, directory, monotonic=time.monotonic):
        self.repository, self.directory = repository, directory
        # Injectable so tests control the clock that stamps each writer lease on arrival.
        self.monotonic = monotonic

    async def _eligible_versions(self, tx, targets, *, activation=False, keyed_by_version=False):
        # ``activation`` is accepted for existing callers: one code policy permits preview and activation alike.
        targets = {
            (app.id, version_id): (app, version_id)
            for app, version_id in targets
            if version_id is not None and app.lifecycle_state == "active"
        }
        if not targets:
            return {}
        source, output = aliased(Attempt), aliased(Attempt)
        query = (
            select(Version, Job, source, output)
            .join(
                Job,
                and_(
                    Job.app_id == Version.app_id,
                    Job.version_id == Version.id,
                    Job.id == Version.accepted_job_id,
                ),
            )
            .join(
                source,
                and_(
                    source.app_id == Version.app_id,
                    source.version_id == Version.id,
                    source.id == Version.source_attempt_id,
                ),
            )
            .join(
                output,
                and_(
                    output.app_id == Version.app_id,
                    output.version_id == Version.id,
                    output.id == Version.accepted_attempt_id,
                ),
            )
            .join(App, and_(App.id == Version.app_id))
            .where(
                Version.id.in_([target[1] for target in targets.values()]),
                Version.validation_state == "accepted",
                Version.security_state == "clear",
                Version.upload_state == "completed",
                Job.kind == "validate",
                Job.state == "passed",
                Job.configuration_revision == Version.configuration_revision,
                Job.output_attempt_id == Version.accepted_attempt_id,
                Job.source_digest == Version.artifact_digest,
                Job.manifest_digest == Version.manifest_digest,
                source.kind == "upload",
                source.state == "stored",
                source.upload_generation == Version.upload_generation,
                source.receipt_digest == Version.artifact_digest,
                source.confirmed_bytes == Version.expected_bytes,
                output.kind == "validation",
                output.state == "stored",
                output.receipt_digest == Version.manifest_digest,
                output.confirmed_bytes > Version.expanded_bytes,
                output.provider == source.provider,
                output.namespace == source.namespace,
            )
        )
        result = {}
        for row in (await tx.session.execute(query)).all():
            version, job, _, output = row
            target = targets.get((version.app_id, version.id))
            if (
                target is None
                or version.id != target[1]
                or output.provider != self.storage.provider
                or not self.storage.owns_namespace(output.namespace)
                or not permits(job.policy_version, job.policy_definition_digest)
            ):
                continue
            configuration_digest = hashlib.sha256(canonical_json(version.requested_configuration)).digest()
            if (
                job.configuration_digest is None
                or not hmac.compare_digest(job.configuration_digest, configuration_digest)
                or version.manifest_key != output.prefix + "/manifest.json"
                or job.report_key != output.prefix + "/report.json"
            ):
                continue
            result[version.id if keyed_by_version else version.app_id] = version
        return result

    async def _eligible_version(self, tx, app, version_id, *, activation=False):
        return (await self._eligible_versions(tx, [(app, version_id)], activation=activation)).get(app.id)

    async def _platform_user(self, tx, user):
        """The caller's roles from ``users``/``user_projects``, read once per transaction."""
        cached = getattr(tx, "platform_user", None)
        if cached is None or cached.id != user.id:
            cached = tx.platform_user = await platform_user(tx.session, user)
        return cached

    async def _grants(self, tx, user):
        """The caller's named grants on hosted apps, read once per transaction like ``_platform_user``."""
        if getattr(tx, "grants_user_id", None) != user.id:
            tx.grants = await named_grant_service.apreload(tx.session, ResourceType.HOSTED_APPLICATION, user)
            tx.grants_user_id = user.id
        return tx.grants

    async def _project(self, tx, app, user):
        user = await self._platform_user(tx, user)
        grants = await self._grants(tx, user)
        target = app.selected_version_id if can_manage(app, user, grants) else app.active_version_id
        eligible = (
            tx.eligible_versions.get(app.id)
            if hasattr(tx, "eligible_versions")
            else await self._eligible_version(tx, app, target)
        )
        covered = tx.covered if hasattr(tx, "covered") else await self._covered(tx, [app])
        created_by = app.created_by
        return app_projection(
            app,
            user,
            grants=grants,
            publisher=(created_by.name or created_by.username) if created_by else None,
            artifact_kind=eligible.artifact_kind if eligible else None,
            has_cover=app.id in covered,
        )

    async def create_app(self, identity, request, key):
        user = identity.user
        async with self.repository.transaction(*identity, write=True) as tx:
            platform = await self._platform_user(tx, user)
            if not is_eligible(platform):
                raise account_not_supported()
            require_project_access(platform, request.project)
            # Locked before the app count and the idempotency lookup, so concurrent creates cannot overshoot.
            await tx.lock_quota(identity.principal_id)
            op, replay = await tx.operation("app.create", "catalog", request.model_dump(mode="json"), key)
            if replay is not None:
                await tx.managed_app(replay["id"], user)
                return replay
            created = await tx.session.scalar(
                select(func.count(App.id)).where(
                    App.created_by["id"].as_string() == identity.principal_id,
                    App.lifecycle_state == "active",
                )
            )
            if created >= MAX_APPLICATIONS_PER_OWNER:
                raise HostedApplicationError(
                    "APPLICATION_LIMIT",
                    f"You can keep up to {MAX_APPLICATIONS_PER_OWNER} applications; "
                    "delete applications you no longer need",
                    409,
                )
            app = App(
                id=uuid4(),
                project=request.project,
                created_by=CreatedByUser(id=user.id, username=user.username or "", name=user.name or ""),
                name=request.name,
                description=request.description,
                search_text=(request.name + "\n" + request.description).casefold(),
                shared=request.shared,
                is_global=False,
                security_state="clear",
                lifecycle_state="active",
                revision=1,
                access_revision=1,
                created_at=tx.now,
                updated_at=tx.now,
            )
            tx.session.add(app)
            await tx.session.flush()
            op.app_id = app.id
            return await tx.finish(op, await self._project(tx, app, user), 201)

    async def get_app(self, identity, app_id, *, states=("active",)):
        user_id = bindparam("viewer_id", identity.principal_id, type_=Text)
        async with self.repository.transaction(*identity) as tx:
            found = select(App).where(App.id == as_uuid(app_id), App.lifecycle_state.in_(states))
            # A manager opens the app as its manager; everyone else needs the viewer terms of ``readable``.
            app = (await tx.session.execute(found.where(manageable(App.__table__, user_id)))).scalar_one_or_none()
            if app is None:
                app = (await tx.session.execute(found.where(readable(App.__table__, user_id)))).scalar_one_or_none()
            if app is None:
                raise not_found()
            return await self._project(tx, app, identity.user)

    async def patch_app(self, identity, app_id, request, if_match, key):
        async with self.repository.transaction(*identity, write=True) as tx:
            app = await tx.managed_app(app_id, identity.user, lock=True)
            op, replay = await tx.operation(
                "app.update",
                app.id,
                {"changes": request.model_dump(mode="json", exclude_unset=True), "if_match": if_match},
                key,
                app_id=app.id,
            )
            if replay is not None:
                return replay
            check_etag(if_match, "app", app.id, app.revision)
            changes = request.model_fields_set
            moved = "project" in changes and request.project != app.project
            if moved:
                require_project_access(await self._platform_user(tx, identity.user), request.project)
                suspend_refuses(app, "move")
            if request.shared is True and not app.shared:
                suspend_refuses(app, "share_on")
            for field in changes:
                setattr(app, field, getattr(request, field))
            app.search_text = (app.name + "\n" + app.description).casefold()
            app.revision += 1
            app.updated_at = tx.now
            return await tx.finish(op, await self._project(tx, app, identity.user))

    async def list_apps(
        self,
        identity,
        *,
        scope="mine",
        q="",
        artifact_kind=None,
        version_state=None,
        project=None,
        created_by=None,
        shared=None,
        status=None,
        sort="updated_desc",
        cursor=None,
        limit=24,
    ):
        projects = normalize_projects(project)
        created_by = (created_by or "").strip() or None
        if not _valid_list_filters(
            scope=scope,
            q=q,
            artifact_kind=artifact_kind,
            version_state=version_state,
            projects=projects,
            created_by=created_by,
            shared=shared,
            status=status,
            sort=sort,
            limit=limit,
        ):
            raise HostedApplicationError("INVALID_FILTER", "Invalid application filters")
        q = q.strip().casefold()
        cursor_scope = {
            "actor": identity.principal_id,
            "scope": scope,
            "q": q,
            "artifact": artifact_kind,
            "version_state": version_state,
            "project": projects,
            "created_by": created_by,
            "shared": shared,
            "status": status,
            "sort": sort,
        }
        async with self.repository.transaction(*identity) as tx:
            user = await self._platform_user(tx, identity.user)
            filters = list_filters(
                App.__table__,
                project=projects,
                created_by=created_by,
                shared=shared,
                status=status,
            )
            query = select(App).where(list_scope(App.__table__, scope, user), *filters)
            query = _filtered_list_query(
                query, scope=scope, q=q, artifact_kind=artifact_kind, version_state=version_state
            )
            column = {"updated_desc": App.updated_at, "created_desc": App.created_at, "name_asc": App.name}[sort]
            ascending = sort == "name_asc"
            if cursor:
                query = query.where(_after_cursor(column, ascending, cursor, cursor_scope, self.repository.secret))
            query = (
                query.order_by(column.asc(), App.id.asc())
                if ascending
                else query.order_by(column.desc(), App.id.desc())
            )
            rows = (await tx.session.execute(query.limit(limit + 1))).scalars().all()
            selected = rows[:limit]
            next_cursor = None
            if len(rows) > limit:
                last = selected[-1]
                position = {"value": _cursor_value(last, sort), "id": str(last.id)}
                next_cursor = encode_cursor(cursor_scope, position, self.repository.secret)
            return {"items": await self._project_page(tx, user, selected), "next_cursor": next_cursor}

    async def list_creators(self, identity, *, scope=None):
        """Creators of the apps the caller may list (one scope, or all), for the "Created by" filter options."""
        if scope is not None and scope not in SCOPES:
            raise HostedApplicationError("INVALID_FILTER", "Invalid application filters")
        async with self.repository.transaction(*identity) as tx:
            user = await self._platform_user(tx, identity.user)
            rows = (await tx.session.execute(creators_query(App.__table__, user, scope))).all()
        return [{"id": row.id, "username": row.username, "name": row.name} for row in rows]

    async def _project_page(self, tx, user, apps):
        """Cards for one page: managers see their working version, viewers the active one."""
        user = await self._platform_user(tx, user)
        grants = await self._grants(tx, user)
        managed = {app.id for app in apps if can_manage(app, user, grants)}
        tx.eligible_versions = await self._eligible_versions(
            tx,
            [(app, app.selected_version_id if app.id in managed else app.active_version_id) for app in apps],
        )
        tx.covered = await self._covered(tx, apps)
        return [await self._project(tx, app, user) for app in apps]

    async def _owned_app(
        self, tx: HostedTransaction, identity: HostedActor, app_id: UUID | str, *, allow=None, refusal=NOT_OWNER
    ) -> App:
        """The locked app, for its owner only (or whom ``allow(app, platform user)`` admits): any other manager gets
        403 with ``refusal``, anyone who cannot read it 404."""
        try:
            app = await tx.managed_app(app_id, identity.user, lock=True)
        except HostedApplicationError as exc:
            if exc.status != 404:
                raise
            # Someone who can read the app (project viewer, grantee) learns it exists; anyone else gets the 404.
            user_id = bindparam("viewer_id", identity.principal_id, type_=Text)
            seen = select(App.id).where(
                App.id == as_uuid(app_id), App.lifecycle_state == "active", readable(App.__table__, user_id)
            )
            if (await tx.session.execute(seen)).first() is None:
                raise
            raise HostedApplicationError("ACCESS_DENIED", refusal, 403) from exc
        platform = await self._platform_user(tx, identity.user)
        created_by = app.created_by
        owner_id = created_by.id if created_by else None
        allowed = allow(app, platform) if allow else access.is_owned_by(owner_id, platform)
        if not allowed:
            raise HostedApplicationError("ACCESS_DENIED", refusal, 403)
        return app

    async def share(self, identity: HostedActor, app_id: UUID | str, user_ids: list[str]) -> SharedWith:
        """Grant view-only access to ``user_ids`` (owner only); returns ``{"shared_with": [...]}`` after the change.

        ``_owned_app`` gives the 403/404 split; the shared ``agrant`` checks the owner again on the locked row, then
        runs the descriptor's ``before_grant`` (refused while suspended).
        """
        async with self.repository.transaction(*identity, write=True) as tx:
            app = await self._owned_app(tx, identity, app_id)
            await named_grant_service.agrant(
                tx.session,
                resource_type=ResourceType.HOSTED_APPLICATION,
                resource=app,
                owner=await self._platform_user(tx, identity.user),
                project=app.project,
                user_ids=user_ids,
            )
            return await self._shared_with(tx, app)

    async def unshare(self, identity: HostedActor, app_id: UUID | str, user_ids: list[str]) -> SharedWith:
        """Revoke named grants (owner only, allowed while suspended); returns ``{"shared_with": [...]}``."""
        async with self.repository.transaction(*identity, write=True) as tx:
            app = await self._owned_app(tx, identity, app_id)
            await named_grant_service.arevoke(
                tx.session,
                resource_type=ResourceType.HOSTED_APPLICATION,
                resource=app,
                owner=await self._platform_user(tx, identity.user),
                project=app.project,
                user_ids=user_ids,
            )
            return await self._shared_with(tx, app)

    @staticmethod
    def _link_payload(link: ShareLink | None) -> ShareLinkPayload:
        if link is None:
            return {"link": None}
        return {"link": {"id": str(link.id), "token": link.token, "created_at": link.created_at.isoformat()}}

    async def get_share_link(self, identity: HostedActor, app_id: UUID | str) -> ShareLinkPayload:
        """The live share link (owner only): ``{"link": {id, token, created_at} | None}``."""
        async with self.repository.transaction(*identity, write=True) as tx:
            app = await self._owned_app(tx, identity, app_id)
            return self._link_payload(
                await share_link_service.aget_live(tx.session, ResourceType.HOSTED_APPLICATION, app.id)
            )

    async def create_share_link(self, identity: HostedActor, app_id: UUID | str) -> ShareLinkPayload:
        """Get or create the app's live link (owner only, refused while suspended by the link descriptor)."""
        async with self.repository.transaction(*identity, write=True) as tx:
            app = await self._owned_app(tx, identity, app_id)
            actor = await self._platform_user(tx, identity.user)
            link = await share_link_service.acreate(
                tx.session, ResourceType.HOSTED_APPLICATION, app, actor, app.project
            )
            return self._link_payload(link)

    async def revoke_share_link(self, identity: HostedActor, app_id: UUID | str) -> ShareLinkPayload:
        """Revoke the live link and the grants it made; no link is a no-op, allowed while suspended.

        The owner, or a platform admin or maintainer who reaches the app by id (S-17): an admin stops a leaked link
        without suspending the app, in the same single revoke step, audited with the admin as actor.
        """
        async with self.repository.transaction(*identity, write=True) as tx:
            app = await self._owned_app(
                tx, identity, app_id, allow=link_descriptor.may_revoke, refusal=NOT_LINK_REVOKER
            )
            actor = await self._platform_user(tx, identity.user)
            await share_link_service.arevoke_checked(
                tx.session, ResourceType.HOSTED_APPLICATION, app, actor, app.project
            )
            return {"link": None}

    async def open_share_link(self, identity: HostedActor, token: str) -> dict[str, str]:
        """Open a live share link: the caller becomes a view-only recipient; returns ``{"app_id"}``.

        Eligible internal callers only (403). Unknown, revoked, garbage, purging and suspended all answer the same
        404 ``SHARE_LINK_NOT_AVAILABLE``. Someone who can already read the app gets it with nothing written. No quota
        lock; the app is locked (``tx.lock_app``) and the link re-read before the grant, whose row the link's owner
        creates. The token is never logged or returned. Every lock wait is bounded by
        ``SHARE_LINK_OPEN_LOCK_TIMEOUT_MS`` (a revoke of a link with many recipients holds the app row), and an
        expired wait answers the retryable 503 ``SERVICE_BUSY``.
        """
        # The lock timeout is the transaction's first statement, before its own clock and sign-in reads (spec 6.3).
        async with self.repository.transaction(
            *identity, write=True, lock_timeout_ms=SHARE_LINK_OPEN_LOCK_TIMEOUT_MS
        ) as tx:
            opener = await self._platform_user(tx, identity.user)
            try:
                app_id = await share_link_service.aopen(
                    tx.session, ResourceType.HOSTED_APPLICATION, token, opener, context=tx
                )
            except DBAPIError as exc:
                if not is_lock_timeout(exc):
                    raise
                raise HostedApplicationError("SERVICE_BUSY", "Applications are busy; retry shortly", 503) from exc
            return {"app_id": str(app_id)}

    async def shared_with(self, identity: HostedActor, app_id: UUID | str) -> SharedWith:
        async with self.repository.transaction(*identity, write=True) as tx:
            return await self._shared_with(tx, await self._owned_app(tx, identity, app_id))

    async def sharable_users(self, identity: HostedActor, app_id: UUID | str, q: str) -> dict[str, list[UserPayload]]:
        """Users the owner may grant access to matching ``q``: ``{"users": [...]}``."""
        async with self.repository.transaction(*identity, write=True) as tx:
            await self._owned_app(tx, identity, app_id)
            return {
                "users": await named_grant_service.search_sharable_users(tx.session, ResourceType.HOSTED_APPLICATION, q)
            }

    @staticmethod
    async def _shared_with(tx: HostedTransaction, app: App) -> SharedWith:
        listed = await named_grant_service.alist_shared_with(tx.session, ResourceType.HOSTED_APPLICATION, app.id)
        return {"shared_with": listed}

    async def deletion_status(self, identity, app_id):
        async with self.repository.transaction(*identity) as tx:
            app = await tx.managed_app(app_id, identity.user, states=("purging", "purged"), action=Action.DELETE)
            remaining = (
                (
                    await tx.session.execute(
                        select(Attempt).where(
                            Attempt.app_id == app.id,
                            Attempt.state != "deleted",
                        )
                    )
                )
                .scalars()
                .all()
            )
            return {
                "state": app.lifecycle_state,
                "app_id": str(app.id),
                "remaining_prefixes": len(remaining),
                # A cover stays charged to the owner until the purge deletes it with the last rows.
                "charged_bytes": sum(item.confirmed_bytes + item.held_bytes for item in remaining)
                + await self._cover_size(tx, app),
                "etag": f'"app:{app.id}:{app.revision}"',
            }

    async def delete(self, identity, app_id: UUID | str, if_match: str | None, key: str) -> dict:
        async with self.repository.transaction(*identity, write=True) as tx:
            app = await tx.managed_app(
                app_id, identity.user, lock=True, states=("active", "purging", "purged"), action=Action.DELETE
            )
            op, replay = await tx.operation("app.delete", app.id, {"if_match": if_match}, key, app_id=app.id)
            if replay is not None:
                return replay
            if app.lifecycle_state != "active":
                raise not_found()
            check_etag(if_match, "app", app.id, app.revision)
            return await tx.finish(op, await self._begin_purge(tx, app, identity.user), 202)

    async def _begin_purge(self, tx, app, user):
        """Take an active application out of service and queue its physical cleanup, atomically with the caller."""
        app.lifecycle_state = "purging"
        app.deleted_at = tx.now
        # ``shared`` and ``is_global`` stay as they were: only PATCH and /marketplace/* change them, and the
        # lifecycle state and access revision already end every kind of access.
        app.selected_version_id, app.active_version_id = None, None
        app.revision += 1
        app.access_revision += 1
        app.updated_at = tx.now
        # ``user`` is the platform ``User`` (``identity.user``); the link step only reads its id, username and name.
        # Revoking removes the link's grants with it; every remaining grant goes next, each removal audited.
        await share_link_service.arevoke(tx.session, ResourceType.HOSTED_APPLICATION, app.id, user, app.project)
        await named_grant_service.aremove_all_grants(
            actor=user,
            resource_type=ResourceType.HOSTED_APPLICATION,
            resource_id=app.id,
            project=app.project,
            session=tx.session,
        )
        jobs = (
            (
                await tx.session.execute(
                    select(Job)
                    .where(
                        Job.app_id == app.id,
                        Job.state.in_(("ready", "running")),
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        for job in jobs:
            job.state, job.phase, job.completed_at = "canceled", "purge_requested", tx.now
            job.lease_token, job.revision = uuid4(), job.revision + 1
        attempts = (
            (
                await tx.session.execute(
                    select(Attempt).where(Attempt.app_id == app.id, Attempt.state != "deleted").with_for_update()
                )
            )
            .scalars()
            .all()
        )
        for attempt in attempts:
            attempt.state, attempt.writer_fence, attempt.updated_at = "delete_requested", uuid4(), tx.now
            attempt.revision += 1
        job = Job(
            id=uuid4(),
            app_id=app.id,
            kind="purge",
            state="ready",
            phase="awaiting_cleanup",
            safe_details={},
            available_at=tx.now,
            created_at=tx.now,
        )
        tx.session.add(job)
        return {"state": "purging", "job_id": str(job.id), "application": await self._project(tx, app, user)}
