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

"""Database utility functions for SQL operations.

This module provides utility functions for safe database operations,
particularly focused on security aspects like SQL injection prevention.
"""


def escape_like_wildcards(text: str) -> str:
    """Escape SQL LIKE wildcard characters in user input.

    Escapes the special LIKE wildcard characters (% and _) by prefixing them
    with a backslash, treating them as literal characters in LIKE queries.
    This prevents users from crafting wildcard patterns to enumerate data
    or perform information leakage attacks.

    Security Context:
        - Prevents pattern-based information leakage
        - Stops enumeration attacks via wildcard abuse
        - Complies with NFR-3.1 (SQL Injection Prevention)

    Args:
        text: User input string to escape

    Returns:
        String with % and _ escaped as \\% and \\_

    Examples:
        >>> escape_like_wildcards("normal_text")
        'normal\\_text'
        >>> escape_like_wildcards("100%")
        '100\\%'
        >>> escape_like_wildcards("%admin%")
        '\\%admin\\%'
        >>> escape_like_wildcards("t_st")
        't\\_st'
        >>> escape_like_wildcards("")
        ''

    Usage:
        # In repository search methods:
        search_term = escape_like_wildcards(user_input)
        pattern = f"%{search_term}%"
        query = select(Model).where(Model.field.ilike(pattern))

    Related:
        - FR-1.1: User Search Security
        - FR-3.1: Project Search Security
        - NFR-3.1: SQL Injection Prevention
        - Story 2: Search Security - LIKE Wildcard Escaping
    """
    if not text:
        return text

    # CRITICAL: Escape backslash FIRST to prevent ambiguous patterns
    # If user input contains \, it could interfere with our escape sequences
    text = text.replace("\\", "\\\\")

    # Then escape % (matches any sequence) and _ (matches single character)
    text = text.replace("%", r"\%")
    text = text.replace("_", r"\_")

    return text
