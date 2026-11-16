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

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from codemie.configs.llm_config import CostConfig, LLMModel, LlmRouterOption, ModelStatus, ModelType


class AdminModelCost(BaseModel):
    input: float
    output: float


class AdminLLMModelResponse(BaseModel):
    """Display fields only; provider configuration must never enter this response."""

    base_name: str
    label: str
    model_type: ModelType
    enabled: bool
    status: ModelStatus
    provider: str | None = None
    router_type: Literal["switchyard", "litellm_auto"] | None = None
    is_premium: bool | None = None
    multimodal: bool | None = None
    supports_tools: bool | None = None
    supports_image_generation: bool = False
    cost: AdminModelCost | None = None
    updated_by_name: str | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_catalog(cls, model: LLMModel | LlmRouterOption, model_type: ModelType) -> "AdminLLMModelResponse":
        is_chat = model_type == ModelType.CHAT
        if isinstance(model, LLMModel):
            router_type = "litellm_auto" if model.is_declared_litellm_router() else None
            supports_tools = model.features.tools if is_chat and model.features else None
            cost: CostConfig | None = model.cost
        else:
            router_type = model.router_type
            supports_tools = model.supports_tools
            cost = None
        return cls(
            base_name=model.base_name,
            label=model.label or model.base_name,
            model_type=model_type,
            enabled=model.enabled,
            status=model.status or ModelStatus.ENABLED,
            provider=model.provider.value if model.provider else None,
            router_type=router_type,
            is_premium=model.is_premium,
            multimodal=model.multimodal if is_chat else None,
            supports_tools=supports_tools,
            supports_image_generation=model.supports_image_generation
            if isinstance(model, LLMModel) and is_chat
            else False,
            cost=AdminModelCost(input=cost.input, output=cost.output) if cost else None,
        )
