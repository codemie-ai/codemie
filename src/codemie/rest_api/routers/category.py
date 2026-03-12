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

import yaml
from fastapi import APIRouter, Depends, Query, status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.category_repository import CategoryRepository
from codemie.rest_api.models.category import (
    Category,
    CategoryCreateRequest,
    CategoryListResponse,
    CategoryResponse,
    CategoryUpdateRequest,
)
from codemie.rest_api.security.authentication import admin_access_only, authenticate
from codemie.service.assistant.category_service import category_service

router = APIRouter(
    tags=["Category"],
    prefix="/v1",
    dependencies=[],
)


@router.get(
    "/assistants/categories",
    status_code=status.HTTP_200_OK,
    response_model=list[Category],
    response_model_by_alias=True,
)
def get_assistant_categories():
    """
    Returns all available assistant categories (legacy, non-paginated).

    This endpoint maintains backward compatibility with existing clients.
    For paginated access with assistant counts, use GET /assistants/categories/list.
    """
    try:
        return category_service.get_categories()
    except (FileNotFoundError, ValueError, yaml.YAMLError) as e:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Error loading categories",
            details=f"Failed to load assistant categories configuration: {str(e)}",
            help="Please contact support if this issue persists.",
        )


@router.get(
    "/assistants/categories/list",
    status_code=status.HTTP_200_OK,
    response_model=CategoryListResponse,
    dependencies=[Depends(authenticate), Depends(admin_access_only)],
)
def list_assistant_categories(
    page: int = Query(0, ge=0, description="Page number (0-indexed)"),
    per_page: int = Query(10, ge=1, le=100, description="Items per page"),
):
    """
    Get paginated list of categories with assistant counts.

    Returns counts separated by marketplace vs project assistants.
    This is the preferred endpoint for admin UI with pagination support.

    Args:
        page: Page number (0-indexed, default: 0)
        per_page: Number of items per page (1-100, default: 10)

    Returns:
        Paginated response with categories and metadata
    """
    result = CategoryRepository.query(page=page, per_page=per_page)
    return CategoryListResponse(**result)


@router.get(
    "/assistants/categories/{id}",
    status_code=status.HTTP_200_OK,
    response_model=CategoryResponse,
    dependencies=[Depends(authenticate), Depends(admin_access_only)],
)
def get_assistant_category(
    id: str,
):
    """
    Get a specific category by ID with assistant counts.

    Args:
        id: Category ID (humanized, e.g., "migration_modernization")

    Returns:
        Category details with assistant counts

    Raises:
        404: Category not found
    """
    category = Category.find_by_id(id)
    if not category:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Category not found",
            details=f"Category with ID '{id}' does not exist.",
            help=(
                "Check the category ID and try again. "
                "Use GET /v1/assistants/categories to list all available categories."
            ),
        )

    cat_dict = category.model_dump()
    stats = category_service.get_category_stats(id)
    cat_dict.update(stats)

    return CategoryResponse(**cat_dict)


@router.post(
    "/assistants/categories",
    status_code=status.HTTP_201_CREATED,
    response_model=CategoryResponse,
    dependencies=[Depends(authenticate), Depends(admin_access_only)],
)
def create_assistant_category(request: CategoryCreateRequest):
    """
    Create a new category. Admin access required.

    The category ID will be auto-generated from the name using humanization logic:
    - Special characters are removed
    - Spaces are replaced with underscores
    - All text is lowercased

    Example: "Migration & Modernization" → "migration_modernization"

    Args:
        request: Category creation request with name and description

    Returns:
        Created category with assistant counts (will be 0 for new categories)

    Raises:
        400: Invalid request data or duplicate category ID
        403: User is not an admin
    """
    try:
        category = category_service.create_category(name=request.name, description=request.description)

        cat_dict = category.model_dump()
        stats = category_service.get_category_stats(category.id)
        cat_dict.update(stats)

        return CategoryResponse(**cat_dict)

    except ValueError as e:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Cannot create category",
            details=str(e),
            help="The generated category ID already exists. Try using a different name.",
        )


@router.put(
    "/assistants/categories/{id}",
    status_code=status.HTTP_200_OK,
    response_model=CategoryResponse,
    dependencies=[Depends(authenticate), Depends(admin_access_only)],
)
def update_assistant_category(id: str, request: CategoryUpdateRequest):
    """
    Update an existing category. Admin access required.

    If assistants are assigned to this category, the operation will succeed
    but the response will include the count of affected assistants.
    The frontend should display a warning to the admin when editing categories
    with assigned assistants.

    Args:
        id: Category ID to update
        request: Category update request with new name and description

    Returns:
        Updated category with current assistant counts

    Raises:
        403: User is not an admin
        404: Category not found
    """
    # Check if assistants are assigned (for logging warning)
    stats = category_service.get_category_stats(id)

    category = category_service.update_category(category_id=id, name=request.name, description=request.description)

    cat_dict = category.model_dump()
    cat_dict.update(stats)  # Include counts in response for frontend warning

    return CategoryResponse(**cat_dict)


@router.delete(
    "/assistants/categories/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(authenticate), Depends(admin_access_only)],
)
def delete_assistant_category(id: str):
    """
    Delete a category. Admin access required.

    This operation will fail with a 409 Conflict error if any assistants
    (marketplace or project) are assigned to this category.
    The admin must first remove all assistants from the category before deletion.

    Args:
        id: Category ID to delete

    Returns:
        204 No Content on successful deletion

    Raises:
        403: User is not an admin
        404: Category not found
        409: Category has assigned assistants and cannot be deleted
    """
    category_service.delete_category(id)
    # Return None for 204 No Content (status code is already set in decorator)
    return None
