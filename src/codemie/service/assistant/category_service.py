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

"""
Service for managing assistant categories with database backend.
"""

from __future__ import annotations

from fastapi import status
from psycopg2.errors import UniqueViolation
from sqlalchemy import cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, case, func, select

from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException, ValidationException
from codemie.rest_api.models.category import Category

# ============================================================================
# Constants
# ============================================================================

_UNIQUE_CONSTRAINT = UniqueViolation
_MAX_NAMES = 10  # names listed per entity type in the blocked-delete error

# ============================================================================
# Database-Based Implementation
# ============================================================================


class DatabaseCategoryService:
    """Service for managing assistant categories using PostgreSQL database."""

    def _get_categories_by_ids(self, category_ids: list[str]) -> list[Category]:
        """
        Internal helper to query categories by specific IDs using optimized IN clause.

        Args:
            category_ids: List of category IDs to query

        Returns:
            List of Category objects matching the provided IDs
        """
        if not category_ids:
            return []

        with Session(Category.get_engine()) as session:
            query = select(Category).where(Category.id.in_(category_ids))
            return list(session.exec(query).all())

    def get_categories(self) -> list[Category]:
        """
        Load all categories from database.

        Returns:
            List of Category objects sorted by name
        """
        with Session(Category.get_engine()) as session:
            categories = session.exec(select(Category).order_by(Category.name)).all()
            return list(categories)

    def validate_category_ids(self, category_ids: list[str], required: bool = False) -> list[str]:
        """
        Validate that all provided category IDs exist using optimized IN query.

        Args:
            category_ids: List of category IDs to validate
            required: If True, raises ValidationException when empty list provided

        Returns:
            List of valid category IDs

        Raises:
            ValidationException: If required=True and no categories provided, or any category ID is invalid
            ValueError: If required=False and any category ID is invalid (backward compatibility)
        """
        if not category_ids:
            if required:
                raise ValidationException("At least one category is required")
            return category_ids

        # Query only the specific categories we need and extract IDs
        categories = self._get_categories_by_ids(category_ids)
        valid_ids = {cat.id for cat in categories}
        invalid_ids = list(set(category_ids) - valid_ids)

        if invalid_ids:
            if required:
                raise ValidationException(f"Invalid category IDs: {invalid_ids}")
            else:
                raise ValueError(f"Invalid category IDs: {invalid_ids}")

        return list(dict.fromkeys(category_ids))

    def filter_valid_category_ids(self, category_ids: list[str]) -> list[str]:
        """
        Filter out invalid category IDs using optimized IN query, fail-safe to [] on error.

        Args:
            category_ids: List of category IDs to filter

        Returns:
            List of valid category IDs only
        """
        if not category_ids:
            return []

        try:
            # Query only the specific categories we need and extract IDs
            categories = self._get_categories_by_ids(category_ids)
            valid_ids = {cat.id for cat in categories}

            # Return IDs in the original order, filtering out invalid ones
            return [cat_id for cat_id in category_ids if cat_id in valid_ids]
        except Exception as e:
            logger.warning(f"Error filtering category IDs: {e}")
            return []

    def enrich_categories(self, category_ids: list[str]) -> list[Category]:
        """
        Enrich category IDs with full category information using optimized IN query.

        Args:
            category_ids: List of category ID strings

        Returns:
            List of Category objects. Categories that are not found
            are skipped (graceful degradation). Order is preserved based on input.
        """
        if not category_ids:
            return []

        try:
            # Query only the specific categories we need
            categories = self._get_categories_by_ids(category_ids)

            # Create a dict for fast lookup and preserve input order
            categories_dict = {cat.id: cat for cat in categories}
            return [categories_dict[cat_id] for cat_id in category_ids if cat_id in categories_dict]
        except Exception as e:
            logger.warning(f"Error enriching categories: {e}")
            return []

    def create_category(self, name: str, description: str | None = None) -> Category:
        """
        Create new category with UUID.

        Args:
            name: Display name of the category
            description: Optional description

        Returns:
            Created Category object

        Raises:
            ExtendedHTTPException: 409 if category with same name already exists
        """
        try:
            category = Category(name=name, description=description)
            category.save()
            logger.info(f"Created category: {category.id} ({name})")
            return category
        except IntegrityError as e:
            error_message = str(e)

            # Handle duplicate name constraint
            if isinstance(e.orig, _UNIQUE_CONSTRAINT):
                logger.error(f"Failed to create category: duplicate name '{name}'")
                raise ExtendedHTTPException(
                    code=status.HTTP_409_CONFLICT,
                    message="Category already exists",
                    details=f"A category with name '{name}' already exists",
                )

            # Re-raise other integrity errors
            logger.error(f"Database integrity error creating category '{name}': {error_message}")
            raise

    def update_category(self, category_id: str, name: str, description: str | None = None) -> Category:
        """
        Update existing category.

        Args:
            category_id: ID of category to update
            name: New display name
            description: New description

        Returns:
            Updated Category object

        Raises:
            ExtendedHTTPException: 404 if category doesn't exist, 409 if name already exists
        """
        category = Category.find_by_id(category_id)
        if not category:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message="Category not found",
                details=f"Category with ID '{category_id}' not found",
            )

        try:
            category.name = name
            category.description = description
            category.update()
            logger.info(f"Updated category: {category_id}")
            return category

        except IntegrityError as e:
            error_message = str(e)

            # Handle duplicate name constraint
            if isinstance(e.orig, _UNIQUE_CONSTRAINT):
                logger.error(f"Failed to update category {category_id}: duplicate name '{name}'")
                raise ExtendedHTTPException(
                    code=status.HTTP_409_CONFLICT,
                    message="Category name already exists",
                    details=f"A category with name '{name}' already exists",
                )

            # Re-raise other integrity errors
            logger.error(f"Database integrity error updating category {category_id}: {error_message}")
            raise

    def delete_category(self, category_id: str) -> None:
        """
        Delete category if no assistants or workflows are assigned.

        Uses a single atomic transaction with row-level locking on both the category
        and all assistants using it to prevent race conditions where:
        1. A category could be assigned between check and deletion
        2. An assistant could be created/updated with this category during deletion

        Args:
            category_id: ID of category to delete

        Raises:
            ExtendedHTTPException: 409 if category has assigned assistants, 404 if category doesn't exist
        """
        # Import here to avoid circular dependency
        from codemie.core.workflow_models import WorkflowConfig
        from codemie.rest_api.models.assistant import Assistant

        # Start transaction - all operations must complete atomically
        with Session(Category.get_engine()) as session:
            category_query = select(Category).where(Category.id == category_id)
            category = session.exec(category_query).first()

            if not category:
                raise ExtendedHTTPException(
                    code=status.HTTP_404_NOT_FOUND,
                    message="Category not found",
                    details=f"Category with ID '{category_id}' not found",
                )

            usages = []
            for label, model in (("assistants", Assistant), ("workflows", WorkflowConfig)):
                used = cast(model.categories, JSONB).contains([category_id])
                count = session.exec(select(func.count()).where(used)).one()
                if count > 0:
                    names = session.exec(select(model.name).where(used).order_by(model.name).limit(_MAX_NAMES)).all()
                    usages.append(f"{count} assigned {label} ({', '.join(names)})")

            if usages:
                raise ExtendedHTTPException(
                    code=status.HTTP_409_CONFLICT,
                    message="Cannot delete category",
                    details=f"Cannot delete category with {' and '.join(usages)}",
                )

            # 3. Delete the category (within same transaction, with all relevant rows locked)
            session.delete(category)
            session.commit()
            logger.info(f"Deleted category: {category_id}")

    def get_category_stats(self, category_id: str) -> dict[str, int]:
        """
        Get assistant counts by type for a category using a single optimized query.

        Args:
            category_id: ID of category to get stats for

        Returns:
            Dictionary with marketplace_assistants_count, project_assistants_count and workflows_count
        """
        # Import here to avoid circular dependency
        from codemie.core.workflow_models import WorkflowConfig
        from codemie.rest_api.models.assistant import Assistant

        with Session(Assistant.get_engine()) as session:
            # Use conditional aggregation with CASE statements in a single query
            query = select(
                func.count(case((Assistant.is_global.is_(True), 0))).label('marketplace_count'),
                func.count(case((Assistant.is_global.is_(False), 0))).label('project_count'),
            ).where(Assistant.categories.cast(JSONB).contains([category_id]))

            result = session.exec(query).one()

            workflows_count = session.exec(
                select(func.count()).where(cast(WorkflowConfig.categories, JSONB).contains([category_id]))
            ).one()

            return {
                "marketplace_assistants_count": result[0],
                "project_assistants_count": result[1],
                "workflows_count": workflows_count,
            }


# ============================================================================
# Singleton Instance
# ============================================================================

category_service = DatabaseCategoryService()
