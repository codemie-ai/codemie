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

"""Pytest configuration for local_auth_router tests.

This module disables the rate limiter before importing the router module
to prevent rate limiting from interfering with tests.
"""

import pytest

import codemie.rest_api.rate_limit

# Disable rate limiter for all tests
codemie.rest_api.rate_limit.limiter.enabled = False


@pytest.fixture(autouse=True)
def _inject_request_uuid(monkeypatch):
    """Tests use bare FastAPI() apps without uuid-injection middleware.
    Provide uuid default on request.state so authenticate() doesn't crash."""
    from starlette.datastructures import State

    original_getattr = State.__getattr__

    def _getattr_with_uuid(self, key):
        if key == "uuid":
            return "test-request-uuid"
        return original_getattr(self, key)

    monkeypatch.setattr(State, "__getattr__", _getattr_with_uuid)


# ---------------------------------------------------------------------------
# A real, migrated PostgreSQL for the publish-gate tests (EXECUTION-PLAN 7.1/7.6)
# ---------------------------------------------------------------------------
#
# Publishing under the approval gate is a *database* transition: a
# compare-and-set on `assistants` plus an `mm_review_decision` insert, in one
# transaction (`queue_service.decide`). A mocked session cannot express either,
# and `SQLModel.metadata.create_all` builds the schema from the ORM's beliefs
# rather than from the migration production runs — exactly the divergence that
# left the audit feature persisting nothing at all (EXECUTION-PLAN 5.3).
#
# Session-scoped so one throwaway database serves every module in this package
# that needs it; each test cleans its own rows. If PostgreSQL is unreachable
# these fixtures **fail**, they do not skip — a skipped persistence test is
# indistinguishable from a passing one on a dashboard (F-40).
