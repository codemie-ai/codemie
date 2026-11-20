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

"""Routes for user model override management (admin-only endpoints).

Provides endpoints for setting, deleting and batch-managing user-level LLM model overrides within
projects. Every route is authorized with `_authorize_project_access`, like the other project write routes:
WRITE access to the project (its owner, a project admin or a platform admin), and 404 otherwise so the
existence of a project is not revealed.
"""

from __future__ import annotations

from contextlib import contextmanager
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError

from codemie.clients.postgres import get_session
from codemie.configs.customer_config import customer_config
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException, ModelNotWhitelistedException, NotFoundException
from codemie.core.models import Application
from codemie.rest_api.models.user_model_override import UserModelOverride
from codemie.rest_api.routers.projects._common import _authorize_project_access
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.llm.model_availability_service import USER_MODEL_OVERRIDE_FEATURE
from codemie.service.user.user_model_override_service import BatchValidationError, UserModelOverrideService

MAX_BATCH_USER_IDS = 500
MAX_ALLOWED_MODELS = 100
BATCH_OPERATIONS = ("grant", "revoke")

router = APIRouter()


def _require_override_feature() -> None:
    """Feature flag gate (customer-config userModelOverride) for the writes: 501 when the feature is switched off.

    Reading and deleting stay available with the flag off, so an admin can see and clear the rows that stay
    dormant (and would take effect again) until the flag is switched back on.
    """
    if not customer_config.is_feature_enabled(USER_MODEL_OVERRIDE_FEATURE):
        raise ExtendedHTTPException(code=501, message="User model overrides are not enabled on this platform")


def _validate_model_ids(models: list[str]) -> list[str]:
    """Reject blank ids and drop repeated ones, keeping the first occurrence of each."""
    if any(not m or not m.strip() for m in models):
        raise ValueError("model ids must not be blank")
    return list(dict.fromkeys(models))


# Response Models
class UserModelOverrideResponse(BaseModel):
    """Response model for UserModelOverride."""

    id: UUID
    user_id: str
    project_name: str
    allowed_models: list[str]
    default_model: str | None = None
    date: str | None = None
    update_date: str | None = None

    class Config:
        from_attributes = True


# Request Models
class SetUserModelOverrideRequest(BaseModel):
    """Request body for setting (replacing) a user's model override; the user comes from the path."""

    allowed_models: list[str] = Field(..., min_length=1, max_length=MAX_ALLOWED_MODELS)
    default_model: str | None = None

    @field_validator("allowed_models")
    @classmethod
    def _non_blank_models(cls, value: list[str]) -> list[str]:
        return _validate_model_ids(value)


class BatchUserModelOverrideRequest(BaseModel):
    """Request body for batch override operations (operation and user_ids are validated by the handler)."""

    operation: str
    user_ids: list[str]
    allowed_models: list[str] | None = Field(None, max_length=MAX_ALLOWED_MODELS)
    default_model: str | None = None

    @field_validator("allowed_models")
    @classmethod
    def _non_blank_models(cls, value: list[str] | None) -> list[str] | None:
        return _validate_model_ids(value) if value is not None else None


def _to_response(o: UserModelOverride) -> UserModelOverrideResponse:
    return UserModelOverrideResponse(
        id=o.id,
        user_id=o.user_id,
        project_name=o.project_name,
        allowed_models=o.allowed_models,
        default_model=o.default_model,
        date=o.date.isoformat() if o.date else None,
        update_date=o.update_date.isoformat() if o.update_date else None,
    )


@contextmanager
def _transaction():
    """One database session for the request: commit when the block finishes, roll back on any exception."""
    with get_session() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


_BATCH_FAILURES = (ModelNotWhitelistedException, ValueError, IntegrityError)
_MAPPED_ERRORS = (NotFoundException, *_BATCH_FAILURES)
_CONFLICT_MESSAGE = "Conflicting concurrent change to this override; retry"


def _to_http_error(exc: Exception) -> ExtendedHTTPException:
    """Map service-layer errors to HTTP errors (404 missing, 400 invalid, 409 concurrent change)."""
    if isinstance(exc, ExtendedHTTPException):
        return exc
    if isinstance(exc, NotFoundException):
        return ExtendedHTTPException(code=404, message=str(exc))
    if isinstance(exc, IntegrityError):
        logger.warning(f"user_model_override: integrity error: {exc}")
        return ExtendedHTTPException(code=409, message=_CONFLICT_MESSAGE)
    return ExtendedHTTPException(code=400, message=str(exc))


def _batch_failure_reason(exc: Exception) -> str:
    if isinstance(exc, BatchValidationError):
        return exc.reason
    if isinstance(exc, ExtendedHTTPException):
        return str(exc.details or exc.message)
    if isinstance(exc, IntegrityError):
        logger.warning(f"user_model_override: integrity error in batch: {exc}")
        return _CONFLICT_MESSAGE
    return str(exc)


@router.put(
    "/projects/{projectName}/user-model-overrides/{userId}",
    response_model=UserModelOverrideResponse,
    dependencies=[Depends(authenticate), Depends(_require_override_feature)],
)
def set_user_model_override(
    request: SetUserModelOverrideRequest,
    project_name: str = Path(alias="projectName"),
    user_id: str = Path(alias="userId"),
    user: User = Depends(authenticate),  # noqa: B008 - FastAPI dependency injection
    _authorized: Application = Depends(_authorize_project_access),  # noqa: B008 - FastAPI dependency injection
) -> UserModelOverrideResponse:
    """Set (replace) the model override of a user in a project (admin only); creates it when there is none."""

    try:
        with _transaction() as session:
            override = UserModelOverrideService.set_override(
                session,
                user_id,
                project_name,
                request.allowed_models,
                request.default_model,
                actor_id=user.id,
                actor_email=user.email,
            )
            response = _to_response(override)
    except _MAPPED_ERRORS as e:
        raise _to_http_error(e) from e
    return response


@router.get("/projects/{projectName}/user-model-overrides", response_model=list[UserModelOverrideResponse])
def list_user_model_overrides(
    project_name: str = Path(alias="projectName"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    _authorized: Application = Depends(_authorize_project_access),  # noqa: B008 - FastAPI dependency injection
) -> list[UserModelOverrideResponse]:
    """List all model overrides for a project (admin only)."""

    try:
        with _transaction() as session:
            overrides = UserModelOverrideService.get_overrides_by_project(session, project_name, skip, limit)
            response = [_to_response(o) for o in overrides]
    except NotFoundException as e:
        raise _to_http_error(e) from e
    return response


@router.post(
    "/projects/{projectName}/user-model-overrides/batch",
    response_model=list[UserModelOverrideResponse],
    dependencies=[Depends(authenticate), Depends(_require_override_feature)],
)
def batch_user_model_overrides(
    request: BatchUserModelOverrideRequest,
    project_name: str = Path(alias="projectName"),
    user: User = Depends(authenticate),  # noqa: B008 - FastAPI dependency injection
    _authorized: Application = Depends(_authorize_project_access),  # noqa: B008 - FastAPI dependency injection
) -> list[UserModelOverrideResponse]:
    """Batch grant/revoke model overrides (admin only, atomic). Revoke returns the deleted overrides."""

    if request.operation not in BATCH_OPERATIONS:
        raise ExtendedHTTPException(code=400, message=f"operation must be one of {list(BATCH_OPERATIONS)}")
    user_ids = list(dict.fromkeys(request.user_ids))  # dedupe, keep order
    if not user_ids:
        raise ExtendedHTTPException(code=400, message="user_ids must not be empty")
    if len(user_ids) > MAX_BATCH_USER_IDS:
        raise ExtendedHTTPException(code=400, message=f"user_ids is limited to {MAX_BATCH_USER_IDS} entries")
    if request.operation == "grant" and not request.allowed_models:
        raise ExtendedHTTPException(code=400, message="allowed_models required for grant operation")

    try:
        with _transaction() as session:
            overrides = UserModelOverrideService.batch_set_overrides(
                session,
                project_name,
                user_ids,
                request.operation,
                request.allowed_models,
                request.default_model,
                actor_id=user.id,
                actor_email=user.email,
            )
            response = [_to_response(o) for o in overrides]
    except NotFoundException as e:
        raise _to_http_error(e) from e
    except (BatchValidationError, *_BATCH_FAILURES) as e:
        failed_user_id = e.failed_user_id if isinstance(e, BatchValidationError) else None
        reason = _batch_failure_reason(e)
        raise ExtendedHTTPException(
            code=400,
            message="Batch operation failed; no users updated",
            details=f"{failed_user_id}: {reason}" if failed_user_id else reason,
        ) from e
    return response


@router.get("/projects/{projectName}/user-model-overrides/{userId}", response_model=UserModelOverrideResponse)
def get_user_model_override(
    project_name: str = Path(alias="projectName"),
    user_id: str = Path(alias="userId"),
    _authorized: Application = Depends(_authorize_project_access),  # noqa: B008 - FastAPI dependency injection
) -> UserModelOverrideResponse:
    """Fetch model override for a specific user (admin only)."""

    with _transaction() as session:
        override = UserModelOverrideService.get_override(session, user_id, project_name)
        if not override:
            raise ExtendedHTTPException(code=404, message="Override not found")
        return _to_response(override)


@router.delete("/projects/{projectName}/user-model-overrides/{userId}", status_code=204)
def delete_user_model_override(
    project_name: str = Path(alias="projectName"),
    user_id: str = Path(alias="userId"),
    user: User = Depends(authenticate),  # noqa: B008 - FastAPI dependency injection
    _authorized: Application = Depends(_authorize_project_access),  # noqa: B008 - FastAPI dependency injection
) -> None:
    """Delete model override for a user (admin only); 404 when the user has none."""

    with _transaction() as session:
        deleted = UserModelOverrideService.delete_override(
            session, user_id, project_name, actor_id=user.id, actor_email=user.email
        )
        if not deleted:
            raise ExtendedHTTPException(code=404, message="Override not found")
