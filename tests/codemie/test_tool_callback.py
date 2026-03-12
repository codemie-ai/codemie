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
Unit tests for src/agents/langgraph/graph_callback.py
"""

import unittest
from uuid import uuid4

from codemie.workflows.callbacks.graph_callback import LanggraphNodeCallback


class TestLanggraphNodeCallback(unittest.TestCase):
    def test_init(self):
        """Test the __init__ method."""
        gen = None  # Example generator placeholder
        callback = LanggraphNodeCallback(gen=gen)
        self.assertEqual(callback.gen, gen)

    def test_set_current_thought(self):
        """Test the set_current_thought method."""
        callback = LanggraphNodeCallback(gen=None)
        self.assertEqual(len(callback._thoughts), 0)
        state_id = str(uuid4())
        callback.set_current_thought(state_id=state_id, agent_name='test_tool')
        self.assertEqual(len(callback._thoughts), 1)
        self.assertTrue(callback._thoughts[state_id].in_progress)

    def test_reset_current_thought(self):
        """Test the reset_current_thought method."""
        callback = LanggraphNodeCallback(gen=None)
        state_id = str(uuid4())
        callback.set_current_thought(state_id=state_id, agent_name='test_tool')
        self.assertEqual(len(callback._thoughts), 1)
        callback.reset_current_thought(state_id=state_id)
        self.assertEqual(len(callback._thoughts), 0)
