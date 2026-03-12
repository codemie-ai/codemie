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

from fastapi import APIRouter, status, Depends, Response

from codemie.configs import logger
from codemie.rest_api.models.permission import Permission, PermissionCreateRequest
from codemie.rest_api.security.user import User
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.routers.utils import raise_access_denied, raise_unprocessable_entity, raise_not_found
from codemie.service.permission import (
    PermissionCreationService,
    PermissionDeletionService,
    PermissionAccessDenied,
    PermissionResourceNotFound,
    PermissionPrincipalNotFound,
)

router = APIRouter(
    tags=["Resource Permissions"],
    prefix="/v1",
    dependencies=[],
)


@router.post(
    "/permissions",
    response_model=Permission,
    status_code=status.HTTP_201_CREATED,
    responses={
        "200": {"model": Permission},
        "201": {"model": Permission, "description": "Already Exists"},
    },
)
def create_permission(
    request: PermissionCreateRequest, response: Response, user: User = Depends(authenticate)
) -> Permission:
    """Create resource permission"""
    try:
        permission, status_code = PermissionCreationService.run(request, user)
        response.status_code = status_code

        return permission
    except PermissionResourceNotFound:
        raise_not_found(request.resource_id, request.resource_type.capitalize())
    except PermissionPrincipalNotFound:
        raise_not_found(request.principal_id, f"Principal of type {request.principal_type.capitalize()}")
    except PermissionAccessDenied:
        raise_access_denied("create permissions for")
    except Exception as e:
        logger.error(f"Error creating permission {str(e)}", exc_info=True)
        raise_unprocessable_entity(action="create", resource="permission", exc=e)


@router.delete("/permissions/{permission_id}")
def delete_permission(permission_id: str, user: User = Depends(authenticate)):
    try:
        PermissionDeletionService.run(
            permission_id=permission_id,
            user=user,
        )
    except PermissionResourceNotFound:
        raise_not_found(permission_id, "Permission")
    except PermissionAccessDenied:
        raise_access_denied("create permissions for")
    except Exception as e:
        logger.error(f"Error deleting permission {str(e)}", exc_info=True)
        raise_unprocessable_entity(action="delete", resource="permission", exc=e)
