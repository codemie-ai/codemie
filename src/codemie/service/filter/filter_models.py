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

from datetime import date
from enum import Enum


START_DATE_DEFAULT = date(2024, 1, 1).isoformat()


class SearchFields(str, Enum):
    PROJECT_NAME = "project_name.keyword"
    PROJECT = "project.keyword"
    REPO_NAME = "repo_name.keyword"
    INDEX_TYPE = "index_type.keyword"
    CREATED_BY_NAME = "created_by.name.keyword"
    PROJECT_SPACE_VISIBLE = "project_space_visible"
    SHARED = "shared"
    COMPLETED = "completed"
    SLUG = "slug"
    ERROR = "error"
    DATE = "date"
    CATEGORIES = "categories"
    IS_QUEUED = "is_queued"


class IndexInfoStatus(str, Enum):
    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"
    FAILED = "failed"
    QUEUED = "queued"
