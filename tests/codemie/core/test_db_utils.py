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

"""Unit tests for database utility functions.

Tests cover SQL LIKE wildcard escaping for security (Story 2, NFR-3.1).
"""

import pytest

from codemie.core.db_utils import escape_like_wildcards


class TestEscapeLikeWildcards:
    """Test escape_like_wildcards function for SQL injection prevention."""

    def test_escape_backslash(self):
        """Test escaping of backslash (escape character itself)."""
        # CRITICAL: Backslash must be escaped first to prevent ambiguous patterns
        assert escape_like_wildcards("\\") == "\\\\"
        assert escape_like_wildcards("test\\user") == "test\\\\user"
        assert escape_like_wildcards("\\\\") == "\\\\\\\\"

    def test_escape_percent_sign(self):
        """Test escaping of % (match any sequence) wildcard."""
        # AC: Search for % returns only literal percent matches
        assert escape_like_wildcards("%") == r"\%"
        assert escape_like_wildcards("100%") == r"100\%"
        assert escape_like_wildcards("%admin%") == r"\%admin\%"

    def test_escape_underscore(self):
        """Test escaping of _ (match single character) wildcard."""
        # AC: Search for _ returns only literal underscore matches
        assert escape_like_wildcards("_") == r"\_"
        assert escape_like_wildcards("test_user") == r"test\_user"
        assert escape_like_wildcards("t_st") == r"t\_st"

    def test_escape_combined_wildcards(self):
        """Test escaping when multiple special characters are present."""
        # AC: All special characters are escaped, backslash first
        assert escape_like_wildcards("a%b_c") == r"a\%b\_c"
        assert escape_like_wildcards("%_%") == r"\%\_\%"
        assert escape_like_wildcards("\\%_") == r"\\\%\_"
        assert escape_like_wildcards("test\\%user_name") == r"test\\\%user\_name"

    def test_normal_text_unchanged(self):
        """Test that normal text without wildcards passes through."""
        # AC: Normal search functionality unaffected
        assert escape_like_wildcards("john") == "john"
        assert escape_like_wildcards("john@example.com") == "john@example.com"
        assert escape_like_wildcards("Admin User") == "Admin User"

    def test_empty_string(self):
        """Test handling of empty string."""
        assert escape_like_wildcards("") == ""

    def test_special_characters_preserved(self):
        """Test that other special characters are not affected."""
        # Only % and _ should be escaped
        assert escape_like_wildcards("user@domain.com") == "user@domain.com"
        assert escape_like_wildcards("test-user") == "test-user"
        assert escape_like_wildcards("user#123") == "user#123"
        assert escape_like_wildcards("test*user") == "test*user"

    def test_security_attack_patterns(self):
        """Test escaping of known attack patterns."""
        # AC: Attack patterns are neutralized
        # Pattern: Enumerate all records
        assert escape_like_wildcards("%") == r"\%"

        # Pattern: Single-char wildcard probing
        assert escape_like_wildcards("admin_") == r"admin\_"

        # Pattern: Wildcard sandwich
        assert escape_like_wildcards("%admin%") == r"\%admin\%"

        # Pattern: Underscore probing (t_st matches test, tast, t0st)
        assert escape_like_wildcards("t_st") == r"t\_st"

        # Pattern: Backslash injection to bypass escaping
        assert escape_like_wildcards("\\%") == r"\\\%"
        assert escape_like_wildcards("\\_") == r"\\\_"

    def test_multiple_percent_signs(self):
        """Test multiple percent signs are all escaped."""
        assert escape_like_wildcards("%%%") == r"\%\%\%"
        assert escape_like_wildcards("a%b%c%") == r"a\%b\%c\%"

    def test_multiple_underscores(self):
        """Test multiple underscores are all escaped."""
        assert escape_like_wildcards("___") == r"\_\_\_"
        assert escape_like_wildcards("a_b_c_") == r"a\_b\_c\_"

    def test_real_world_email_patterns(self):
        """Test real-world email search patterns."""
        # Normal email search (no wildcards)
        assert escape_like_wildcards("john@example.com") == "john@example.com"

        # Malicious email search (with wildcards)
        assert escape_like_wildcards("john%@example.com") == r"john\%@example.com"
        assert escape_like_wildcards("_@example.com") == r"\_@example.com"

    def test_real_world_username_patterns(self):
        """Test real-world username search patterns."""
        # Normal username (with underscore)
        assert escape_like_wildcards("john_doe") == r"john\_doe"

        # Malicious username
        assert escape_like_wildcards("admin%") == r"admin\%"

    @pytest.mark.parametrize(
        "input_text,expected_output",
        [
            # Edge cases
            ("%", r"\%"),
            ("_", r"\_"),
            ("\\", "\\\\"),
            ("", ""),
            # Normal text
            ("simple", "simple"),
            # Mixed cases with backslash
            ("test_%_user", r"test\_\%\_user"),
            ("100%_complete", r"100\%\_complete"),
            ("\\%admin", r"\\\%admin"),
            ("\\_test", r"\\\_test"),
            # Real-world patterns
            ("admin@epam.com", "admin@epam.com"),
            ("john_doe_123", r"john\_doe\_123"),
        ],
    )
    def test_parametrized_escaping(self, input_text: str, expected_output: str):
        """Parametrized test for various input patterns including backslash."""
        assert escape_like_wildcards(input_text) == expected_output


class TestEscapeLikeWildcardsSQL:
    """F-06: Integration-style tests verifying escape_like_wildcards produces correct SQL LIKE ESCAPE behavior.

    Uses SQLite in-memory to verify actual SQL LIKE ESCAPE '\' semantics,
    confirming that escaped patterns match only literal characters.
    """

    @pytest.fixture(autouse=True)
    def setup_db(self):
        """Create in-memory SQLite database with test data."""
        import sqlite3

        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE users (email TEXT)")
        test_emails = [
            "admin@example.com",
            "admin_test@example.com",
            "100%done@example.com",
            "john@example.com",
            "john_doe@example.com",
            "test\\slash@example.com",
        ]
        self.conn.executemany("INSERT INTO users (email) VALUES (?)", [(e,) for e in test_emails])
        self.conn.commit()
        yield
        self.conn.close()

    def _search(self, search_term: str) -> list[str]:
        """Execute LIKE search with escaped wildcards, same pattern used in repository layer."""
        escaped = escape_like_wildcards(search_term)
        pattern = f"%{escaped}%"
        cursor = self.conn.execute(
            "SELECT email FROM users WHERE email LIKE ? ESCAPE '\\'",
            (pattern,),
        )
        return [row[0] for row in cursor.fetchall()]

    def test_literal_underscore_matches_only_literal(self):
        """Searching for '_' should match emails with literal underscores, not single-char wildcards."""
        results = self._search("_")
        assert "admin_test@example.com" in results
        assert "john_doe@example.com" in results
        # 'admin@example.com' should NOT match (no literal underscore)
        assert "admin@example.com" not in results

    def test_literal_percent_matches_only_literal(self):
        """Searching for '%' should match emails with literal percent signs only."""
        results = self._search("%")
        assert results == ["100%done@example.com"]

    def test_normal_search_unaffected(self):
        """Normal search without wildcards still works correctly."""
        results = self._search("admin")
        assert "admin@example.com" in results
        assert "admin_test@example.com" in results

    def test_wildcard_attack_pattern_neutralized(self):
        """Searching for '%@%' should match only literal percent signs, not all emails."""
        results = self._search("%@%")
        assert len(results) == 0  # No email has literal '%@%'

    def test_underscore_probe_neutralized(self):
        """Searching for 'a_min' should NOT match 'admin' (underscore is literal)."""
        results = self._search("a_min")
        # Only matches if there's a literal 'a_min' substring
        assert "admin@example.com" not in results
