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

import json

from functools import partial
from typing import Dict, Any
from collections.abc import Sequence
from pydantic import BaseModel
from typing import Callable

from langchain_core.agents import AgentAction
from langchain_core.prompts.chat import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnablePassthrough
from langchain_classic.agents.output_parsers.tools import ToolsAgentOutputParser
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatGeneration
from langchain_classic.agents.format_scratchpad.tools import (
    format_to_tool_messages,
)
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
)


MessageFormatter = Callable[[Sequence[tuple[AgentAction, str]]], list[BaseMessage]]


def middleware(
    input_dict: Dict[str, Any],
    llm_with_tools: BaseChatModel,
    llm_with_structured_output: BaseChatModel,
    prompt: ChatPromptTemplate,
) -> list[ChatGeneration]:
    # Extract relevant information from the input dictionary
    input_text = input_dict["input"]
    agent_scratchpad = input_dict["agent_scratchpad"]
    chat_history = input_dict["chat_history"]

    formatted_prompt = prompt.format_messages(
        input=input_text, agent_scratchpad=agent_scratchpad, chat_history=chat_history
    )

    # Call the LLM with the prepared input
    llm_output = llm_with_tools.invoke(formatted_prompt)

    # Check if the LLM returned a ToolCall
    if llm_output.additional_kwargs.get("tool_calls"):
        # Extract the tool calls from the LLM output
        return llm_output

    try:
        # If the LLM did not return a ToolCall, make another LLM call to get the structured output
        structured_output = llm_with_structured_output.invoke(formatted_prompt)

        if isinstance(structured_output, BaseModel):
            content = structured_output.model_dump_json()
        elif isinstance(structured_output, dict):
            content = json.dumps(structured_output)
        else:
            raise ValueError(f"Unexpected structured output data type: {type(structured_output)}")
        # Create an AIMessage with the structured output
        ai_message = AIMessage(
            content=content,
            additional_kwargs={"finish_reason": "stop"},
        )
        # Return the AIMessage as a ChatGeneration
        return ai_message
    except Exception as e:
        raise ValueError(f"Unable to generate stuctured output. Reason: {e}")


def create_structured_tool_calling_agent(
    llm: BaseChatModel,
    tools: list,
    prompt: ChatPromptTemplate,
    structured_output: BaseModel | dict,
    *,
    message_formatter: MessageFormatter = format_to_tool_messages,
) -> Runnable:
    missing_vars = {"agent_scratchpad"}.difference(prompt.input_variables + list(prompt.partial_variables))
    if missing_vars:
        raise ValueError(f"Prompt missing required variables: {missing_vars}")

    if not hasattr(llm, "bind_tools"):
        raise ValueError(
            "This function requires a .bind_tools method be implemented on the LLM.",
        )
    llm_with_tools = llm.bind_tools(tools)
    llm_with_structured_output = llm.with_structured_output(structured_output)

    agent = (
        RunnablePassthrough.assign(
            agent_scratchpad=lambda x: message_formatter(x["intermediate_steps"]),
        )
        | partial(
            middleware,
            llm_with_tools=llm_with_tools,
            llm_with_structured_output=llm_with_structured_output,
            prompt=prompt,
        )
        | ToolsAgentOutputParser()
    )
    return agent
