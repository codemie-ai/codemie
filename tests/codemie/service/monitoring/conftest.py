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

from codemie.core.dependecies import litellm_context


@pytest.fixture(autouse=True)
def reset_litellm_context():
    """Reset litellm_context ContextVar before each test.

    Prevents cross-test contamination: if any test (or imported code) sets
    litellm_context, the value persists in the thread's execution context.
    Resetting to None ensures get_current_project() returns the fallback value,
    matching the pre-context-aware behaviour expected by existing assertions.
    """
    token = litellm_context.set(None)
    yield
    litellm_context.reset(token)
