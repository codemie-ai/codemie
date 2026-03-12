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

from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, YamlConfigSettingsSource

from codemie.configs import logger
from codemie.configs.config import config


class CostConfig(BaseModel):
    input: float
    output: float
    input_cost_per_token_batches: Optional[float] = None
    output_cost_per_token_batches: Optional[float] = None
    cache_read_input_token_cost: Optional[float] = None
    cache_creation_input_token_cost: Optional[float] = None


class LLMProvider(Enum):
    """Enum to define the LLMProvider options"""

    AZURE_OPENAI = "azure_openai"
    AWS_BEDROCK = "aws_bedrock"
    GOOGLE_VERTEX_AI = "google_vertexai"
    ANTHROPIC = "anthropic"
    VERTEX_AI_ANTHROPIC = "vertex_ai-anthropic_models"


class LLMFeatures(BaseModel):
    streaming: Optional[bool] = True
    tools: Optional[bool] = True
    temperature: Optional[bool] = True
    parallel_tool_calls: Optional[bool] = False
    system_prompt: Optional[bool] = True
    max_tokens: Optional[bool] = True
    top_p: Optional[bool] = True


class ModelCategory(str, Enum):
    """Enum to define the categories where models can be used"""

    GLOBAL = "global"  # Global default model (replacing standalone default flag)
    CHAT = "chat"  # General conversation/chat completion
    CODE = "code"  # Code generation, analysis, etc.
    DOCUMENTATION = "documentation"  # Documentation generation
    SUMMARIZATION = "summarization"  # Text summarization tasks
    TRANSLATION = "translation"  # Translation between languages
    KNOWLEDGE_BASE = "knowledge_base"  # Knowledge base retrieval/embedding tasks
    WORKFLOW = "workflow"  # For workflow-related LLM tasks
    FILE_ANALYSIS = "file_analysis"  # For file content analysis
    REASONING = "reasoning"  # Deep reasoning tasks
    PLANNING = "planning"  # Planning and strategic thinking tasks


class ModelType(str, Enum):
    """Enum to define the type of LLM model"""

    CHAT = "chat"
    EMBEDDING = "embedding"


class ModelConfigurationSection(BaseModel):
    """Additional configuration options for the model that will be passed directly to providers"""

    client_headers: Optional[dict[str, list[str] | str]] = None


class LLMModel(BaseModel):
    base_name: str
    deployment_name: str
    label: Optional[str] = None
    multimodal: Optional[bool] = None
    react_agent: Optional[bool] = None
    enabled: bool
    provider: Optional[LLMProvider] = None
    default: Optional[bool] = False  # Backward compatibility for "default" field
    default_for_categories: list[ModelCategory] = Field(default_factory=list)
    cost: Optional[CostConfig] = None
    max_output_tokens: Optional[int] = None
    features: Optional[LLMFeatures] = LLMFeatures()
    configuration: Optional[ModelConfigurationSection] = None
    forbidden_for_web: Optional[bool] = (
        False  # Controls whether model should be hidden from web/UI (defaults to False - visible)
    )

    @model_validator(mode='after')
    def populate_default_field(self):
        """
        Populate default field based on default_for_categories and existing default value.
        """
        if ModelCategory.GLOBAL in self.default_for_categories:
            self.default = True
        return self

    def is_default_for(self, category: ModelCategory) -> bool:
        """Checks if the model is a default for the specified category."""
        return self.default_for_categories and category in self.default_for_categories


class LiteLLMModels(BaseModel):
    """Response model containing allowed models for a user"""

    chat_models: list[LLMModel] = Field(default_factory=list)
    embedding_models: list[LLMModel] = Field(default_factory=list)


class LLMYamlSettings(BaseSettings):
    yaml_file: Path

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,  # type: ignore[override]
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return init_settings, env_settings, YamlConfigSettingsSource(cls, init_settings.init_kwargs["yaml_file"])


class LLMConfig(LLMYamlSettings):
    llm_models: list[LLMModel]
    embeddings_models: list[LLMModel]


llm_config = LLMConfig(yaml_file=config.LLM_TEMPLATES_ROOT / f"llm-{config.MODELS_ENV}-config.yaml")

logger.info(
    f"LLMConfig initiated. Config={llm_config.yaml_file}. "
    f"LLMModels={llm_config.llm_models}. EmbeddingModels={llm_config.embeddings_models}"
)
