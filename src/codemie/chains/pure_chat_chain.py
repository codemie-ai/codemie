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

import traceback
import json
from operator import itemgetter
from pydantic import BaseModel
from typing import Any, Dict, Optional

from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import (
    MessagesPlaceholder,
    HumanMessagePromptTemplate,
    SystemMessagePromptTemplate,
    ChatPromptTemplate,
)

from codemie.chains import StreamingChain, GenerationResult, StreamedGenerationResult
from codemie.configs import logger
from codemie.core.models import AssistantChatRequest
from codemie.agents.callbacks.chain_streaming_callback import ChainStreamingCallback
from codemie.agents.utils import get_run_config
from codemie.core.thread import ThreadedGenerator
from codemie.rest_api.security.user import User
from codemie.service.llm_service.llm_service import llm_service


class PureChatChain(StreamingChain):
    def __init__(
        self,
        request: AssistantChatRequest,
        system_prompt: str,
        llm_model: str,
        llm: Optional[Runnable] = None,
        thread_generator: ThreadedGenerator = None,
        user: Optional[User] = None,
        agent_name: Optional[str] = None,
    ):
        self.request = request
        self.system_prompt = system_prompt
        self.llm_model = llm_model
        self.llm = llm
        self.thread_generator = thread_generator
        self.user = user
        self.agent_name = agent_name

    def _get_inputs(self) -> Dict[str, Any]:
        return {"question": self.request.text, "chat_history": self.request.history}

    def invoke(self, inputs: Dict[str, Any]) -> GenerationResult:
        response = self._chain.invoke(
            input=inputs,
            config=self._get_config(),
        )

        return GenerationResult(
            generated=response,
            time_elapsed=None,
            input_tokens_used=None,
            tokens_used=0,
            success=True,
        )

    def generate(self) -> GenerationResult:
        return self.invoke(self._get_inputs())

    def stream(self):
        chunks_collector = []
        try:
            chain = self._chain.with_config(callbacks=[ChainStreamingCallback(self.thread_generator)])
            stream = chain.stream(
                input=self._get_inputs(),
                config=self._get_config(),
            )
            for chunk in stream:
                if not chunk:
                    continue

                chunks_collector.append(chunk)

            if isinstance(chunk, dict):
                generated = json.dumps(chunk)
            elif isinstance(chunk, BaseModel):
                generated = chunk.model_dump_json()
            else:
                generated = "".join(chunks_collector)

            self.thread_generator.send(
                StreamedGenerationResult(
                    generated=generated,
                    generated_chunk="",
                    last=True,
                ).model_dump_json()
            )
        except Exception as e:
            stacktrace = traceback.format_exc()
            error_message = str(e)
            exception_type = type(e).__name__
            chunks_collector.append(f"PureChain generation failed with error: {exception_type}: {error_message}")
            logger.error(f"PureChain generation failed with error: {stacktrace}", exc_info=True)
            self.thread_generator.send(
                StreamedGenerationResult(
                    generated="".join(chunks_collector), generated_chunk="", last=True, debug={}
                ).model_dump_json()
            )
        finally:
            self.thread_generator.close()

    def _build_chain(self) -> Runnable:
        """Helper method to build the chain - makes testing easier"""
        prompt = self._build_prompt_template()

        inputs = {
            "question": itemgetter("question"),
            "chat_history": (itemgetter("chat_history") | RunnableLambda(self._transform_history)),
        }

        return inputs | prompt | self.llm | StrOutputParser()

    @property
    def _chain(self) -> Runnable[Any, str]:
        return self._build_chain()

    def _get_system_prompt(self):
        return self.request.system_prompt or self.system_prompt

    def _build_prompt_template(self):
        llm_model_details = llm_service.get_model_details(self.llm_model)
        first_message = SystemMessagePromptTemplate.from_template(self._get_system_prompt(), template_format="jinja2")
        if not llm_model_details.features.system_prompt:
            first_message = HumanMessagePromptTemplate.from_template(
                self._get_system_prompt(), template_format="jinja2"
            )
        messages = [
            first_message,
            MessagesPlaceholder(variable_name="chat_history"),
            HumanMessagePromptTemplate.from_template("{{question}}", template_format="jinja2"),
        ]
        return ChatPromptTemplate.from_messages(messages, template_format="jinja2")

    def _get_config(self):
        # Use the utility function with correct parameters
        return get_run_config(
            request=self.request,
            llm_model=self.llm_model,
            agent_name=self.agent_name or "pure_chat_chain",
            conversation_id=self.request.conversation_id if self.request else None,
            username=self.user.username if self.user and self.user.username else None,
            additional_tags=["execution_engine:pure_chat_chain"],
        )
