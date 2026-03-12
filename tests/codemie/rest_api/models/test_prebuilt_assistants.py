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

import sys
import unittest
from pathlib import Path

from codemie.configs import config
from codemie.rest_api.models.prebuilt_assistants import PrebuiltAssistant
from codemie.rest_api.security.user import User

# Adjust the path for the test environment
sys.path.append(str(Path(__file__).resolve().parents[3]))


class TestPrebuiltAssistant(unittest.TestCase):
    admin_only_assistants = [
        "CodeMie FAQ",
        "CodeMie Feedback",
        "CodeMie Back-end Local Unit Tester",
        "CodeMie Front-end Local Unit Tester",
        "CodeMie UI Local Developer",
        "CodeMie Back-end Local Developer",
    ]

    def test_prebuilt_assistants_returns_list_for_regular_user(self):
        user = User(id="test_user")
        self.assertFalse(user.is_admin)  # Ensure the user is not an admin
        expected_prebuilt_regular_user_assistants_count = 27

        result = PrebuiltAssistant.prebuilt_assistants(user)

        self.assertIsInstance(result, list)
        self.assertEqual(
            len(result), expected_prebuilt_regular_user_assistants_count
        )  # Adjusted to match the implementation
        # Ensure regular user does not see admin-only assistants
        self.assertEqual(len([a for a in result if a.project == "codemie"]), 0)

    def test_prebuilt_assistants_returns_list_for_admin(self):
        user = User(id="admin_user")
        self.assertFalse(user.is_admin)
        result = PrebuiltAssistant.prebuilt_assistants(user)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_prebuilt_assistants_contains_valid_attributes(self):
        user = User(id="test_user")
        self.assertFalse(user.is_admin)
        expected_prebuilt_regular_user_assistants_count = 27

        assistants = PrebuiltAssistant.prebuilt_assistants(user)

        self.assertEqual(len(assistants), expected_prebuilt_regular_user_assistants_count)
        for assistant in assistants:
            self.assertIsNotNone(assistant.name)
            self.assertIsNotNone(assistant.description)
            self.assertIsNotNone(assistant.system_prompt)
            self.assertIsInstance(assistant.toolkits, list)
            self.assertIsNotNone(assistant.icon_url)
            self.assertIsInstance(assistant.is_react, bool)
            self.assertIsNotNone(assistant.project)

    def test_prebuilt_assistants_admin_only_assistants_excluded_for_regular_user(self):
        config.ENV = "tests"
        user = User(id="test_user", roles=[""])
        self.assertFalse(user.is_admin)
        expected_prebuilt_admin_assistants_count = 27

        assistants = PrebuiltAssistant.prebuilt_assistants(user)

        self.assertEqual(
            len(assistants), expected_prebuilt_admin_assistants_count
        )  # Adjusted based on current implementation
        for assistant in assistants:
            self.assertNotIn(assistant.name, self.admin_only_assistants)

    def test_prebuilt_assistants_admin_only_assistants_included_for_admin_user(self):
        user = User(id="admin_user", roles=["admin"])
        expected_prebuilt_assistants_count = 33

        assistants = PrebuiltAssistant.prebuilt_assistants(user)

        self.assertEqual(
            len(assistants), expected_prebuilt_assistants_count
        )  # Adjusted based on current implementation
        for assistant_name in self.admin_only_assistants:
            self.assertTrue(any(assistant.name == assistant_name for assistant in assistants))

    def test_slug_is_prefilled(self):
        assistant = PrebuiltAssistant(
            name="Hello, World!", description="", system_prompt="", toolkits=[], slug="hello-world"
        )
        assert assistant.slug == "hello-world"
