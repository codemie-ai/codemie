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

"""Hosted management API on the trusted management host only."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request

from fastapi.responses import JSONResponse, Response

from codemie.configs import config
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.identity import VerifiedIdentityContext
from codemie.hosted_apps_gateway.management import HostedAPIRoute
from codemie.rest_api.security.hosted_applications import (
    HostedActor,
    guard_mutation,
    hosted_actor,
    hosted_identity,
    verified_context,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.models.hosted_application_requests import (
    AppCreate,
    AppPatch,
    VersionCreate,
    UploadComplete,
    UploadReset,
    VersionConfiguration,
    VersionSelection,
    RequestModel,
    SecurityReason,
    ShareLinkOpenRequest,
    ShareUsersRequest,
)
from codemie.rest_api.security.user import User
from codemie.service.hosted_applications.runtime import HostedApplicationsRuntime


class LaunchRequest(RequestModel):
    purpose: Literal["owner_preview", "published_view"]
    version_id: UUID | None = None
    path: None = None


router = APIRouter(prefix="/v1", tags=["Hosted applications"], route_class=HostedAPIRoute)


def runtime(request: Request) -> HostedApplicationsRuntime:
    value = getattr(request.app.state, "hosted_applications", None)
    if value is None or not value.settings.enabled:
        raise HostedApplicationError("FEATURE_DISABLED", "Application hosting is unavailable", 403)
    return value


# Raw cover bodies; the service checks that each file's signature matches the declared type.
COVER_MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp")


def user_management(value: HostedApplicationsRuntime = Depends(runtime)) -> HostedApplicationsRuntime:
    """Named grants rest on the user directory; without user management the share routes are closed."""
    if not config.ENABLE_USER_MANAGEMENT:
        raise HostedApplicationError("USER_MANAGEMENT_DISABLED", "User management not enabled", 400)
    return value


def mutation(request: Request, value: HostedApplicationsRuntime = Depends(runtime)) -> HostedApplicationsRuntime:
    path = request.url.path
    if request.method == "PUT" and path.endswith("/cover"):
        mime = COVER_MEDIA_TYPES
    elif path.endswith("/artifact"):
        mime = "application/octet-stream"
    else:
        mime = "application/json"
    guard_mutation(request, value.settings, mime=mime)
    return value


def reply(value: Any, status: int = 200) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if isinstance(value, dict) and value.get("etag"):
        headers["ETag"] = value["etag"]
    return JSONResponse(value, status_code=status, headers=headers)


def unavailable_capabilities(reason: str) -> JSONResponse:
    return reply(
        {
            "create": False,
            "view": False,
            "disabled_reason": reason,
            "launch_policy": None,
            "limits": {},
        }
    )


@router.get("/hosted-applications/capabilities")
async def capabilities(request: Request, user: User = Depends(authenticate)) -> JSONResponse:
    """Every signed-in user gets an answer: accounts that cannot use the feature see it as unavailable."""
    value = getattr(request.app.state, "hosted_applications", None)
    if value is None or not value.settings.enabled:
        return unavailable_capabilities("Application hosting is unavailable")
    identity = verified_context(request, user)
    if identity is None:
        return unavailable_capabilities("Applications are not available for this account")
    try:
        await value.directory.require(identity)
    except HostedApplicationError as exc:
        if exc.status != 404:
            raise
        return unavailable_capabilities("Applications are not available for this account")
    # Opening a hosted transaction applies the logout cutoff and revoked sign-ins; no work runs inside it.
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(value.repository.transaction(user, identity))
    return reply(
        {
            "create": True,
            "view": True,
            "disabled_reason": None,
            "launch_policy": value.settings.launch_policy(),
            "limits": {
                "html_bytes": 20 * 1024**2,
                "zip_bytes": 100 * 1024**2,
                "expanded_bytes": 500 * 1024**2,
                "file_bytes": 50 * 1024**2,
                "files": 5000,
            },
        }
    )


@router.get("/hosted-applications/usage")
async def usage(
    identity: HostedActor = Depends(hosted_actor), value: HostedApplicationsRuntime = Depends(runtime)
) -> JSONResponse:
    await value.directory.require(identity)
    async with value.repository.transaction(*identity) as tx:
        state = await tx.read_quota(identity.principal_id)
        return reply(
            {
                "byte_limit": state.byte_limit,
                "available_bytes": max(0, state.byte_limit - state.stored_bytes - state.reserved_bytes),
                "stored_bytes": state.stored_bytes,
                "reserved_bytes": state.reserved_bytes,
            }
        )


@router.post("/hosted-applications/sessions/revoke")
async def revoke(
    identity: VerifiedIdentityContext = Depends(hosted_identity), value: HostedApplicationsRuntime = Depends(mutation)
) -> JSONResponse:
    """Ends the caller's hosted sessions; the UI calls it before it signs out."""
    await value.launch.logout(identity)
    return reply({"state": "revoked"})


@router.get("/hosted-applications")
async def catalog(
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(runtime),
    scope: str = Query("mine", description="mine, project, marketplace or shared"),
    q: str = Query("", max_length=200),
    artifact_kind: str | None = None,
    version_state: str | None = None,
    project: list[str] | None = Query(None, description="Project names (repeat the parameter for several)"),
    created_by: str | None = Query(None, max_length=200, description="Creator display name, as in skills"),
    shared: str | None = Query(None, description="mine scope only: private, people or project"),
    status: str | None = Query(None, description="mine scope only: draft, published or unpublished"),
    sort: str = "updated_desc",
    cursor: str | None = Query(None, max_length=4096),
    limit: int = Query(24, ge=1, le=100),
) -> JSONResponse:
    return reply(
        await value.catalog.list_apps(
            identity,
            scope=scope,
            q=q,
            artifact_kind=artifact_kind,
            version_state=version_state,
            project=project,
            created_by=created_by,
            shared=shared,
            status=status,
            sort=sort,
            cursor=cursor,
            limit=limit,
        )
    )


@router.get("/hosted-applications/users")
async def creators(
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(runtime),
    scope: str | None = None,
) -> JSONResponse:
    """Creators of the apps the caller may list (in ``scope``, or in any scope), for the "Created by" filter."""
    return reply(await value.catalog.list_creators(identity, scope=scope))


@router.post("/hosted-applications/share-links/open")
async def open_share_link(
    body: ShareLinkOpenRequest,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(user_management),
    _guard: HostedApplicationsRuntime = Depends(mutation),
) -> JSONResponse:
    """Exchange a share-link token for the app id; the token is never echoed back."""
    return reply(await value.catalog.open_share_link(identity, body.token))


@router.get("/hosted-applications/{app_id}/shared-with")
async def shared_with(
    app_id: UUID,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(user_management),
) -> JSONResponse:
    """Owner only: the users who were granted view access by name."""
    return reply(await value.catalog.shared_with(identity, app_id))


@router.get("/hosted-applications/{app_id}/sharable-users")
async def sharable_users(
    app_id: UUID,
    q: str = Query(..., min_length=2, max_length=200),
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(user_management),
) -> JSONResponse:
    """Owner only: directory users that can be granted access, matching ``q``."""
    return reply(await value.catalog.sharable_users(identity, app_id, q))


@router.post("/hosted-applications/{app_id}/share")
async def share(
    app_id: UUID,
    body: ShareUsersRequest,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(user_management),
    _guard: HostedApplicationsRuntime = Depends(mutation),
) -> JSONResponse:
    """Owner only: grant view-only access to the listed users."""
    return reply(await value.catalog.share(identity, app_id, body.user_ids))


@router.delete("/hosted-applications/{app_id}/share")
async def unshare(
    app_id: UUID,
    body: ShareUsersRequest,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(user_management),
    _guard: HostedApplicationsRuntime = Depends(mutation),
) -> JSONResponse:
    """Owner only: revoke the listed users' access."""
    return reply(await value.catalog.unshare(identity, app_id, body.user_ids))


@router.get("/hosted-applications/{app_id}/share-link")
async def get_share_link(
    app_id: UUID,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(user_management),
) -> JSONResponse:
    """Owner only: the app's current share link, or null."""
    return reply(await value.catalog.get_share_link(identity, app_id))


@router.post("/hosted-applications/{app_id}/share-link")
async def create_share_link(
    app_id: UUID,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(user_management),
    _guard: HostedApplicationsRuntime = Depends(mutation),
) -> JSONResponse:
    """Owner only: create (or return) the app's share link."""
    return reply(await value.catalog.create_share_link(identity, app_id))


@router.delete("/hosted-applications/{app_id}/share-link")
async def revoke_share_link(
    app_id: UUID,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(user_management),
    _guard: HostedApplicationsRuntime = Depends(mutation),
) -> JSONResponse:
    """Owner, or a platform admin or maintainer: revoke the app's share link and the access it gave."""
    return reply(await value.catalog.revoke_share_link(identity, app_id))


@router.post("/hosted-applications")
async def create(
    body: AppCreate,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(mutation),
    key: str = Header(..., alias="Idempotency-Key"),
) -> JSONResponse:
    return reply(await value.catalog.create_app(identity, body, key), 201)


@router.get("/hosted-applications/{app_id}")
async def detail(
    app_id: UUID, identity: HostedActor = Depends(hosted_actor), value: HostedApplicationsRuntime = Depends(runtime)
) -> JSONResponse:
    return reply(await value.catalog.get_app(identity, app_id))


@router.get("/hosted-applications/{app_id}/thumbnail")
async def thumbnail(
    app_id: UUID,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(runtime),
    if_none_match: str | None = Header(None, alias="If-None-Match", max_length=1024),
) -> Response:
    from codemie.core.hosted_applications.protocol import etag_matches
    from codemie.service.hosted_applications.thumbnails import HostedThumbnailService

    # Live authorization always runs first; only then may a matching validator skip the transfer.
    cover = await HostedThumbnailService(value.catalog).get(identity, app_id)
    headers = {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox; frame-ancestors 'none'",
        "Content-Disposition": 'inline; filename="application-preview"',
    }
    if cover is None:
        return Response(status_code=204, headers=headers)
    headers |= {"Cache-Control": "private, no-cache", "ETag": cover.etag}
    if etag_matches(if_none_match, cover.etag):
        return Response(status_code=304, headers=headers)
    return Response(cover.body, media_type=cover.mime, headers=headers)


@router.patch("/hosted-applications/{app_id}")
async def edit(
    app_id: UUID,
    body: AppPatch,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(mutation),
    match: str | None = Header(None, alias="If-Match"),
    key: str = Header(..., alias="Idempotency-Key"),
) -> JSONResponse:
    return reply(await value.catalog.patch_app(identity, app_id, body, match, key))


@router.put("/hosted-applications/{app_id}/cover")
async def upload_cover(
    request: Request,
    app_id: UUID,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(mutation),
    match: str | None = Header(None, alias="If-Match"),
    key: str = Header(..., alias="Idempotency-Key"),
) -> JSONResponse:
    """Raw PNG, JPEG or WebP body up to 1 MiB; answers with the updated application."""
    media_type = request.headers.get("content-type", "").lower()
    return reply(await value.catalog.put_cover(identity, app_id, media_type, match, key, request.stream()))


@router.delete("/hosted-applications/{app_id}/cover")
async def remove_cover(
    app_id: UUID,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(mutation),
    match: str | None = Header(None, alias="If-Match"),
    key: str = Header(..., alias="Idempotency-Key"),
) -> JSONResponse:
    return reply(await value.catalog.delete_cover(identity, app_id, match, key))


@router.post("/hosted-applications/{app_id}/launch")
async def launch(
    app_id: UUID,
    body: LaunchRequest,
    identity: VerifiedIdentityContext = Depends(hosted_identity),
    value: HostedApplicationsRuntime = Depends(mutation),
    user: User = Depends(authenticate),
) -> JSONResponse:
    return reply(
        await value.launch.issue(HostedActor(user, identity), app_id, purpose=body.purpose, version_id=body.version_id)
    )


@router.post("/hosted-applications/{app_id}/versions")
async def begin(
    app_id: UUID,
    body: VersionCreate,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(mutation),
    match: str | None = Header(None, alias="If-Match"),
    key: str = Header(..., alias="Idempotency-Key"),
) -> JSONResponse:
    return reply(await value.catalog.begin_version(identity, app_id, body, match, key), 201)


@router.get("/hosted-applications/{app_id}/versions/{version_id}")
async def version(
    app_id: UUID,
    version_id: UUID,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(runtime),
) -> JSONResponse:
    return reply(await value.catalog.get_version(identity, app_id, version_id))


@router.put("/hosted-applications/{app_id}/versions/{version_id}/artifact")
async def transfer(
    request: Request,
    app_id: UUID,
    version_id: UUID,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(mutation),
    match: str | None = Header(None, alias="If-Match"),
    generation: int = Header(..., alias="X-Upload-Generation", ge=1),
) -> JSONResponse:
    return reply(await value.catalog.transfer(identity, app_id, version_id, generation, match, request.stream()))


@router.post("/hosted-applications/{app_id}/versions/{version_id}/reset-upload")
async def reset(
    app_id: UUID,
    version_id: UUID,
    body: UploadReset,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(mutation),
    match: str | None = Header(None, alias="If-Match"),
    key: str = Header(..., alias="Idempotency-Key"),
) -> JSONResponse:
    return reply(await value.catalog.reset_upload(identity, app_id, version_id, body, match, key))


@router.post("/hosted-applications/{app_id}/versions/{version_id}/complete")
async def complete(
    app_id: UUID,
    version_id: UUID,
    body: UploadComplete,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(mutation),
    match: str | None = Header(None, alias="If-Match"),
    key: str = Header(..., alias="Idempotency-Key"),
) -> JSONResponse:
    return reply(await value.catalog.complete_version(identity, app_id, version_id, body, match, key), 202)


@router.post("/hosted-applications/{app_id}/select-version")
async def select_version(
    app_id: UUID,
    body: VersionSelection,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(mutation),
    match: str | None = Header(None, alias="If-Match"),
    key: str = Header(..., alias="Idempotency-Key"),
) -> JSONResponse:
    return reply(await value.catalog.select_version(identity, app_id, body, match, key))


@router.post("/hosted-applications/{app_id}/activate")
async def activate(
    app_id: UUID,
    body: VersionSelection,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(mutation),
    match: str | None = Header(None, alias="If-Match"),
    key: str = Header(..., alias="Idempotency-Key"),
) -> JSONResponse:
    return reply(await value.catalog.activate_version(identity, app_id, body, match, key))


@router.post("/hosted-applications/{app_id}/marketplace/publish")
async def publish(
    app_id: UUID,
    body: RequestModel,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(mutation),
    match: str | None = Header(None, alias="If-Match"),
    key: str = Header(..., alias="Idempotency-Key"),
) -> JSONResponse:
    return reply(await value.catalog.publish(identity, app_id, match, key))


@router.post("/hosted-applications/{app_id}/marketplace/unpublish")
async def unpublish(
    app_id: UUID,
    body: RequestModel,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(mutation),
    match: str | None = Header(None, alias="If-Match"),
    key: str = Header(..., alias="Idempotency-Key"),
) -> JSONResponse:
    return reply(await value.catalog.unpublish(identity, app_id, match, key))


@router.get("/hosted-applications/{app_id}/versions")
async def versions(
    app_id: UUID,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(runtime),
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=4096),
) -> JSONResponse:
    return reply(await value.catalog.list_versions(identity, app_id, limit=limit, cursor=cursor))


@router.get("/hosted-applications/{app_id}/versions/{version_id}/validation")
async def validation(
    app_id: UUID,
    version_id: UUID,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(runtime),
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(100, ge=1, le=100),
) -> JSONResponse:
    return reply(await value.catalog.validation_report(identity, app_id, version_id, offset=offset, limit=limit))


@router.put("/hosted-applications/{app_id}/versions/{version_id}/configuration")
async def configure(
    app_id: UUID,
    version_id: UUID,
    body: VersionConfiguration,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(mutation),
    match: str | None = Header(None, alias="If-Match"),
    key: str = Header(..., alias="Idempotency-Key"),
) -> JSONResponse:
    return reply(await value.catalog.configure_version(identity, app_id, version_id, body, match, key), 202)


@router.post("/hosted-applications/{app_id}/versions/{version_id}/cancel")
async def cancel(
    app_id: UUID,
    version_id: UUID,
    body: RequestModel,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(mutation),
    match: str | None = Header(None, alias="If-Match"),
    key: str = Header(..., alias="Idempotency-Key"),
) -> JSONResponse:
    return reply(await value.catalog.version_action(identity, app_id, version_id, "cancel", match, key), 202)


@router.post("/hosted-applications/{app_id}/versions/{version_id}/retry")
async def retry(
    app_id: UUID,
    version_id: UUID,
    body: RequestModel,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(mutation),
    match: str | None = Header(None, alias="If-Match"),
    key: str = Header(..., alias="Idempotency-Key"),
) -> JSONResponse:
    return reply(await value.catalog.version_action(identity, app_id, version_id, "retry", match, key), 202)


@router.delete("/hosted-applications/{app_id}")
async def delete_app(
    app_id: UUID,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(mutation),
    match: str | None = Header(None, alias="If-Match"),
    key: str = Header(..., alias="Idempotency-Key"),
) -> JSONResponse:
    return reply(await value.catalog.delete(identity, app_id, match, key), 202)


@router.post("/admin/hosted-applications/{app_id}/security/{action}")
async def app_security(
    app_id: UUID,
    action: Literal["suspend", "reinstate"],
    body: SecurityReason,
    identity: HostedActor = Depends(hosted_actor),
    value: HostedApplicationsRuntime = Depends(mutation),
    match: str | None = Header(None, alias="If-Match"),
    key: str = Header(..., alias="Idempotency-Key"),
) -> JSONResponse:
    """Platform administrators take an application down at once, or reinstate it (Suspend and Reinstate dialogs).

    The service checks administrator status against the ``users`` row, the source the access SQL reads.
    """
    return reply(
        await value.administration.security(
            identity,
            app_id,
            action,
            body.reason,
            match,
            key,
            version_id=body.version_id,
            delete_previous=body.delete_previous,
        )
    )


@router.get("/hosted-applications/{app_id}/deletion-status")
async def deletion_status(
    app_id: UUID, value: HostedApplicationsRuntime = Depends(runtime), identity: HostedActor = Depends(hosted_actor)
) -> JSONResponse:
    return reply(await value.catalog.deletion_status(identity, app_id))
