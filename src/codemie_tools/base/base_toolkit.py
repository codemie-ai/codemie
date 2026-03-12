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

from abc import ABC

from pydantic import BaseModel


class BaseToolkit(BaseModel, ABC):
    @classmethod
    def get_definition(cls):
        """
        Returns the toolkit definition containing information about the toolkit and its tools.
        This method is used by the toolkit scanner to discover available toolkits and their tools.

        Returns:
            ToolKit: A ToolKit object containing toolkit information and list of tools
        """
        # Get the toolkit UI info which contains the toolkit definition
        toolkit_info = cls.get_tools_ui_info()

        # If the result is a dictionary, convert it to a ToolKit object
        if isinstance(toolkit_info, dict):
            from codemie_tools.base.models import ToolKit

            return ToolKit(**toolkit_info)

        # If it's already a ToolKit object, return it directly
        return toolkit_info

    def get_tools(self, *args, **kwargs):
        raise NotImplementedError("get_tools method is not implemented for toolkit")

    @classmethod
    def get_tools_ui_info(cls, *args, **kwargs):
        raise NotImplementedError("Toolkit couldn't be used on UI")

    def get_toolkit(self, *args, **kwargs):
        return self.__class__()


class DiscoverableToolkit(BaseToolkit):
    """A marker interface that indicates a toolkit should be autodiscovered.

    Any toolkit that implements this interface will be automatically discovered
    by the toolkit_provider
    """

    pass
