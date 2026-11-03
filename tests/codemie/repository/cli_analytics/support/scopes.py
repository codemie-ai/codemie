# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""A ready-made scope for fixtures that build a LocalAnalyticsFilter."""

from codemie.repository.cli_analytics.scope import ViewerScope

# Shared-spend view of an unrestricted admin: every non-Personal session, no Personal one.
EXCLUDE_SCOPE = ViewerScope(viewer_email="admin@x.com", unrestricted=True, admin_projects=frozenset(), mode="exclude")

# A detail-mode scope of an unrestricted admin: any non-Personal session, no one else's Personal one.
DETAIL_SCOPE = ViewerScope(viewer_email="admin@x.com", unrestricted=True, admin_projects=frozenset(), mode="detail")
