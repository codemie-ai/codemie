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


class RoutingMode(str, Enum):
    """Switchyard routing strategy."""

    SIGNAL = "signal"
    CLASSIFIER = "classifier"


class SwitchyardTuning(BaseModel):
    """Tunable Switchyard routing parameters. Defaults reproduce the previous hardcoded constants."""

    recent_window: int = 3
    classifier_base_threshold: float = 0.65
    classifier_threshold_step: float = 0.15
    signal_threshold: float = 0.0  # confidence_threshold for signal mode (was the 0.0 literal)
    classifier_threshold: float = 0.5  # confidence_threshold for classifier mode (was the 0.5 literal)
    classifier_model: str | None = None


class ModelSwitchyard(BaseModel):
    """One fully self-configuring Switchyard router declaration, living on the capable
    model. Unlike the previous `efficient + modes[]` shape (which forced a fixed
    "<capable>-switchyard-<efficient>-<mode>" base_name / "SY (<Mode>) <label>" label,
    auto-generated per mode), each entry here stands entirely on its own: a capable model can
    declare any number of these, each pointing at its own efficient target, mode, and identity
    — enabling e.g. two routers to different efficient targets, or custom naming, without
    collision."""

    base_name: str  # this router's own base_name — must not collide with any model's base_name
    label: str | None = None  # same optional/fallback semantics as LLMModel.label
    efficient: str  # base_name of the cheaper same-family model this router can fall down to
    mode: RoutingMode
    tuning: SwitchyardTuning | None = None  # optional partial override of the resolved tuning


class SwitchyardConfig(BaseModel):
    """Resolved routing configuration for a single generated router."""

    capable_model: str  # base_name of the capable model
    efficient_model: str  # base_name of the efficient model
    mode: RoutingMode
    tuning: SwitchyardTuning = Field(default_factory=SwitchyardTuning)


class LiteLLMRouterConfig(BaseModel):
    """Declares that this LLMModel's model_name is itself a LiteLLM auto-router
    (https://docs.litellm.ai/docs/proxy/auto_routing), not a concrete deployment. LiteLLM
    exposes no reliable API signal for this (no router_id, same model_name namespace as real
    deployments) — CodeMie must declare it explicitly, mirroring whatever is configured in
    LiteLLM's own proxy config.yaml for this model_name."""

    is_router: bool = True  # deliberately redundant with the field's mere presence on LLMModel
    # — kept as an object (not a bare bool on LLMModel) so there is a
    # place to add fields later without a breaking type change. Does
    # NOT reuse LLMModel.enabled, which already means something
    # different (this model's own enabled/disabled state).


class LLMRouter(BaseModel):
    """A virtual model that routes requests to actual capable/efficient models."""

    base_name: str
    label: str | None = None
    enabled: bool = True
    forbidden_for_web: bool | None = False
    switchyard: SwitchyardConfig


class LlmRouterOption(BaseModel):
    """REST-boundary projection of an LLMRouter. Not a domain model."""

    base_name: str
    label: str | None = None
    is_router: bool = True
    # Always True for enabled routers (get_allowed_router_options only returns enabled ones).
    # Clients (e.g. codemie-code) gate on this field to decide whether to show the model.
    enabled: bool = True
    multimodal: bool | None = None
    supports_tools: bool | None = None
    is_premium: bool | None = None


class LLMModel(BaseModel):
    base_name: str
    deployment_name: str
    label: Optional[str] = None
    multimodal: Optional[bool] = None
    react_agent: Optional[bool] = None
    enabled: bool
    supports_image_generation: bool = False
    provider: Optional[LLMProvider] = None
    default: Optional[bool] = False  # Backward compatibility for "default" field
    default_for_categories: list[ModelCategory] = Field(default_factory=list)
    cost: Optional[CostConfig] = None
    max_output_tokens: Optional[int] = None
    features: Optional[LLMFeatures] = LLMFeatures()
    configuration: Optional[ModelConfigurationSection] = None
    switchyard: list[ModelSwitchyard] = Field(default_factory=list)
    litellm_router: Optional[LiteLLMRouterConfig] = None
    forbidden_for_web: Optional[bool] = (
        False  # Controls whether model should be hidden from web/UI (defaults to False - visible)
    )
    api_version: Optional[str] = None  # Azure OpenAI specific: overrides global OPENAI_API_VERSION when set
    # Set only when a premium_models budget is configured; True if base_name matches LITELLM_PREMIUM_MODELS_ALIASES
    is_premium: Optional[bool] = None

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

    def is_declared_litellm_router(self) -> bool:
        """True when this model's config declares it as a LiteLLM auto-router (see
        LiteLLMRouterConfig). The single predicate shared by every "is this target itself a
        router" check in the codebase — build_switchyard_routers below (config-build-time,
        static YAML), engine.py::_router_on_router_role (runtime, live catalog defense-in-
        depth), and router_factory.py::_resolve_litellm_router (runtime resolution) — so they
        can't drift on what "declared as a router" means, even though each keeps its own
        reason for checking it at a different point in time."""
        return self.litellm_router is not None and self.litellm_router.is_router


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


def _try_build_switchyard_router(
    model: "LLMModel",
    entry: "ModelSwitchyard",
    by_name: dict[str, "LLMModel"],
    generated_names: set[str],
    global_tuning: "SwitchyardTuning",
) -> "LLMRouter | None":
    """Validate one model's switchyard entry and build its LLMRouter, or return None (after
    logging why) when the entry can't be turned into a router."""
    if entry.base_name in by_name:
        logger.error(
            f"Switchyard entry {entry.base_name!r} on {model.base_name!r} collides with an "
            f"existing model's base_name; skipping router generation."
        )
        return None
    if entry.base_name in generated_names:
        logger.error(
            f"Switchyard entry {entry.base_name!r} on {model.base_name!r} collides with a router "
            f"already generated in this config; skipping router generation."
        )
        return None
    efficient = by_name.get(entry.efficient)
    if efficient is None:
        logger.warning(
            f"Switchyard entry {entry.base_name!r} on {model.base_name!r} references unknown "
            f"efficient model {entry.efficient!r}; skipping router generation."
        )
        return None
    router_on_router = next(
        (
            role
            for role, target in (("capable", model), ("efficient", efficient))
            if target.is_declared_litellm_router()
        ),
        None,
    )
    if router_on_router is not None:
        logger.error(
            f"Switchyard entry {entry.base_name!r} on {model.base_name!r} has its {router_on_router} "
            f"model declared as a LiteLLM auto-router; router-on-router is not supported, skipping "
            f"router generation."
        )
        return None
    resolved_tuning = (
        global_tuning.model_copy(update={f: getattr(entry.tuning, f) for f in entry.tuning.model_fields_set})
        if entry.tuning is not None
        else global_tuning
    )
    if entry.mode == RoutingMode.CLASSIFIER and not resolved_tuning.classifier_model:
        logger.warning(
            f"Switchyard router {entry.base_name!r} on {model.base_name!r} declares classifier mode "
            f"but no classifier_model is configured (SWITCHYARD_CLASSIFIER_MODEL or per-router tuning); "
            f"skipping router generation."
        )
        return None
    return LLMRouter(
        base_name=entry.base_name,
        label=entry.label,
        enabled=model.enabled,
        forbidden_for_web=model.forbidden_for_web,
        switchyard=SwitchyardConfig(
            capable_model=model.base_name,
            efficient_model=efficient.base_name,
            mode=entry.mode,
            tuning=resolved_tuning,
        ),
    )


def build_switchyard_routers(models: list["LLMModel"]) -> list["LLMRouter"]:
    """Expand each model's `switchyard` declaration into first-class LLMRouter entries.

    Pure function of the given model list — takes the static YAML models (see
    LLMConfig.generate_switchyard_routers) or the live LiteLLM/DIAL catalog (see
    LLMService.get_llm_routers), so the same generation algorithm can run against
    whichever single model source is currently in effect for a given base_name.

    SWITCHYARD_ENABLED is the master switch. When off, generate no routers so the
    /llm_models catalog, llm_service.is_router_model, and the proxy engine all agree
    that no routers exist — otherwise the catalog would advertise routers the engine
    refuses to route, and clients would select a model that 404s downstream.
    (engine._get_switchyard_model_config also gates at request time as defense in depth.)
    """
    if not config.SWITCHYARD_ENABLED:
        return []
    by_name = {model.base_name: model for model in models}
    global_tuning = SwitchyardTuning(
        **({"classifier_model": config.SWITCHYARD_CLASSIFIER_MODEL} if config.SWITCHYARD_CLASSIFIER_MODEL else {})
    )
    generated: list[LLMRouter] = []
    generated_names: set[str] = set()
    for model in models:
        for entry in model.switchyard:
            router = _try_build_switchyard_router(model, entry, by_name, generated_names, global_tuning)
            if router is None:
                continue
            generated.append(router)
            generated_names.add(router.base_name)
    return generated


class LLMConfig(LLMYamlSettings):
    llm_models: list[LLMModel]
    embeddings_models: list[LLMModel]
    llm_routers: list[LLMRouter] = Field(default_factory=list)

    @model_validator(mode="after")
    def generate_switchyard_routers(self) -> "LLMConfig":
        """Expand each model's `switchyard` declaration into first-class LLMRouter entries."""
        self.llm_routers = [*self.llm_routers, *build_switchyard_routers(self.llm_models)]
        return self


llm_config = LLMConfig(yaml_file=config.LLM_TEMPLATES_ROOT / f"llm-{config.MODELS_ENV}-config.yaml")

logger.info(
    f"LLMConfig initiated. Config={llm_config.yaml_file}. "
    f"LLMModels={llm_config.llm_models}. EmbeddingModels={llm_config.embeddings_models}"
)
