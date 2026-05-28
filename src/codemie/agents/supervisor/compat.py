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

from codemie.agents.supervisor.bootstrap import apply_langgraph_supervisor_compatibility_patch
from codemie.agents.supervisor.constants import METADATA_KEY_PARALLEL_SUBAGENT_PARENT_HANDOFF
from codemie.agents.supervisor.history import (
    PARALLEL_SUBAGENT_HANDOFF_ACK_KEY,
    _strip_handoff_back_messages_pre_model_hook,
    _strip_subagent_task_messages_pre_model_hook,
    _subagent_task_pre_model_hook,
)
from codemie.agents.supervisor.pre_model_hooks import (
    _compose_pre_model_hooks,
    _image_artifact_pre_model_hook,
)

__all__ = [
    "PARALLEL_SUBAGENT_HANDOFF_ACK_KEY",
    "METADATA_KEY_PARALLEL_SUBAGENT_PARENT_HANDOFF",
    "_compose_pre_model_hooks",
    "_image_artifact_pre_model_hook",
    "_strip_handoff_back_messages_pre_model_hook",
    "_strip_subagent_task_messages_pre_model_hook",
    "_subagent_task_pre_model_hook",
    "apply_langgraph_supervisor_compatibility_patch",
]
