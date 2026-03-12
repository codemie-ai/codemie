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
Repository for category database operations.
"""

import math
from typing import Any, Dict, List

from sqlmodel import Session, case, func, select

from codemie.rest_api.models.category import Category


class CategoryRepository:
    """Repository for category database operations"""

    @staticmethod
    def _build_counts_subquery(assistant_model):
        """
        Build subquery to unnest assistant categories and aggregate counts.

        Returns a subquery that can be used for filtering or as a CTE.
        """
        return select(
            func.jsonb_array_elements_text(assistant_model.categories).label("category_id"),
            assistant_model.is_global,
        ).select_from(assistant_model)

    @staticmethod
    def _aggregate_counts(subquery):
        """
        Aggregate counts from unnested subquery.

        Returns query with category_id, marketplace_count, project_count.
        """
        return select(
            subquery.c.category_id,
            func.sum(case((subquery.c.is_global, 1), else_=0)).label("marketplace_count"),
            func.sum(case((~subquery.c.is_global, 1), else_=0)).label("project_count"),
        ).group_by(subquery.c.category_id)

    @staticmethod
    def _query_for_name_sort(session: Session, assistant_model, page: int, per_page: int) -> List[Dict[str, Any]]:
        """
        Query categories sorted by name (optimized: paginate first, then count).
        """
        # Step 1: Get paginated categories (simple, fast query)
        categories = list(
            session.exec(select(Category).order_by(Category.name.asc()).offset(page * per_page).limit(per_page)).all()
        )

        if not categories:
            return []

        # Step 2: Build counts query for only these specific categories
        category_ids = [cat.id for cat in categories]
        counts_subquery = CategoryRepository._build_counts_subquery(assistant_model).subquery()
        counts_query = CategoryRepository._aggregate_counts(counts_subquery).where(
            counts_subquery.c.category_id.in_(category_ids)
        )

        # Step 3: Execute and build lookup dictionary
        counts_result = session.exec(counts_query).all()
        counts_dict = {row[0]: {"marketplace": row[1], "project": row[2]} for row in counts_result}

        # Step 4: Combine categories with counts
        return [
            {
                "id": cat.id,
                "name": cat.name,
                "description": cat.description,
                "date": cat.date,
                "update_date": cat.update_date,
                "marketplace_assistants_count": counts_dict.get(cat.id, {}).get("marketplace", 0),
                "project_assistants_count": counts_dict.get(cat.id, {}).get("project", 0),
            }
            for cat in categories
        ]

    @staticmethod
    def query(page: int = 0, per_page: int = 10) -> Dict[str, Any]:
        """
        Query categories with assistant counts, supporting pagination.

        Strategy:
        - Categories are sorted by name (alphabetically)
        - Paginate categories first, then calculate counts (efficient)

        Args:
            page: Page number (0-indexed)
            per_page: Number of items per page

        Returns:
            Dictionary containing:
                - categories: List of category dicts with counts
                - page: Current page number
                - per_page: Items per page
                - total: Total number of categories
                - pages: Total number of pages
        """
        from codemie.rest_api.models.assistant import Assistant

        with Session(Category.get_engine()) as session:
            # Get total count and calculate pages
            total = session.exec(select(func.count()).select_from(Category)).one()
            pages = math.ceil(total / per_page) if per_page > 0 else 1

            result_categories = CategoryRepository._query_for_name_sort(session, Assistant, page, per_page)

            return {
                "categories": result_categories,
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": pages,
            }
