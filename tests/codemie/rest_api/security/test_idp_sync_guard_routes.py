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
"""Route walk of the real app: no authenticated route escapes the IdP-sync rule (AC4, EPMCDME-16895)."""

from __future__ import annotations

from tests.codemie.rest_api.router_guards.guarded_app import load_guarded_app_dump


def test_sync_account_is_refused_on_every_authenticated_route_outside_its_api():
    report = load_guarded_app_dump()["idp_sync_guard"]

    assert report["authenticated_routes"] > 0
    assert report["not_refused"] == []
