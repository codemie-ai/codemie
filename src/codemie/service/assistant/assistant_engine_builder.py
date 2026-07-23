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

from __future__ import annotations

from typing import Any, Callable

from langgraph.graph.state import CompiledStateGraph

from codemie.configs.logger import logger
from codemie.core.models import AssistantChatRequest
from codemie.core.utils import dedupe_preserve_order
from codemie.core.thread import MessageQueue
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User

from codemie.service.mcp.models import MCPToolLoadException
from codemie.service.skills.skill_contributions import SkillContributionsResolver
from codemie.service.subagents.builtin_subagents_registry import BuiltinSubagentsRegistry
from codemie.service.subagents.builtin_subagents import BuiltinSubagent


class LangGraphAssistantBuilder:
    @staticmethod
    def _coerce_list(value: Any) -> list[Any]:
        """Best-effort coercion for optional list-like fields.

        In production these fields are `list[...]` or `None`.
        In unit tests, `Mock(spec=...)` unset attrs often return another Mock
        which is truthy but not iterable in the intended way.
        """
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, tuple | set):
            return list(value)
        return []

    @staticmethod
    def _safe_get_runtime_attr(obj: Any, name: str, default: Any = None) -> Any:
        """Safely read runtime-only attrs from SQLModel/Pydantic objects.

        Pydantic v2 stores underscore attrs in `__pydantic_private__`. Under some
        SQLModel/SQLAlchemy construction paths, `__pydantic_private__` may be
        `None`, and plain `getattr(obj, "_foo")` can crash with
        `TypeError: 'NoneType' object is not subscriptable` (via Pydantic's
        `BaseModel.__getattr__`).

        We avoid `getattr(obj, name)` and read from `__dict__` /
        `__pydantic_private__` directly.
        """
        dct = getattr(obj, "__dict__", None)
        if isinstance(dct, dict) and name in dct:
            return dct[name]

        private = getattr(obj, "__pydantic_private__", None)
        if isinstance(private, dict) and name in private:
            return private[name]

        return default

    @staticmethod
    def _coerce_builtin_subagents(items: list[Any] | None) -> list[BuiltinSubagent]:
        """Coerce a mixed list (strings/enums) into BuiltinSubagent values.

        DB JSONB fields often come back as plain strings; API requests are parsed
        as enums. This helper makes runtime logic tolerant to both.
        """
        # In production `items` is expected to be list-like (or None).
        # In unit tests, `Mock(spec=...)` unset attrs often return a Mock which is
        # truthy but not iterable in the intended sense.
        if items is None or isinstance(items, bool):
            return []
        if not isinstance(items, list | tuple | set):
            return []

        result: list[BuiltinSubagent] = []
        for item in items:
            if isinstance(item, BuiltinSubagent):
                result.append(item)
                continue
            try:
                result.append(BuiltinSubagent(str(item)))
            except Exception:
                # Ignore unknown/invalid entries for backward/forward compatibility.
                continue
        return result

    @classmethod
    def _merge_skill_builtin_subagents(cls, assistant: Assistant) -> list[BuiltinSubagent]:
        base = cls._coerce_builtin_subagents(getattr(assistant, "enabled_builtin_subagents", None) or [])

        # Builtin subagent clones inherit skills for tools/context, but must not
        # re-enable builtin subagents via skills (would cause recursive subagent creation).
        if cls._safe_get_runtime_attr(assistant, "_runtime_disable_skill_builtin_subagents", False) is True:
            return dedupe_preserve_order(base)

        skill_ids = cls._coerce_list(getattr(assistant, "skill_ids", None))
        if not skill_ids:
            return dedupe_preserve_order(base)

        skills = SkillContributionsResolver.resolve_skills_for_assistant(assistant)
        merged = list(base)
        for skill in skills:
            merged.extend(cls._coerce_builtin_subagents(getattr(skill, "enabled_builtin_subagents", None) or []))
        return dedupe_preserve_order(merged)

    @staticmethod
    def _make_unique_builtin_agent_name(base_name: str, existing_names: set[str]) -> str:
        # NOTE: mutates existing_names as an accumulator / name registry.
        if base_name not in existing_names:
            existing_names.add(base_name)
            return base_name

        idx = 2
        while True:
            candidate = f"{base_name}__{idx}"
            if candidate not in existing_names:
                existing_names.add(candidate)
                return candidate
            idx += 1

    @classmethod
    def _build_builtin_subagent_executor(
        cls,
        *,
        parent: Assistant,
        builtin: BuiltinSubagent,
        user: User,
        request: AssistantChatRequest,
        request_uuid: str,
        thread_generator: MessageQueue,
        llm_model: str,
        agent_name: str,
    ):
        """Create a compiled executor for a builtin subagent.

        The subagent inherits everything from the parent assistant except:
        - assistant_ids (no nested assistants)
        - enabled_builtin_subagents (no builtin recursion)
        - system_prompt (builtin-specific prompt from config)
        """
        from codemie.service.tools.assistant_factory import AssistantFactory

        builtin_system_prompt = BuiltinSubagentsRegistry.get_system_prompt(builtin)

        # Clone parent assistant config cheaply (no full serialize/validate pass), then override.
        child_assistant = parent.model_copy(deep=False)
        child_assistant.id = None
        child_assistant.name = agent_name
        child_assistant.assistant_ids = []
        child_assistant.enabled_builtin_subagents = []
        child_assistant.system_prompt = builtin_system_prompt

        # Mark as builtin clone and disable skill-contributed builtins at runtime to prevent recursion.
        child_assistant._runtime_builtin_subagent = builtin
        child_assistant._runtime_disable_skill_builtin_subagents = True

        return AssistantFactory(
            assistant=child_assistant,
            user=user,
            request=request,
            request_uuid=request_uuid,
            thread_generator=thread_generator,
            llm_model=llm_model,
        ).build()

    @classmethod
    def create_subagent_executors(
        cls,
        assistant: Assistant,
        user: User,
        request: AssistantChatRequest,
        request_uuid: str,
        thread_generator: MessageQueue,
        llm_model: str,
    ) -> list[CompiledStateGraph[Any, Any, Any, Any]]:
        effective_builtins = cls._merge_skill_builtin_subagents(assistant)

        if not assistant.assistant_ids and not effective_builtins:
            return []

        from codemie.service.tools.assistant_factory import create_assistant_executors

        assistant_ids = list(assistant.assistant_ids or [])

        logger.debug(
            f"Creating subagent executors for {len(assistant_ids)} sub-assistants and "
            f"{len(effective_builtins)} builtin subagents"
        )

        subagents = create_assistant_executors(
            assistant_ids=assistant_ids,
            user=user,
            request=request,
            request_uuid=request_uuid,
            thread_generator=thread_generator,
            llm_model=llm_model,
            parent_assistant=assistant,
        )

        existing_names: set[str] = set()
        # Best-effort seed: names of assistants referenced by assistant_ids
        try:
            sub_assistants = Assistant.get_by_ids(user, assistant_ids, parent_assistant=assistant)
            existing_names.update([sa.name for sa in sub_assistants if sa and sa.name])
        except Exception:
            pass

        for builtin in effective_builtins:
            base_name = f"__builtin__{builtin.value}"
            agent_name = cls._make_unique_builtin_agent_name(base_name, existing_names)
            try:
                subagents.append(
                    cls._build_builtin_subagent_executor(
                        parent=assistant,
                        builtin=builtin,
                        user=user,
                        request=request,
                        request_uuid=request_uuid,
                        thread_generator=thread_generator,
                        llm_model=llm_model,
                        agent_name=agent_name,
                    )
                )
            except MCPToolLoadException as e:
                # Builtin subagent failures stay non-fatal, but the log must still name the
                # subagent and the MCP server that broke it.
                e.attach_assistant_context(assistant_name=agent_name)
                logger.error(
                    f"Failed to create builtin subagent executor for {builtin.value} "
                    f"due to MCP server '{e.server_name}': {str(e.original_error)}"
                )
            except Exception as e:
                logger.error(f"Failed to create builtin subagent executor for {builtin.value}: {e}")

        logger.debug(f"Created {len(subagents)} subagent executors")
        return subagents

    @staticmethod
    def get_subagent_descriptions_with_contributions(assistant: Assistant, user: User) -> dict[str, str]:
        effective_builtins = LangGraphAssistantBuilder._merge_skill_builtin_subagents(assistant)

        assistant_ids = LangGraphAssistantBuilder._coerce_list(getattr(assistant, "assistant_ids", None))
        if not assistant_ids and not effective_builtins:
            return {}

        try:
            sub_assistants = Assistant.get_by_ids(user, assistant.assistant_ids, parent_assistant=assistant)
            descriptions = {
                sub_assistant.name: sub_assistant.description or f"Assistant {sub_assistant.name}"
                for sub_assistant in sub_assistants
            }

            existing_names = set(descriptions.keys())
            for builtin in effective_builtins:
                base_name = f"__builtin__{builtin.value}"
                agent_name = LangGraphAssistantBuilder._make_unique_builtin_agent_name(base_name, existing_names)
                descriptions[agent_name] = BuiltinSubagentsRegistry.get_display_name(builtin)

            logger.debug(f"Fetched descriptions for {len(descriptions)} subagents")
            return descriptions
        except Exception as error:
            logger.error(f"Failed to fetch subagent descriptions: {str(error)}")
            return {}

    @staticmethod
    def configure_agent_kwargs(
        agent_kwargs: dict[str, Any],
        assistant: Assistant,
        user: User,
        request: AssistantChatRequest,
        request_uuid: str,
        thread_generator: MessageQueue,
        llm_model: str,
        smart_tool_selection_enabled: bool,
        *,
        create_subagent_executors: Callable[..., list[CompiledStateGraph[Any, Any, Any, Any]]],
        get_subagent_descriptions: Callable[[Assistant, User], dict[str, str]],
    ) -> None:
        agent_kwargs["smart_tool_selection_enabled"] = smart_tool_selection_enabled

        subagents = create_subagent_executors(
            assistant=assistant,
            user=user,
            request=request,
            request_uuid=request_uuid,
            thread_generator=thread_generator,
            llm_model=llm_model,
        )
        if subagents:
            agent_kwargs["subagents"] = subagents
            # Use injected callback for descriptions (makes this helper testable and
            # keeps runtime behavior customizable by the caller).
            agent_kwargs["subagent_descriptions"] = get_subagent_descriptions(assistant, user)
