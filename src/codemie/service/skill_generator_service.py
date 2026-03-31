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

"""
Service to generate skill details from user input text.
"""

from typing import Optional

from pydantic import BaseModel, Field

from codemie.configs.logger import current_user_email, logger, logging_user_id
from codemie.core.dependecies import get_llm_by_credentials
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.assistant_generator import RecommendationAction, RefineGeneratorResponse
from codemie.rest_api.models.skill import SkillCategory
from codemie.rest_api.models.skill_generator import AssistantToolkit, SkillGeneratorResponse
from codemie.rest_api.security.user import User
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.monitoring.base_monitoring_service import send_log_metric
from codemie.service.monitoring.metrics_constants import (
    SKILL_GENERATOR_ERRORS_METRIC,
    SKILL_GENERATOR_TOTAL_METRIC,
    MetricsAttributes,
)
from codemie.service.tools.tools_info_service import ToolsInfoService
from codemie.templates.agents.skill_generator_prompt import (
    SKILL_GENERATOR_CATEGORY,
    SKILL_GENERATOR_TEMPLATE,
    SKILL_GENERATOR_TEMPLATE_WITHOUT_TOOLS,
)


class SkillTool(BaseModel):
    name: str = Field(description="Correct name of tool from provided available options")
    label: str = Field(description="Correct label of tool from provided available options")


class SkillToolkit(BaseModel):
    toolkit: str = Field(description="Correct name of toolkit from provided available options")
    tools: list[SkillTool] = Field(description="Correct list of necessary tools from existing toolkit options")


class SkillDetails(BaseModel):
    """Structure for LLM structured output."""

    name: str = Field(description="A concise skill identifier in kebab-case format")
    description: str = Field(
        description="A brief description starting with 'You must use this skill when ...' phrasing"
    )
    instructions: str = Field(description="Comprehensive skill instructions in Markdown format")
    categories: list[str] = Field(
        default_factory=list,
        description="Up to 3 category values that define the skill's domain",
    )
    toolkits: list[SkillToolkit] = Field(
        default=[],
        description="List of toolkits with their tools required by this skill",
    )


HELP_MESSAGE = "Try again with a different input text or model."

# All available skill category values to pass to the LLM
_SKILL_CATEGORIES = [c.value for c in SkillCategory]


class SkillGeneratorService:
    """Service for generating skill details from user input."""

    @classmethod
    def generate_skill_details(
        cls,
        text: str,
        user: User,
        llm_model: Optional[str] = None,
        include_tools: bool = True,
        request_id: Optional[str] = None,
    ) -> SkillGeneratorResponse:
        """
        Generate skill details from user input text.

        Args:
            text: User input describing the desired skill
            user: User requesting the generation
            llm_model: Optional LLM model to use for generation
            include_tools: Whether to include toolkit suggestions
            request_id: Optional request ID for tracking

        Returns:
            SkillGeneratorResponse: Generated skill details
        """
        if not llm_model:
            llm_model = llm_service.default_llm_model

        try:
            base_template = SKILL_GENERATOR_TEMPLATE if include_tools else SKILL_GENERATOR_TEMPLATE_WITHOUT_TOOLS

            llm = get_llm_by_credentials(llm_model=llm_model, request_id=request_id)
            if not llm:
                raise RuntimeError(f"LLM model '{llm_model}' not found")

            chain_input: dict = {}

            # Inject toolkits if requested
            if include_tools:
                toolkits = ToolsInfoService.get_tools_info(user=user)
                toolkits = cls._transform_toolkits(toolkits)
                chain_input["toolkits"] = toolkits

            # Inject skill categories
            chain_input["categories"] = SKILL_GENERATOR_CATEGORY.format(
                include_categories=True,
                categories=_SKILL_CATEGORIES,
            )

            chain = base_template | llm.with_structured_output(SkillDetails)
            response: SkillDetails = chain.invoke({**chain_input, "text": text})  # pyright: ignore

            categories = cls._validate_categories(response.categories)

            send_log_metric(
                name=SKILL_GENERATOR_TOTAL_METRIC,
                attributes={
                    MetricsAttributes.LLM_MODEL: llm_model,
                    MetricsAttributes.USER_ID: logging_user_id.get("-"),
                    MetricsAttributes.USER_NAME: current_user_email.get("-"),
                },
            )

            return SkillGeneratorResponse(
                name=response.name,
                description=response.description,
                instructions=response.instructions,
                categories=categories,
                toolkits=response.toolkits if include_tools else [],
            )

        except Exception as e:
            logger.error(f"Failed to generate skill details: {e}", exc_info=True)
            send_log_metric(
                name=SKILL_GENERATOR_ERRORS_METRIC,
                attributes={
                    MetricsAttributes.LLM_MODEL: llm_model or "default",
                    MetricsAttributes.USER_ID: logging_user_id.get("-"),
                    MetricsAttributes.USER_NAME: current_user_email.get("-"),
                },
            )
            raise ExtendedHTTPException(
                code=500,
                message="Failed to generate skill details",
                details=f"An error occurred while generating skill details: {str(e)}",
                help=HELP_MESSAGE,
            )

    @classmethod
    def _validate_categories(cls, categories: list[str]) -> list[str]:
        """Validate and filter categories to only include valid SkillCategory values."""
        valid_values = {c.value for c in SkillCategory}
        validated = [c for c in categories if c in valid_values]
        # Enforce maximum of 3 categories
        return validated[:3]

    @classmethod
    def _filter_keep_toolkits(cls, toolkits: list) -> list:
        """Filter out toolkits where all tools have 'keep' action."""
        filtered = []
        for toolkit in toolkits:
            non_keep = [t for t in toolkit.tools if t.action != RecommendationAction.Keep]
            if non_keep:
                toolkit.tools = non_keep
                filtered.append(toolkit)
        return filtered

    @classmethod
    def _apply_field_category_recommendations(cls, fields: list) -> None:
        """Validate and update category recommendations against SkillCategory enum."""
        for field in fields:
            if field.name == "categories":
                recommended = field.recommended or []
                cats = [recommended] if isinstance(recommended, str) else recommended
                field.recommended = cls._validate_categories(cats)

    @classmethod
    def refine_skill_details(
        cls,
        user: User,
        request_id: str | None,
        name: str | None = None,
        description: str | None = None,
        instructions: str | None = None,
        categories: list[str] | None = None,
        toolkits: list[AssistantToolkit] | None = None,
        refine_prompt: str | None = None,
        llm_model: str | None = None,
    ) -> RefineGeneratorResponse:
        """
        Refine skill details using AI and return field/toolkit recommendations.

        Args:
            user: User requesting the refinement
            request_id: Optional request ID for tracking
            name: Current skill name
            description: Current skill description
            instructions: Current skill instructions
            categories: Current skill categories
            toolkits: Current skill toolkits
            refine_prompt: Optional user instructions to guide refinement
            llm_model: Optional LLM model to use

        Returns:
            RefineGeneratorResponse: Field and toolkit recommendations
        """
        from codemie.service.assistant_generator_service import PromptGeneratorChain
        from codemie.templates.agents.assistant_generator_prompt import REFINE_TOOLKITS_PROMPT_TEMPLATE
        from codemie.templates.agents.skill_generator_prompt import (
            SKILL_GENERATOR_CATEGORY,
            SKILL_REFINE_INPUT_TEMPLATE,
            SKILL_REFINE_PROMPT_TEMPLATE,
            SKILL_USER_REFINE_PROMPT,
        )

        if not llm_model:
            llm_model = llm_service.default_llm_model

        try:
            chain = PromptGeneratorChain.from_prompt_template(SKILL_REFINE_PROMPT_TEMPLATE, request_id, llm_model)
            chain.add_toolkits(user, True, REFINE_TOOLKITS_PROMPT_TEMPLATE, toolkits)

            result = chain.invoke_with_model(
                RefineGeneratorResponse,
                {
                    "text": SKILL_REFINE_INPUT_TEMPLATE.format(
                        name=name or "",
                        description=description or "",
                        categories=categories or [],
                        instructions=instructions or "",
                        toolkits=toolkits or [],
                    ),
                    "categories": SKILL_GENERATOR_CATEGORY.format(
                        include_categories=True,
                        categories=_SKILL_CATEGORIES,
                    ),
                    "user_refine_instructions": SKILL_USER_REFINE_PROMPT.format(
                        refine_prompt=refine_prompt or "",
                    ),
                },
            )

            # Validate categories against SkillCategory enum
            cls._apply_field_category_recommendations(result.fields)

            # Filter keep actions and empty context
            result.fields = [f for f in result.fields if f.action != RecommendationAction.Keep]
            result.toolkits = cls._filter_keep_toolkits(result.toolkits)
            result.context = []

            return result

        except ExtendedHTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to refine skill details: {e}", exc_info=True)
            send_log_metric(
                name=SKILL_GENERATOR_ERRORS_METRIC,
                attributes={
                    MetricsAttributes.LLM_MODEL: llm_model or "default",
                    MetricsAttributes.USER_ID: logging_user_id.get("-"),
                    MetricsAttributes.USER_NAME: current_user_email.get("-"),
                },
            )
            raise ExtendedHTTPException(
                code=500,
                message="Failed to refine skill details",
                details=f"An error occurred while refining skill details: {str(e)}",
                help=HELP_MESSAGE,
            )

    @classmethod
    def _transform_toolkits(cls, toolkits: list[dict]) -> list[dict]:
        """Transform toolkit info to the format expected by the prompt."""
        result = []
        for toolkit in toolkits:
            tools_with_description = [
                {
                    "name": tool.get("name", ""),
                    "label": tool.get("label", ""),
                    "description": tool.get("user_description", ""),
                }
                for tool in toolkit.get("tools", [])
                if tool.get("user_description") is not None
            ]
            if tools_with_description:
                result.append({"toolkit": toolkit.get("toolkit", ""), "tools": tools_with_description})
        return result
