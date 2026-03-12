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
Service to generate assistant details from user input text.
"""

from typing import Any, Optional, Self

from langchain_core.language_models import BaseLanguageModel
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables.config import RunnableConfig
from pydantic import BaseModel, Field

from codemie.configs.logger import current_user_email, logger, logging_user_id
from codemie.core.dependecies import get_llm_by_credentials
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.assistant import Assistant, QualityValidationResult
from codemie.rest_api.models.assistant_generator import (
    AssistantContext,
    AssistantGeneratorResponse,
    PromptGeneratorResponse,
    RecommendationAction,
    RefineGeneratorResponse,
)
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.security.user import User
from codemie.service.assistant.category_service import category_service
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.monitoring.base_monitoring_service import send_log_metric
from codemie.service.monitoring.metrics_constants import (
    ASSISTANT_GENERATOR_ERRORS_METRIC,
    ASSISTANT_GENERATOR_TOTAL_METRIC,
    MARKETPLACE_ASSISTANT_VALIDATION_ERROR_METRIC,
    MARKETPLACE_ASSISTANT_VALIDATION_FAILED_METRIC,
    MARKETPLACE_ASSISTANT_VALIDATION_SUCCESS_METRIC,
    PROMPT_GENERATOR_ERRORS_METRIC,
    PROMPT_GENERATOR_TOTAL_METRIC,
    MetricsAttributes,
)
from codemie.service.tools.tools_info_service import ToolsInfoService
from codemie.templates.agents.assistant_generator_prompt import (
    ASSISTANT_GENERATOR_CATEGORY,
    ASSISTANT_GENERATOR_TEMPLATE,
    ASSISTANT_GENERATOR_TEMPLATE_WITHOUT_TOOLS,
    PROMPT_REFINE_TEMPLATE,
    PROMPT_REFINE_USER_INSTRUCTIONS,
    REFINE_CONTEXT_PROMPT_TEMPLATE,
    REFINE_GENERATOR_RESPONSE_TEMPLATE_EXAMPLE,
    REFINE_PROMPT_TEMPLATE,
    REFINE_TOOLKITS_PROMPT_TEMPLATE,
)
from codemie.workflows.assistant_generator.assistant_validation_workflow import AssistantValidationWorkflow


class AssistantTool(BaseModel):
    name: str = Field(description="Correct name of tool from provided available options")
    label: str = Field(description="Correct label of tool from provided available options")


class AssistantToolkit(BaseModel):
    toolkit: str = Field(description="Correct name of toolkit from provided available options")
    tools: list[AssistantTool] = Field(description="Correct list of necessary tools from existing toolkits options")


class AssistantDetails(BaseModel):
    """Structure for LLM response."""

    name: str = Field(description="A concise, professional name for the assistant")
    description: str = Field(
        description="A comprehensive description of the assistant's purpose, capabilities, and domain expertise"
    )
    categories: list[str] = Field(
        default_factory=list,
        description="A list of classifications that define the assistant's primary areas of focus or domain use cases.",
    )
    conversation_starters: list[str] = Field(
        description="Four engaging conversation starters that "
        "showcase different aspects of the assistant's capabilities"
    )
    system_prompt: str = Field(
        description="A comprehensive system prompt that guides the assistant's behavior, knowledge, and tone"
    )
    toolkits: list[AssistantToolkit] = Field(
        default=[], description="List of toolkits with their tools that should be used by the assistant"
    )


class PromptDetails(BaseModel):
    """Structure for LLM response."""

    system_prompt: str = Field(
        description="A comprehensive system prompt that guides the assistant's behavior, knowledge, and tone"
    )


class RefinePromptDetails(BaseModel):
    name: str | None = None
    description: str | None = None
    categories: list[str] | None = None
    system_prompt: str | None = None
    conversation_starters: list[str] | None = None
    toolkits: list[AssistantToolkit] | None = None
    context: list[AssistantContext] | None = None


HELP_MESSAGE = "Try again with a different input text or model."


class AssistantGeneratorService:
    """Service for generating assistant details from user input."""

    @classmethod
    def generate_assistant_details(
        cls,
        text: str,
        user: User,
        llm_model: Optional[str] = llm_service.default_llm_model,
        include_tools: bool = True,
        include_categories: bool = True,
        request_id: Optional[str] = None,
    ) -> AssistantGeneratorResponse:
        """
        Generate assistant details from user input text.
        Args:
            text: User input describing the desired assistant
            llm_model: Optional LLM model to use for generation
            request_id: Optional request ID for tracking
        Returns:
            AssistantGeneratorResponse: Generated assistant details
        """
        try:
            base_template = (
                ASSISTANT_GENERATOR_TEMPLATE if include_tools else ASSISTANT_GENERATOR_TEMPLATE_WITHOUT_TOOLS
            )

            chain = PromptGeneratorChain.from_prompt_template(base_template, request_id, llm_model)

            chain.add_toolkits(user, include_tools)
            chain.add_categories(include_categories=include_categories)

            response = chain.invoke_with_model(AssistantDetails, {"text": text})

            # Ensure we have exactly 4 conversation starters
            conversation_starters = cls._validate_conversation_starters(response.conversation_starters)

            categories = cls._validate_categories(response.categories) if response.categories else []

            # Send metrics for successful generation
            send_log_metric(
                name=ASSISTANT_GENERATOR_TOTAL_METRIC,
                attributes={
                    MetricsAttributes.LLM_MODEL: llm_model or "default",
                    MetricsAttributes.USER_ID: logging_user_id.get("-"),
                    MetricsAttributes.USER_NAME: current_user_email.get("-"),
                },
            )

            # Create the response
            return AssistantGeneratorResponse(
                name=response.name,
                description=response.description,
                conversation_starters=conversation_starters,
                system_prompt=response.system_prompt,
                toolkits=response.toolkits if include_tools else [],
                categories=categories,
            )

        except Exception as e:
            logger.error(f"Failed to generate assistant details: {e}", exc_info=True)
            send_log_metric(
                name=ASSISTANT_GENERATOR_ERRORS_METRIC,
                attributes={
                    MetricsAttributes.LLM_MODEL: llm_model or "default",
                    MetricsAttributes.USER_ID: logging_user_id.get("-"),
                    MetricsAttributes.USER_NAME: current_user_email.get("-"),
                },
            )
            raise ExtendedHTTPException(
                code=500,
                message="Failed to generate assistant details",
                details=f"An error occurred while generating assistant details: {str(e)}",
                help=HELP_MESSAGE,
            )

    @classmethod
    def generate_assistant_prompt(
        cls,
        user: User,
        text: str | None = None,
        existing_prompt: str | None = None,
        project: str | None = None,
        llm_model: str = llm_service.default_llm_model,
        request_id: str | None = None,
    ) -> PromptGeneratorResponse:
        """
        Generate or refine assistant system prompt using AI.

        This method operates in two modes:
        1. User Instructions Mode: If 'text' is provided, uses it as refinement instructions
        2. Automatic Quality Review: If 'text' is None/empty, performs automatic quality review

        Args:
            user: User requesting the generation
            text: User input/instructions for generating or refining the prompt
            existing_prompt: Current system prompt to refine (None for new generation)
            project: Optional project ID for datasource filtering
            llm_model: LLM model to use for generation
            request_id: Request ID for tracking

        Returns:
            PromptGeneratorResponse: Generated/refined system prompt
        """
        try:
            # Build chain with prompt refine template
            chain = PromptGeneratorChain.from_prompt_template(
                PROMPT_REFINE_TEMPLATE,
                request_id,
                llm_model,
            )

            # Add datasource context (fetches available datasources internally)
            chain.add_context(user, project, None)

            # Add refine instructions (handles both modes: user instructions or automatic review)
            chain.add_prompt_refine_instructions(text)

            # Invoke with PromptDetails model (just system_prompt field)
            response = chain.invoke_with_model(
                PromptDetails,
                {"system_prompt": existing_prompt or "No existing prompt. Create an intelligent, friendly chatbot"},
            )

            logger.info(
                f"Generated/refined prompt: "
                f"had_existing={existing_prompt is not None}, "
                f"had_instructions={text is not None}, "
                f"prompt_length={len(response.system_prompt)}"
            )

            # Send success metrics
            send_log_metric(
                name=PROMPT_GENERATOR_TOTAL_METRIC,
                attributes={
                    MetricsAttributes.LLM_MODEL: llm_model or "default",
                    MetricsAttributes.USER_ID: logging_user_id.get("-"),
                    MetricsAttributes.USER_NAME: current_user_email.get("-"),
                },
            )

            # Return response with refined system_prompt
            return PromptGeneratorResponse(
                system_prompt=response.system_prompt,
            )

        except Exception as e:
            logger.error(f"Failed to generate/refine prompt: {e}", exc_info=True)
            send_log_metric(
                name=PROMPT_GENERATOR_ERRORS_METRIC,
                attributes={
                    MetricsAttributes.LLM_MODEL: llm_model or "default",
                    MetricsAttributes.USER_ID: logging_user_id.get("-"),
                    MetricsAttributes.USER_NAME: current_user_email.get("-"),
                },
            )
            raise ExtendedHTTPException(
                code=500,
                message="Failed to generate/refine system prompt",
                details=f"An error occurred: {str(e)}",
                help=HELP_MESSAGE,
            )

    @classmethod
    def generate_refine_prompt(
        cls,
        user: User,
        request_id: str,
        refine_details: RefinePromptDetails,
        refine_prompt: str | None = None,
        project: str | None = None,
        _include_tools: bool = False,
        include_context: bool = True,
        include_categories: bool = False,
        llm_model: str = llm_service.default_llm_model,
    ) -> RefineGeneratorResponse:
        try:
            chain = PromptGeneratorChain.from_prompt_template(REFINE_PROMPT_TEMPLATE, request_id, llm_model)

            chain.add_toolkits(user, True, REFINE_TOOLKITS_PROMPT_TEMPLATE, refine_details.toolkits)
            chain.add_context(user, project, refine_details.context)
            chain.add_categories(refine_details.categories, include_categories)
            chain.add_refine_prompt(refine_prompt)

            result = chain.invoke_with_model(
                RefineGeneratorResponse,
                {
                    "text": REFINE_GENERATOR_RESPONSE_TEMPLATE_EXAMPLE.format(
                        name=refine_details.name,
                        description=refine_details.description,
                        categories=refine_details.categories,
                        system_prompt=refine_details.system_prompt,
                        conversation_starters=refine_details.conversation_starters,
                        toolkits=refine_details.toolkits,
                        context=refine_details.context,
                    ),
                },
            )

            send_log_metric(
                name=ASSISTANT_GENERATOR_TOTAL_METRIC,
                attributes={
                    MetricsAttributes.LLM_MODEL: llm_model or "default",
                    MetricsAttributes.USER_ID: logging_user_id.get("-"),
                    MetricsAttributes.USER_NAME: current_user_email.get("-"),
                },
            )

            # Get original values from refine_details for comparison
            original_values = {
                "name": refine_details.name,
                "description": refine_details.description,
                "categories": refine_details.categories,
                "system_prompt": refine_details.system_prompt,
                "conversation_starters": refine_details.conversation_starters,
            }

            for field in result.fields:
                cls._process_field_recommendation(field, original_values)

            # Filter out fields with "keep" action from the response
            result.fields = [field for field in result.fields if field.action != RecommendationAction.Keep]

            # Filter out toolkits that have all tools with "keep" action
            result.toolkits = cls._filter_keep_toolkits(result.toolkits)

            # Filter out context items with "keep" action
            result.context = [ctx for ctx in result.context if ctx.action != RecommendationAction.Keep]

            # Filter out invalid delete recommendations for datasources that aren't currently enabled
            current_datasource_names = [ctx.name for ctx in refine_details.context] if refine_details.context else []
            result.context = cls._filter_invalid_delete_recommendations(result.context, current_datasource_names)

            # Validate that all suggested datasources actually exist in the project
            result.context = cls._validate_context_recommendations(result.context, user, project)

            return result

        except Exception as e:
            logger.error(f"Failed to refine assistant: {e}", exc_info=True)

            send_log_metric(
                name=ASSISTANT_GENERATOR_ERRORS_METRIC,
                attributes={
                    MetricsAttributes.LLM_MODEL: llm_model or "default",
                    MetricsAttributes.USER_ID: logging_user_id.get("-"),
                    MetricsAttributes.USER_NAME: current_user_email.get("-"),
                },
            )
            raise ExtendedHTTPException(
                code=500,
                message="Failed to refine assistant",
                details=f"An error occurred while refining: {str(e)}",
                help=HELP_MESSAGE,
            )

    @classmethod
    def validate_assistant_for_publish(
        cls,
        assistant: Assistant,
        user: User,
        request_id: Optional[str] = None,
        llm_model: str = llm_service.default_llm_model,
    ) -> QualityValidationResult:
        """
        Validate an assistant for marketplace publication using LangGraph workflow (v2).

        This method uses the new AI-Based Marketplace Assistant Validation Workflow
        with three-phase validation (system prompt, tools via RAG, context) and
        LLM-driven verification with retry logic.

        Args:
            assistant: The assistant to validate
            user: The user requesting validation (for context access)
            request_id: Optional request ID for tracking and logging
            llm_model: LLM model to use for validation (defaults to default_llm_model)

        Returns:
            QualityValidationResult: Structured validation decision with reasoning

        Raises:
            ExtendedHTTPException: If validation fails due to service errors
        """
        try:
            logger.info(f"Starting marketplace validation workflow for assistant: {assistant.name}")

            # Initialize and execute workflow
            workflow = AssistantValidationWorkflow(llm_model=llm_model, request_id=request_id)
            decision, recommendations = workflow.validate(
                assistant=assistant,
                user=user,
                request_id=request_id,
            )

            # Build QualityValidationResult from workflow output
            validation_result = QualityValidationResult(
                decision=decision,
                recommendations=recommendations if decision == "reject" else None,
            )

            # Track metrics
            if validation_result.decision == "accept":
                send_log_metric(
                    name=MARKETPLACE_ASSISTANT_VALIDATION_SUCCESS_METRIC,
                    attributes={
                        MetricsAttributes.LLM_MODEL: llm_model,
                        MetricsAttributes.USER_ID: user.id,
                        MetricsAttributes.USER_NAME: user.name,
                        MetricsAttributes.USER_EMAIL: user.username,
                        MetricsAttributes.ASSISTANT_ID: assistant.id,
                    },
                )
            elif validation_result.decision == "reject":
                send_log_metric(
                    name=MARKETPLACE_ASSISTANT_VALIDATION_FAILED_METRIC,
                    attributes={
                        MetricsAttributes.LLM_MODEL: llm_model,
                        MetricsAttributes.USER_ID: user.id,
                        MetricsAttributes.USER_NAME: user.name,
                        MetricsAttributes.USER_EMAIL: user.username,
                        MetricsAttributes.ASSISTANT_ID: assistant.id,
                    },
                )

            logger.info(f"Marketplace validation for assistant {assistant.name} completed: decision={decision}")
            return validation_result

        except Exception as e:
            logger.error(f"Marketplace validation for assistant {assistant.name} failed: error={str(e)}")
            send_log_metric(
                name=MARKETPLACE_ASSISTANT_VALIDATION_ERROR_METRIC,
                attributes={
                    MetricsAttributes.LLM_MODEL: llm_model,
                    MetricsAttributes.USER_ID: user.id,
                    MetricsAttributes.USER_NAME: user.name,
                    MetricsAttributes.USER_EMAIL: user.username,
                    MetricsAttributes.ASSISTANT_ID: assistant.id,
                },
            )

            raise ExtendedHTTPException(
                code=500,
                message="Failed to validate assistant for publication (v2 workflow)",
                details=f"An error occurred during v2 quality validation: {str(e)}",
                help="Please try again later. If the issue persists, contact support.",
            )

    @classmethod
    def _process_field_recommendation(cls, field, original_values: dict[str, Any]):
        """
        Process a single field recommendation by validating and filtering redundant suggestions.

        Args:
            field: FieldRecommendation object to process
            original_values: Dictionary of original values from refine_details
        """
        # Validate specific field types
        cls._validate_field_recommendation(field)

        # Filter redundant recommendations
        cls._filter_redundant_recommendation(field, original_values)

    @classmethod
    def _validate_field_recommendation(cls, field):
        """
        Validate and normalize field recommendations based on field type.

        Args:
            field: FieldRecommendation object to validate
        """
        match field.name:
            case "conversation_starters":
                recommended = field.recommended or []
                conversation_starters = [recommended] if isinstance(recommended, str) else recommended
                field.recommended = cls._validate_conversation_starters(conversation_starters)
            case "categories":
                recommended = field.recommended or []
                categories = [recommended] if isinstance(recommended, str) else recommended
                field.recommended = cls._validate_categories_for_refine(categories)

    @classmethod
    def _filter_keep_toolkits(cls, toolkits: list) -> list:
        """
        Filter out toolkits that have all tools with "keep" action.
        Only return toolkits that have at least one tool with "change" or "delete" action.

        Args:
            toolkits: List of ToolkitRecommendation objects

        Returns:
            Filtered list of toolkits with only non-keep recommendations
        """
        filtered_toolkits = []
        for toolkit in toolkits:
            # Filter out tools with "keep" action
            non_keep_tools = [tool for tool in toolkit.tools if tool.action != RecommendationAction.Keep]
            # Only include toolkit if it has tools that need action
            if non_keep_tools:
                toolkit.tools = non_keep_tools
                filtered_toolkits.append(toolkit)
        return filtered_toolkits

    @classmethod
    def _validate_context_recommendations(cls, context_recommendations: list, user: User, project: str | None) -> list:
        """
        Validate context recommendations by filtering out LLM-hallucinated datasource names.

        Ensures all suggested datasources actually exist in the project and are accessible
        to the user. Logs warnings for any hallucinated datasources.

        Args:
            context_recommendations: List of ContextRecommendation objects from LLM
            user: Current user for access control
            project: Project name to filter datasources

        Returns:
            Filtered list containing only recommendations for datasources that actually exist
        """
        if not context_recommendations:
            return []

        # Fetch all available datasources the user has access to
        available_indexes = IndexInfo.filter_for_user(user, project or user.current_project)
        valid_datasource_names = {idx.repo_name for idx in available_indexes}

        # Filter and log invalid recommendations
        validated_recommendations = []
        for rec in context_recommendations:
            if rec.name in valid_datasource_names:
                validated_recommendations.append(rec)
            else:
                logger.warning(
                    f"Filtered out non-existent datasource: name='{rec.name}', "
                    f"action={rec.action}, reason='{rec.reason}'"
                )

        return validated_recommendations

    @classmethod
    def _filter_invalid_delete_recommendations(
        cls, context_recommendations: list, current_datasources: list[str] | None
    ) -> list:
        """
        Filter out delete recommendations for datasources that aren't currently enabled.

        Prevents LLM from suggesting deletion of datasources that aren't in the assistant's
        current context. Only datasources that are currently enabled can be deleted.

        Args:
            context_recommendations: List of ContextRecommendation objects from LLM
            current_datasources: List of currently enabled datasource names

        Returns:
            Filtered list without invalid delete recommendations
        """
        if not context_recommendations:
            return []

        current_datasource_set = set(current_datasources) if current_datasources else set()

        # Filter out invalid delete recommendations
        filtered_recommendations = []
        for rec in context_recommendations:
            # If it's a DELETE recommendation, verify the datasource is actually enabled
            if rec.action == RecommendationAction.Delete:
                if rec.name in current_datasource_set:
                    filtered_recommendations.append(rec)
                else:
                    logger.warning(
                        f"Filtered out invalid delete recommendation for non-enabled datasource: "
                        f"name='{rec.name}', reason='{rec.reason}'"
                    )
            else:
                # ADD or CHANGE recommendations are always valid
                # (will be validated by _validate_context_recommendations)
                filtered_recommendations.append(rec)

        return filtered_recommendations

    @classmethod
    def _filter_redundant_recommendation(cls, field, original_values: dict[str, Any]):
        """
        Filter out redundant recommendations where suggested value matches original.

        Args:
            field: FieldRecommendation object to check
            original_values: Dictionary of original values from refine_details
        """
        if field.action == RecommendationAction.Keep:
            field.recommended = None
        elif field.action in (RecommendationAction.Change, RecommendationAction.Delete):
            original_value = original_values.get(field.name)
            if cls._is_recommendation_same_as_original(original_value, field.recommended):
                field.action = RecommendationAction.Keep
                field.recommended = None

    @staticmethod
    def _validate_conversation_starters(conversation_starters: list[str]) -> list[str]:
        """
        Ensure we have exactly 4 conversation starters.
        Args:
            conversation_starters: List of conversation starters from LLM response
        Returns:
            List containing exactly 4 conversation starters
        """
        defaults = [
            "Tell me more about what you can do",
            "How can you help me?",
            "What features do you have?",
            "Show me an example of your capabilities",
        ]

        # Not a list or empty list
        if not conversation_starters or not isinstance(conversation_starters, list):
            return defaults

        # Too many items - truncate
        if len(conversation_starters) > 4:
            return conversation_starters[:4]

        # Just right
        return conversation_starters

    @staticmethod
    def _validate_categories(categories: list[str]) -> list[str]:
        """
        Ensure we have exactly 3 or less.

        Args:
            categories: List of categories from LLM response

        Returns:
            List containing exactly 3 or less categories.
        """

        validated = category_service.validate_category_ids(categories)
        return validated[:3]

    @staticmethod
    def _validate_categories_for_refine(categories: list[str]) -> list[str]:
        """
        Ensure we have exactly 3 or less valid categories for refine operation.
        Filters out invalid category IDs instead of raising an error.

        Args:
            categories: List of categories from LLM response

        Returns:
            List containing exactly 3 or less valid categories.
            Invalid category IDs are filtered out gracefully.
        """

        validated = category_service.filter_valid_category_ids(categories)
        return validated[:3]

    @staticmethod
    def _is_recommendation_same_as_original(original: Any, recommended: Any) -> bool:
        """
        Check if the recommended value is the same as the original value.
        Used to filter out redundant suggestions where LLM recommends the same value.

        Args:
            original: Original value from user's assistant draft
            recommended: Recommended value from LLM

        Returns:
            True if values are the same (no actual change), False otherwise
        """
        # Both None
        if original is None and recommended is None:
            return True

        # One is None and the other is not
        if original is None or recommended is None:
            return False

        # Both are lists - compare after sorting for order-independent comparison
        if isinstance(original, list) and isinstance(recommended, list):
            # Sort both lists for comparison (if items are sortable)
            try:
                return sorted(original) == sorted(recommended)
            except TypeError:
                # If items aren't sortable, compare as-is
                return original == recommended

        # Direct comparison for strings and other types
        return original == recommended


class PromptGeneratorChain:
    def __init__(self, base_template: PromptTemplate, base_llm: BaseLanguageModel):
        self._base_template = base_template
        self._base_llm = base_llm
        self._chain_input = {}

    @classmethod
    def from_prompt_template(
        cls,
        prompt_template: PromptTemplate,
        request_id: str | None = None,
        llm_model: str | None = None,
    ) -> Self:
        if not llm_model:
            llm_model = llm_service.default_llm_model

        llm = get_llm_by_credentials(llm_model=llm_model, request_id=request_id)
        if not llm:
            raise RuntimeError(f"llm {llm_model} model not found")

        return cls(prompt_template, llm)  # pyright: ignore

    def invoke_with_model[F: BaseModel](
        self,
        base_model: type[F],
        input: dict[Any, Any],
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> F:
        chain = self._base_template | self._base_llm.with_structured_output(base_model)
        result = chain.invoke({**self._chain_input, **input}, config, **kwargs)
        return result  # pyright: ignore

    def add_categories(self, user_categories: list[str] | None = None, include_categories: bool = True):
        if not include_categories:
            return

        categories = category_service.get_categories()
        categories = [
            {
                "id": category.id,
                "description": category.description,
            }
            for category in categories
        ]

        self._chain_input["categories"] = ASSISTANT_GENERATOR_CATEGORY.format(
            include_categories=include_categories,
            categories=categories,
            user_categories=user_categories,
        )

    @classmethod
    def _transform_toolkits(cls, toolkits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Transform list of toolkit dictionaries to the requested format.

        Args:
            toolkits: List of toolkit dictionaries

        Returns:
            List of dictionaries with toolkit name and tools information
        """
        result = []

        for toolkit in toolkits:
            # Filter tools that have descriptions
            tools_with_description = [
                {
                    "name": tool.get("name", ""),
                    "label": tool.get("label", ""),
                    "description": tool.get("user_description", ""),
                }
                for tool in toolkit.get("tools", [])
                if tool.get("user_description") is not None
            ]

            # Only include toolkits that have tools with descriptions
            if tools_with_description:
                result.append({"toolkit": toolkit.get("toolkit", ""), "tools": tools_with_description})

        return result

    def add_toolkits(
        self,
        user: User,
        include_tools: bool,
        prompt_template: PromptTemplate | None = None,
        assistant_toolkits: list[AssistantToolkit] | None = None,
    ):
        if not include_tools:
            return

        toolkits = ToolsInfoService.get_tools_info(user=user)
        toolkits = self._transform_toolkits(toolkits)
        if not prompt_template:
            self._chain_input["toolkits"] = toolkits
            return

        # Build toolkit_aliases map directly from transformed toolkits
        toolkit_aliases = {
            toolkit["toolkit"]: [tool["name"] for tool in toolkit["tools"] if tool.get("name")]
            for toolkit in toolkits
            if toolkit.get("toolkit") and toolkit.get("tools")
        }

        prompt_input = {
            "toolkits": toolkits,
            "include_tools": include_tools,
            "toolkit_aliases": toolkit_aliases,
        }

        if assistant_toolkits:
            prompt_input["assistant_toolkits"] = [toolkit.model_dump() for toolkit in assistant_toolkits]

        self._chain_input["toolkits"] = prompt_template.format(**prompt_input)

    def add_context(self, user: User, project: str | None, context: list[AssistantContext] | None):
        """
        Add datasources context to the LLM prompt.

        Provides the LLM with:
        1. Currently enabled datasources in the assistant
        2. ALL available datasources for the project that the user has access to

        This allows the LLM to make informed recommendations about which datasources
        to add or remove based on what's currently enabled and what's actually available.

        Args:
            user: Current user for access control
            project: Project name to filter datasources
            context: Current assistant datasources (used to show what's already enabled)
        """
        # Extract currently enabled datasource names from the assistant's context
        current_datasources = [ctx.name for ctx in context] if context else []

        # Fetch ALL available datasources for the project that the user has access to
        indexes = IndexInfo.filter_for_user(user, project or user.current_project)

        available_datasources = [
            {
                "repo_name": idx.repo_name,
                "index_type": idx.index_type,
                "description": idx.description or "No description available",
            }
            for idx in indexes
        ]

        self._chain_input["context"] = REFINE_CONTEXT_PROMPT_TEMPLATE.format(
            include_context=True,
            context=available_datasources,
            current_datasources=current_datasources,
        )

    def add_refine_prompt(self, refine_prompt: str | None):
        """
        Add user's refine prompt to the chain input using USER_REFINE_PROMPT template.

        Args:
            refine_prompt: User's refinement instructions. If None or empty, a message indicating
                          no refine prompt was provided will be used.
        """
        from codemie.templates.agents.assistant_generator_prompt import USER_REFINE_PROMPT

        if refine_prompt:
            rendered_prompt = USER_REFINE_PROMPT.format(refine_prompt=refine_prompt)
        else:
            rendered_prompt = USER_REFINE_PROMPT.format(
                refine_prompt="No specific refine instructions provided by the user."
            )

        self._chain_input["user_refine_instructions"] = rendered_prompt

    def add_prompt_refine_instructions(self, refine_prompt: str | None):
        """
        Add user's refine instructions for system prompt generation.
        Similar to add_refine_prompt but for prompt-only refinement.

        Args:
            refine_prompt: User's refinement instructions (None for automatic review)
        """
        rendered_prompt = PROMPT_REFINE_USER_INSTRUCTIONS.format(refine_prompt=refine_prompt)
        self._chain_input["user_refine_instructions"] = rendered_prompt
