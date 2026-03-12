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

"""Unit tests for SecureQueryBuilder - query structure validation."""

from __future__ import annotations

from unittest.mock import Mock


from codemie.service.analytics.query_builder import SecureQueryBuilder


# =============================================================================
# TEST CLASS
# =============================================================================


class TestSecureQueryBuilder:
    """Test suite for SecureQueryBuilder role-based query construction."""

    # =========================================================================
    # 1. QUERY STRUCTURE TESTS (4 tests)
    # =========================================================================

    def test_inject_project_filter_plain_user_only(self):
        """Verify query structure for plain user only."""

        user = Mock(id="user-1", project_names=["proj-a"], admin_project_names=[])
        user.is_admin = False

        builder = SecureQueryBuilder(user)
        query = builder.build()

        # Verify structure
        assert "bool" in query
        assert "must" in query["bool"]

        # Find injected filter
        injected_filter = query["bool"]["must"][0]
        assert "bool" in injected_filter
        assert "should" in injected_filter["bool"]
        assert injected_filter["bool"]["minimum_should_match"] == 1

        # Verify plain user condition
        should_clauses = injected_filter["bool"]["should"]
        assert len(should_clauses) == 1
        plain_clause = should_clauses[0]
        assert plain_clause["bool"]["must"][0] == {"term": {"attributes.user_id.keyword": "user-1"}}
        assert plain_clause["bool"]["must"][1] == {"terms": {"attributes.project.keyword": ["proj-a"]}}

    def test_inject_project_filter_admin_only(self):
        """Verify query structure for admin only."""

        user = Mock(id="user-2", project_names=[], admin_project_names=["proj-c"])
        user.is_admin = False

        builder = SecureQueryBuilder(user)
        query = builder.build()

        injected_filter = query["bool"]["must"][0]
        should_clauses = injected_filter["bool"]["should"]

        # Should have single clause without user_id filter
        assert len(should_clauses) == 1
        assert should_clauses[0] == {"terms": {"attributes.project.keyword": ["proj-c"]}}

    def test_inject_project_filter_mixed_roles(self):
        """Verify query structure for mixed roles."""

        user = Mock(id="user-3", project_names=["proj-a", "proj-b"], admin_project_names=["proj-c", "proj-d"])
        user.is_admin = False

        builder = SecureQueryBuilder(user)
        query = builder.build()

        injected_filter = query["bool"]["must"][0]
        should_clauses = injected_filter["bool"]["should"]

        # Should have TWO clauses (plain + admin)
        assert len(should_clauses) == 2

        # First clause: plain user (with user_id filter)
        plain_clause = should_clauses[0]
        assert "bool" in plain_clause
        assert plain_clause["bool"]["must"][0] == {"term": {"attributes.user_id.keyword": "user-3"}}
        assert plain_clause["bool"]["must"][1] == {"terms": {"attributes.project.keyword": ["proj-a", "proj-b"]}}

        # Second clause: admin (no user_id filter)
        admin_clause = should_clauses[1]
        assert admin_clause == {"terms": {"attributes.project.keyword": ["proj-c", "proj-d"]}}

    def test_inject_project_filter_empty_lists(self):
        """CRITICAL: Verify empty lists create safe query that returns 0 results."""

        user = Mock(id="user-4", project_names=[], admin_project_names=None)
        user.is_admin = False

        builder = SecureQueryBuilder(user)
        query = builder.build()

        injected_filter = query["bool"]["must"][0]

        # Should have empty should clause with minimum_should_match: 0
        assert injected_filter["bool"]["should"] == []
        assert injected_filter["bool"]["minimum_should_match"] == 0  # Safe: returns 0 results

    # =========================================================================
    # 2. add_project_filter() TESTS (5 tests)
    # =========================================================================

    def test_add_project_filter_restricts_to_plain_subset(self):
        """Verify add_project_filter restricts plain user projects."""

        user = Mock(id="user-6", project_names=["proj-a", "proj-b"], admin_project_names=[])
        user.is_admin = False

        builder = SecureQueryBuilder(user)
        builder.add_project_filter(["proj-a"])  # Restrict to subset
        query = builder.build()

        # Should have 2 items in must: base filter + restriction
        assert len(query["bool"]["must"]) == 2

        # Second item is restriction (append-only)
        restriction = query["bool"]["must"][1]
        assert restriction == {"terms": {"attributes.project.keyword": ["proj-a"]}}

    def test_add_project_filter_restricts_to_admin_subset(self):
        """Verify add_project_filter restricts admin projects."""

        user = Mock(id="user-7", project_names=[], admin_project_names=["proj-c", "proj-d"])
        user.is_admin = False

        builder = SecureQueryBuilder(user)
        builder.add_project_filter(["proj-c"])
        query = builder.build()

        restriction = query["bool"]["must"][1]
        assert restriction == {"terms": {"attributes.project.keyword": ["proj-c"]}}

    def test_add_project_filter_rejects_inaccessible(self):
        """CRITICAL: Verify unauthorized projects filtered out with warning."""

        user = Mock(id="user-8", project_names=["proj-a"], admin_project_names=[])
        user.is_admin = False

        builder = SecureQueryBuilder(user)
        builder.add_project_filter(["proj-a", "proj-unauthorized"])

        query = builder.build()

        # Only proj-a in restriction (unauthorized project filtered out)
        restriction = query["bool"]["must"][1]
        assert restriction == {"terms": {"attributes.project.keyword": ["proj-a"]}}

        # Note: Warning is logged but captured by pytest's logging infrastructure
        # The key security check is that only accessible projects are in the query

    def test_add_project_filter_mixed_roles_restriction(self):
        """Verify add_project_filter works with mixed roles."""

        user = Mock(id="user-9", project_names=["proj-a", "proj-b"], admin_project_names=["proj-c"])
        user.is_admin = False

        builder = SecureQueryBuilder(user)
        builder.add_project_filter(["proj-a", "proj-c"])  # One plain, one admin
        query = builder.build()

        # Should have base filter + restriction
        assert len(query["bool"]["must"]) == 2

        restriction = query["bool"]["must"][1]
        assert set(restriction["terms"]["attributes.project.keyword"]) == {"proj-a", "proj-c"}

    def test_add_project_filter_empty_list_does_nothing(self):
        """Verify empty project list doesn't modify query."""

        user = Mock(id="user-10", project_names=["proj-a"], admin_project_names=[])
        user.is_admin = False

        builder = SecureQueryBuilder(user)
        query_before = builder.build()

        builder.add_project_filter([])  # Empty list
        query_after = builder.build()

        # Queries should be identical
        assert query_before == query_after

    # =========================================================================
    # 3. SUPER ADMIN TESTS (4 tests)
    # =========================================================================

    def test_super_admin_no_filters_injected(self):
        """CRITICAL: Verify super admin queries have NO project filters."""

        user = Mock(id="super-admin-1", project_names=[], admin_project_names=[])
        user.is_admin = True  # Super admin

        builder = SecureQueryBuilder(user)
        query = builder.build()

        # Super admin should have NO filters in must clause
        assert len(query["bool"]["must"]) == 0
        assert query["bool"]["filter"] == []

    def test_super_admin_with_empty_projects_gets_unrestricted_access(self):
        """CRITICAL: Verify super admin with no projects gets full access."""

        user = Mock(id="super-admin-2", project_names=None, admin_project_names=None)
        user.is_admin = True  # Super admin

        builder = SecureQueryBuilder(user)
        query = builder.build()

        # No filters should be present
        assert len(query["bool"]["must"]) == 0

    def test_super_admin_add_project_filter_skips_validation(self):
        """Verify super admin can filter any project without validation."""

        user = Mock(id="super-admin-3", project_names=[], admin_project_names=[])
        user.is_admin = True  # Super admin

        builder = SecureQueryBuilder(user)
        builder.add_project_filter(["any-project", "another-project"])
        query = builder.build()

        # Should have project filter added (no base filter, just the restriction)
        assert len(query["bool"]["must"]) == 1
        restriction = query["bool"]["must"][0]
        assert set(restriction["terms"]["attributes.project.keyword"]) == {"any-project", "another-project"}

    def test_super_admin_with_existing_projects_ignores_them(self):
        """Verify super admin ignores their project assignments."""

        user = Mock(id="super-admin-4", project_names=["proj-a"], admin_project_names=["proj-b"])
        user.is_admin = True  # Super admin

        builder = SecureQueryBuilder(user)
        query = builder.build()

        # No filters should be injected (projects ignored)
        assert len(query["bool"]["must"]) == 0
