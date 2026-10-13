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

"""OpenAPI snapshot guard (EPMCDME-15651 step 0).

``baselines/openapi.json`` holds ``app.openapi()`` of the guarded app, built from base commit
``e55bea3005``. The test compares the live schema in full, as exact text: paths, operationIds, tags
(including the extra ``Assistant Versioning`` tag), request and response schemas, and components.

A failing run never rewrites the snapshot. After an intended, reviewed contract change, regenerate it
manually (see ``regenerate_baselines``).
"""

from __future__ import annotations

from tests.codemie.rest_api.router_guards.baseline import OPENAPI_BASELINE, assert_matches_baseline
from tests.codemie.rest_api.router_guards.guarded_app import load_guarded_app_dump


def test_openapi_matches_baseline():
    assert_matches_baseline(load_guarded_app_dump()["openapi"], OPENAPI_BASELINE)
