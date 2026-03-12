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

import codemie.service.filter.compose_filter_functions as fltr_func

from codemie.service.filter.base_filter_service import BaseFilterData
from codemie.service.filter.filter_models import SearchFields

ID_KEYWORD = "id.keyword"


class IndexInfoFilter(BaseFilterData):
    FILTER_CONFIG = {
        "name": {
            "field_name": SearchFields.REPO_NAME.value,
            "filter_compose_func": fltr_func.compose_wildcard_filter,
        },
        "project": {
            "field_name": SearchFields.PROJECT_NAME.value,
            "filter_compose_func": fltr_func.compose_term_filter,
        },
        "index_type": {
            "field_name": SearchFields.INDEX_TYPE.value,
            "filter_compose_func": fltr_func.compose_term_filter,
        },
        "created_by": {
            "field_name": SearchFields.CREATED_BY_NAME.value,
            "filter_compose_func": fltr_func.compose_wildcard_filter,
        },
        "date_range": {
            "field_name": SearchFields.DATE.value,
            "filter_compose_func": fltr_func.compose_combined_date_range_filter,
        },
        "is_shared": {
            "field_name": SearchFields.PROJECT_SPACE_VISIBLE.value,
            "filter_compose_func": fltr_func.compose_term_filter,
        },
        "status": {
            "field_name": [SearchFields.COMPLETED.value, SearchFields.ERROR.value],
            "filter_compose_func": fltr_func.compose_status_filter,
        },
    }


class WorkflowFilter(BaseFilterData):
    FILTER_CONFIG = {
        "name": {
            "field_name": ["name.keyword", "description", ID_KEYWORD],
            "filter_compose_func": fltr_func.compose_multi_field_wildcard_filter,
        },
        "project": {
            "field_name": SearchFields.PROJECT.value,
            "filter_compose_func": fltr_func.compose_term_filter,
        },
        "created_by": {
            "field_name": SearchFields.CREATED_BY_NAME.value,
            "filter_compose_func": fltr_func.compose_wildcard_filter,
        },
        "shared": {
            "field_name": SearchFields.SHARED.value,
            "filter_compose_func": fltr_func.compose_term_filter,
        },
    }


class SettingsFilter(BaseFilterData):
    FILTER_CONFIG = {
        "search": {
            "field_name": ["project_name.keyword", "alias.keyword"],
            "filter_compose_func": fltr_func.compose_multi_field_wildcard_filter,
        },
        "project": {
            "field_name": "project_name.keyword",
            "filter_compose_func": fltr_func.compose_term_filter,
        },
        "created_by": {
            "field_name": "created_by.name.keyword",
            "filter_compose_func": fltr_func.compose_wildcard_filter,
        },
        "type": {"field_name": "credential_type.keyword", "filter_compose_func": fltr_func.compose_term_filter},
        "is_global": {
            "field_name": "is_global",
            "filter_compose_func": fltr_func.compose_term_filter,
            "is_bool": True,
        },
    }


class AssistantFilter(BaseFilterData):
    FILTER_CONFIG = {
        "project": {
            "field_name": SearchFields.PROJECT.value,
            "filter_compose_func": fltr_func.compose_term_filter,
        },
        "created_by": {
            "field_name": SearchFields.CREATED_BY_NAME.value,
            "filter_compose_func": fltr_func.compose_term_filter,
        },
        "search": {
            "field_name": ["name.keyword", "description", ID_KEYWORD],
            "filter_compose_func": fltr_func.compose_multi_field_wildcard_filter,
        },
        "shared": {
            "field_name": SearchFields.SHARED.value,
            "filter_compose_func": fltr_func.compose_term_filter,
            "is_bool": True,
        },
        "is_global": {
            "field_name": "is_global",
            "filter_compose_func": fltr_func.compose_term_filter,
            "is_bool": True,
        },
        "id": {
            "field_name": ID_KEYWORD,
            "filter_compose_func": fltr_func.compose_term_filter,
        },
        "slug": {
            "field_name": SearchFields.SLUG.value,
            "filter_compose_func": fltr_func.compose_term_filter,
        },
        "categories": {
            "field_name": SearchFields.CATEGORIES.value,
            "filter_compose_func": fltr_func.compose_json_array_filter,
        },
        "created_date": {
            "field_name": "created_date",
            "filter_compose_func": fltr_func.compose_comparison_filter,
        },
    }


class AssistantNameFilter(BaseFilterData):
    FILTER_CONFIG = {
        "search": {
            "field_name": "name",
            "filter_compose_func": fltr_func.compose_wildcard_filter,
        },
    }
