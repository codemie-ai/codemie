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
Tools preprocessing module for modifying tool representations before they are sent to LLMs.

This module implements a flexible framework for preprocessing tools based on the specific LLM model
being used, allowing for model-specific adaptations to improve tool usability.
"""

from abc import ABC, abstractmethod
import textwrap

from langchain_core.tools import BaseTool

from codemie.configs.logger import logger

GPT_DEPLOYMENT_NAME_SUBSTRING = "gpt-4"


class ToolsPreprocessor(ABC):
    """Base interface for all tool preprocessors."""

    @abstractmethod
    def process(self, tools: list[BaseTool]) -> list[BaseTool]:
        """
        Process a list of tools and return the processed list.

        Args:
            tools: List of tools to process

        Returns:
            Processed list of tools
        """
        pass


class DescriptionPreprocessor(ToolsPreprocessor):
    """Preprocessor that optimizes tool descriptions for specific models."""

    def process(self, tools: list[BaseTool]) -> list[BaseTool]:
        """
        Optimize tool descriptions for specific LLM models.

        This implementation cleans up tool descriptions by:
        1. Removing empty lines at the beginning and end of descriptions
        2. Removing common leading whitespace from all lines in the description

        Args:
            tools: List of tools to process

        Returns:
            List of tools with optimized descriptions
        """
        logger.debug(f"Optimizing descriptions for {len(tools)} tools")

        for tool in tools:
            if tool.description:
                # Use textwrap.dedent to remove common leading whitespace
                # and strip to remove empty lines at the beginning and end
                dedented_description = textwrap.dedent(tool.description).strip()

                # If the description is only whitespace, set to empty string
                if not dedented_description:
                    tool.description = ""
                else:
                    tool.description = dedented_description

        return tools


class GPT4ToolsPreprocessor(ToolsPreprocessor):
    """Preprocessor specifically designed for GPT-4 compatibility."""

    # Maximum allowed description length for GPT-4 tools
    MAX_DESCRIPTION_LENGTH = 1024

    def process(self, tools: list[BaseTool]) -> list[BaseTool]:
        """
        Apply GPT-4 specific modifications to tools.

        Truncates tool descriptions to a maximum of 1024 characters to ensure
        they fit within GPT-4's context processing limits.

        Args:
            tools: List of tools to process

        Returns:
            List of tools optimized for GPT-4
        """
        logger.debug(f"Applying GPT-4 specific preprocessing to {len(tools)} tools")

        for tool in tools:
            if tool.description and len(tool.description) > self.MAX_DESCRIPTION_LENGTH:
                original_length = len(tool.description)
                tool.description = tool.description[: self.MAX_DESCRIPTION_LENGTH]
                logger.debug(
                    f"Truncated description for tool '{tool.name}' from {original_length} to "
                    f"{self.MAX_DESCRIPTION_LENGTH} characters"
                )

        return tools


class ToolsPreprocessorFactory:
    """Factory for creating preprocessor chains based on model type."""

    # Cache for preprocessor chains to avoid recreating them
    _preprocessor_cache: dict[str, list[ToolsPreprocessor]] = {}

    @classmethod
    def create_preprocessor_chain(cls, llm_model: str) -> list[ToolsPreprocessor]:
        """
        Create a chain of preprocessors appropriate for the given model.

        Args:
            llm_model: The name/identifier of the LLM model

        Returns:
            A list of preprocessors to be applied in sequence
        """
        # Check if we've already created a chain for this model
        if llm_model in cls._preprocessor_cache:
            return cls._preprocessor_cache[llm_model]

        # Default chain
        default_chain = [DescriptionPreprocessor()]

        # Model-specific chains
        chain = default_chain

        # Create model-specific chains
        if GPT_DEPLOYMENT_NAME_SUBSTRING in llm_model.lower():
            chain = [DescriptionPreprocessor(), GPT4ToolsPreprocessor()]

        # Cache the chain for future use
        cls._preprocessor_cache[llm_model] = chain

        logger.info(f"Created preprocessor chain for model {llm_model}: {[c.__class__.__name__ for c in chain]}")
        return chain
