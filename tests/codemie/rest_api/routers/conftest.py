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

import codemie.rest_api.rate_limit

# Disable rate limiter for all tests
codemie.rest_api.rate_limit.limiter.enabled = False
