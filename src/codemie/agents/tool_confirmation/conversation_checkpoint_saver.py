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

import json
from base64 import b64encode, b64decode
from typing import Any, Iterable, Optional

from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langchain_core.runnables import RunnableConfig

from codemie.repository.conversation_pause_repository import ConversationPauseRepository


class ConversationCheckpointSaver(BaseCheckpointSaver):
    """LangGraph checkpointer of tool-call confirmation over the ``conversation_checkpoints`` table.

    The conversation id is the thread id. One row per (conversation, checkpoint namespace) holds the latest
    checkpoint of that namespace (root ``""`` and each nested agent) with its writes, so a pause inside a nested
    agent survives a reload. Writes are keyed ``"<checkpoint_id>|<task_id>|<idx>"`` with ``idx`` from
    ``WRITES_IDX_MAP`` for the special channels (``__interrupt__``, ``__resume__`` ...), which are replaced, not
    appended.

    Each call is one statement of ``ConversationPauseRepository``: a put drops only older writes, writes for an
    older checkpoint are ignored, and ``get_tuple`` reads the one namespace row and returns only the writes of its
    stored checkpoint.
    """

    def __init__(self, repository: ConversationPauseRepository | None = None):
        super().__init__()
        self.repository = repository or ConversationPauseRepository()

    @staticmethod
    def _ids(config: RunnableConfig) -> tuple[str, str]:
        configurable = config["configurable"]
        return configurable["thread_id"], configurable.get("checkpoint_ns", "")

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        """Return the stored checkpoint of the config's namespace, or None if absent."""
        thread_id, ns = self._ids(config)
        entry = self.repository.get_checkpoint(thread_id, ns)
        if not entry or "checkpoint" not in entry:
            return None

        checkpoint_id = entry["checkpoint_id"]
        wanted = config["configurable"].get("checkpoint_id")
        if wanted and wanted != checkpoint_id:
            return None

        writes = sorted(
            (w for w in (entry.get("writes") or {}).values() if w["checkpoint_id"] == checkpoint_id),
            key=lambda w: (w["task_id"], w["idx"]),
        )
        return CheckpointTuple(
            config={"configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": checkpoint_id}},
            checkpoint=self._deserialize(entry["checkpoint"]),
            metadata=self._deserialize(entry["metadata"]),
            parent_config=None,
            pending_writes=[(w["task_id"], w["channel"], self._deserialize(w["value"])) for w in writes],
        )

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        *args,
    ) -> RunnableConfig:
        """Store the checkpoint as the latest entry of its namespace."""
        thread_id, ns = self._ids(config)
        self.repository.put_checkpoint(
            thread_id,
            ns,
            {
                "checkpoint_id": checkpoint["id"],
                "checkpoint": self._serialize(checkpoint),
                "metadata": self._serialize(metadata),
            },
        )
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": checkpoint["id"]}}

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Iterable[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Persist the task's pending writes against the checkpoint named in the config."""
        thread_id, ns = self._ids(config)
        checkpoint_id = config["configurable"]["checkpoint_id"]
        regular: dict[str, Any] = {}
        special: dict[str, Any] = {}
        for position, (channel, value) in enumerate(writes):
            idx = WRITES_IDX_MAP.get(channel, position)
            item = {
                "checkpoint_id": checkpoint_id,
                "task_id": task_id,
                "idx": idx,
                "channel": channel,
                "value": self._serialize(value),
            }
            (special if idx < 0 else regular)[f"{checkpoint_id}|{task_id}|{idx}"] = item
        self.repository.put_writes(thread_id, ns, checkpoint_id, regular, special)

    def list(self, config: RunnableConfig) -> Iterable[CheckpointTuple]:
        """Not required for interrupt/resume. Yields nothing."""
        return iter([])

    def _serialize(self, obj) -> str:
        type_str, data_bytes = self.serde.dumps_typed(obj)

        return json.dumps({"type": type_str, "data": b64encode(data_bytes).decode("utf-8")})

    def _deserialize(self, data_str: str):
        data_dict = json.loads(data_str)
        type_str = data_dict["type"]
        data_bytes = b64decode(data_dict["data"])

        return self.serde.loads_typed((type_str, data_bytes))
