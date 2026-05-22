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

"""Shared Elasticsearch field constants for analytics handlers."""

from __future__ import annotations

# Elasticsearch field constants
METRIC_NAME_KEYWORD_FIELD = "metric_name.keyword"
USER_NAME_KEYWORD_FIELD = "attributes.user_name.keyword"
USER_EMAIL_KEYWORD_FIELD = "attributes.user_email.keyword"
USER_ID_KEYWORD_FIELD = "attributes.user_id.keyword"
PROJECT_KEYWORD_FIELD = "attributes.project.keyword"

# Placeholder user IDs that carry no real user context and always have zero spending.
# "unknown"  — emitted by webhook binding when webhook_id is not found (no auth context).
# SYSTEM_USER UUID — emitted by background datasource indexing and platform budget checks.
# Verified via ES query 2026-05-11: money_spent = 0 on all 60 203 affected documents.
PLACEHOLDER_USER_IDS: list[str] = [
    "unknown",
    "00000000-0000-0000-0000-000000000000",
]

# Placeholder user emails/usernames that correspond to system or unresolved actors.
# "system"  — SYSTEM_USER username used for background datasource indexing and budget checks.
# "unknown" — webhook binding fallback when no user context is available.
PLACEHOLDER_USER_EMAILS: list[str] = [
    "system",
    "unknown",
]
