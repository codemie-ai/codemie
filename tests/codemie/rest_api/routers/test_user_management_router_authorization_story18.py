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

"""Tests for Story 18: User Detail Filtering for Project Admins

Note: Per code review, authorization dependency tests have been removed as they tested
internal implementation details and were brittle to changes in authorization flow.

Core authorization logic is fully validated by:
- Repository tests (13 tests): test_user_project_repository_story18.py
  - can_project_admin_view_user() authorization check
  - get_admin_visible_projects_for_user() response filtering
- Service tests (9 tests): test_user_management_service_story18.py
  - Super admin sees all projects
  - Project admin sees filtered projects
  - Knowledge bases shown in full
  - 404 handling for non-existent users

Total: 22 unit tests providing complete coverage of Story 18 functionality.
"""
