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

"""
Tests for LiteLLM context functionality in core dependencies module.
"""

import contextvars
import contextlib

from codemie.core.dependecies import (
    set_litellm_context,
    litellm_context,
)
from codemie.rest_api.models.settings import LiteLLMContext, LiteLLMCredentials


class TestSetLiteLLMContext:
    """Test suite for set_litellm_context function."""

    def setUp(self):
        """Clean up context variables before each test."""
        # Clear the context variable
        with contextlib.suppress(LookupError):
            litellm_context.delete()

    def test_set_valid_litellm_context(self):
        """Test setting a valid LiteLLM context."""
        # Arrange
        credentials = LiteLLMCredentials(api_key="test-key", url="https://test.com")
        context = LiteLLMContext(credentials=credentials, current_project="test-project")

        # Act
        set_litellm_context(context)

        # Assert
        stored_context = litellm_context.get()
        assert stored_context == context
        assert stored_context.current_project == "test-project"
        assert stored_context.credentials.api_key == "test-key"

    def test_set_litellm_context_overwrites_existing(self):
        """Test that setting context overwrites existing context."""
        # Arrange
        old_credentials = LiteLLMCredentials(api_key="old-key", url="https://old.com")
        old_context = LiteLLMContext(credentials=old_credentials, current_project="old-project")
        litellm_context.set(old_context)

        new_credentials = LiteLLMCredentials(api_key="new-key", url="https://new.com")
        new_context = LiteLLMContext(credentials=new_credentials, current_project="new-project")

        # Act
        set_litellm_context(new_context)

        # Assert
        stored_context = litellm_context.get()
        assert stored_context == new_context
        assert stored_context.current_project == "new-project"
        assert stored_context.credentials.api_key == "new-key"

    def test_litellm_context_isolation_between_contexts(self):
        """Test that LiteLLM context is properly isolated between different execution contexts."""
        # This test simulates how context variables work in different async contexts

        # Arrange
        context1 = LiteLLMContext(
            credentials=LiteLLMCredentials(api_key="key1", url="https://test1.com"), current_project="project1"
        )
        context2 = LiteLLMContext(
            credentials=LiteLLMCredentials(api_key="key2", url="https://test2.com"), current_project="project2"
        )

        # Act & Assert in first context
        set_litellm_context(context1)
        stored_context1 = litellm_context.get()
        assert stored_context1 == context1

        # Simulate context switch (in real async code this would be automatic)
        ctx = contextvars.copy_context()

        def test_in_new_context():
            # In new context, context should be copied from parent
            current_context = litellm_context.get(None)
            # Context variables are copied to new contexts, so we should see context1
            assert current_context == context1

            # Set new context in the copied context
            set_litellm_context(context2)
            stored_context2 = litellm_context.get()
            assert stored_context2 == context2
            assert stored_context2.current_project == "project2"

        ctx.run(test_in_new_context)

        # Original context should still be available and unchanged
        stored_context_after = litellm_context.get()
        assert stored_context_after == context1
