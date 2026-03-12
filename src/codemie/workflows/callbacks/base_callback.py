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


class BaseCallback:
    """
    Base callback class for workflow nodes. This class defines the interface
    for handling events when a node starts, ends, or fails within a workflow.
    """

    def on_node_start(self, *args, **kwargs):
        """
        Called when a workflow node starts.

        Args:
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.
        """
        pass

    def on_node_end(self, output: str, *args, **kwargs):
        """
        Called when a workflow node ends.

        Args:
            output (str): The output result from the node.
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.
        """
        pass

    def on_node_fail(self, exception: Exception, *args, **kwargs):
        """
        Called when a workflow node fails.

        Args:
            exception (Exception): The error from the node.
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.
        """
        pass
