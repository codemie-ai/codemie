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

import pytest

from codemie.rest_api.security.client_context import current_client_source, ClientSource


@pytest.fixture(autouse=True)
def reset_client_source():
    """Reset the client-source ContextVar before each test.

    Mirrors reset_litellm_context in tests/codemie/service/monitoring/conftest.py:
    prevents a value set by one test (or by production code under test) from
    leaking into the next test in the same process.
    """
    token = current_client_source.set(ClientSource.PLATFORM)
    yield
    current_client_source.reset(token)
