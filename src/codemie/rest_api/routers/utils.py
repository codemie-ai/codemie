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

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, NoReturn

from fastapi import Request, status
from starlette.responses import StreamingResponse

from codemie.configs import config, logger
from codemie.core.exceptions import ExtendedHTTPException

if TYPE_CHECKING:
    from codemie.core.thread import ThreadedGenerator
    from codemie.workflows.workflow import WorkflowExecutor

NDJSON_MEDIA_TYPE = "application/x-ndjson"
WORKFLOW_EXECUTION_ID_KEY = "workflow_execution_id"


# Keep a general-purpose pool for unrelated background work. Workflow streaming producers,
# assistant execution, and ThoughtConsumer readers must not share a pool because they can
# block one another under load; separate pools prevent cross-role starvation.
executor = ThreadPoolExecutor(max_workers=config.THREAD_POOL_MAX_WORKERS, thread_name_prefix="default")
producer_executor = ThreadPoolExecutor(
    max_workers=config.THREAD_POOL_MAX_WORKERS,
    thread_name_prefix="producer",
)
assistant_executor = ThreadPoolExecutor(
    max_workers=config.ASSISTANT_THREAD_POOL_MAX_WORKERS,
    thread_name_prefix="assistant",
)
consumer_executor = ThreadPoolExecutor(
    max_workers=config.THREAD_POOL_MAX_WORKERS,
    thread_name_prefix="consumer",
)


def raise_access_denied(action: str) -> NoReturn:
    raise ExtendedHTTPException(
        code=status.HTTP_401_UNAUTHORIZED,
        message="Access denied",
        details=f"You do not have the necessary permissions to {action} this entity.",
        help="Please ensure you have the correct role or permissions assigned to your account. "
        "If you believe this is an error, contact your system administrator.",
    )


def raise_forbidden(action: str) -> NoReturn:
    raise ExtendedHTTPException(
        code=status.HTTP_403_FORBIDDEN,
        message="Access denied",
        details=f"You do not have the necessary permissions to {action} this entity.",
        help="Please ensure you have the correct role or permissions assigned to your account. "
        "If you believe this is an error, contact your system administrator.",
    )


def raise_unprocessable_entity(action: str, resource: str, exc: Exception) -> NoReturn:
    raise ExtendedHTTPException(
        code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        message=f"Failed to {action} a {resource}",
        details=f"An error occurred while trying to {action} a {resource}: {str(exc)}",
        help="Please check your request format and try again. If the issue persists, contact support.",
    ) from exc


def raise_not_found(resource_id: str, resource_type: str) -> NoReturn:
    raise ExtendedHTTPException(
        code=status.HTTP_404_NOT_FOUND,
        message=f"{resource_type} not found",
        details=f"The {resource_type} with ID [{resource_id}] could not be found in the system.",
        help="Please ensure the specified ID is correct",
    )


def run_in_thread_pool(func, *args):
    future = executor.submit(func, *args)
    return future


def run_producer_in_thread_pool(func, *args):
    future = producer_executor.submit(func, *args)
    return future


def run_assistant_in_thread_pool(func, *args):
    future = assistant_executor.submit(func, *args)
    return future


def run_consumer_in_thread_pool(func, *args):
    future = consumer_executor.submit(func, *args)
    return future


def remove_nulls(obj):
    if isinstance(obj, dict):
        return {k: remove_nulls(v) for k, v in obj.items() if v is not None}

    elif isinstance(obj, list):
        return [remove_nulls(i) for i in obj if i is not None]

    return obj


def _handle_streaming_execution(
    workflow: "WorkflowExecutor", raw_request: Request, generator_queue: ThreadedGenerator, execution_id: str
) -> StreamingResponse:
    """
    Handle synchronous streaming workflow execution.

    Args:
        workflow: WorkflowExecutor already initialized with the generator_queue
        raw_request: FastAPI request for disconnect handling
        generator_queue: ThreadedGenerator that was passed during workflow creation
        execution_id: Workflow execution ID injected into every streamed chunk

    Returns:
        StreamingResponse with NDJSON content
    """
    raw_request.state.on_disconnect(lambda: _handle_client_disconnect(generator_queue))

    wrapped_stream = _serve_workflow_stream(workflow, generator_queue, execution_id)

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }

    return StreamingResponse(
        content=wrapped_stream,
        media_type=NDJSON_MEDIA_TYPE,
        headers=headers,
    )


def _handle_client_disconnect(threaded_generator: ThreadedGenerator):
    """Stop thread generator queue on client disconnect"""
    if not threaded_generator.is_closed():
        logger.debug("Workflow streaming client disconnected")
        threaded_generator.close()


def _serve_workflow_stream(workflow: "WorkflowExecutor", generator_queue: ThreadedGenerator, execution_id: str):
    """
    Execute workflow in thread and yield streaming data.

    The workflow is already initialized with the generator_queue, so no mutation is needed.

    Args:
        workflow: WorkflowExecutor with generator_queue already set
        generator_queue: ThreadedGenerator to read messages from
        execution_id: Injected into every chunk as workflow_execution_id
    """
    from codemie.chains.base import StreamedGenerationResult
    from time import time
    from types import SimpleNamespace

    execution_start = time()

    run_producer_in_thread_pool(workflow.stream_to_client)

    generation_result = None
    accumulated_content = ""
    while True:
        value = generator_queue.queue.get()
        if value is not StopIteration:
            generation_result = json.loads(value, object_hook=lambda d: SimpleNamespace(**d))
            accumulated_content += getattr(generation_result, 'generated_chunk', '') or ''

            chunk = json.loads(value)
            chunk[WORKFLOW_EXECUTION_ID_KEY] = execution_id
            yield f"{json.dumps(chunk)}\n"
            generator_queue.queue.task_done()
        else:
            # Skip if the last chunk already carries last=True (e.g. state_interrupted).
            if getattr(generation_result, 'last', False):
                break

            # Always send the final chunk so clients can reliably detect stream completion.
            # We intentionally avoid querying the DB here: when delete_on_completion=True,
            # the execution record may already be deleted by _auto_delete_execution() which
            # races with this consumer after thought_queue.close() signals StopIteration.
            # The generated text comes from accumulated stream chunks; thought.message is the
            # fallback for thought-only nodes that emit no generated_chunk deltas.
            thought = getattr(generation_result, 'thought', None)
            final_message = StreamedGenerationResult(
                generated=accumulated_content or (thought.message if thought else ""),
                time_elapsed=time() - execution_start,
                generated_chunk="",
                last=True,
                workflow_execution_id=execution_id,
            )
            yield f"{final_message.model_dump_json()}\n"
            break
