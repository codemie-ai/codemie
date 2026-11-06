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

"""Category exporter: emits CategoryCreateRequest fields only."""

from typing import Any

from fastapi import status

from codemie.rest_api.models.category import Category, CategoryCreateRequest
from codemie.service.iac_export.document import ExportDocument, ExportError, ExportMeta


class CategoryExporter:
    """No per-user check on purpose: `GET /assistants/categories` already returns every category (name, description)
    to any authenticated user, and this export carries nothing beyond those two fields. Only the admin UI routes
    (`/assistants/categories/{id}` and `/list`) are admin-only, because they manage categories."""

    def export(self, id: str, user: Any) -> ExportDocument:  # noqa: ARG002  # NOSONAR: S1172 - Exporter protocol
        category = Category.find_by_id(id)
        if not category:
            raise ExportError(f"Category with ID '{id}' does not exist.", status.HTTP_404_NOT_FOUND)
        spec = CategoryCreateRequest(name=category.name, description=category.description)
        return ExportDocument(meta=ExportMeta(kind="category", name=category.name), spec=spec)
