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

import threading
from codemie.configs import logger
from codemie.core.thread import CancellationReason, ThreadedGenerator


class GenerationManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls, *args, **kwargs)
                    cls._instance._generators = {}
                    cls._instance._reg_lock = threading.Lock()
        return cls._instance

    def register(self, conversation_id: str, generator: ThreadedGenerator) -> None:
        if not conversation_id:
            return
        with self._reg_lock:
            if conversation_id not in self._generators:
                self._generators[conversation_id] = set()
            self._generators[conversation_id].add(generator)
            logger.debug(
                f"Registered generator for conversation_id={conversation_id}. "
                f"Total registered: {len(self._generators[conversation_id])}"
            )

    def unregister(self, conversation_id: str, generator: ThreadedGenerator) -> None:
        if not conversation_id:
            return
        with self._reg_lock:
            if conversation_id in self._generators:
                self._generators[conversation_id].discard(generator)
                if not self._generators[conversation_id]:
                    del self._generators[conversation_id]
                logger.debug(f"Unregistered generator for conversation_id={conversation_id}")

    def abort(self, conversation_id: str) -> bool:
        if not conversation_id:
            return False
        aborted = False
        with self._reg_lock:
            if conversation_id in self._generators:
                generators = list(self._generators[conversation_id])
                for gen in generators:
                    if not gen.is_closed():
                        gen.close(reason=CancellationReason.ABORTED_BY_USER)
                        aborted = True
                del self._generators[conversation_id]
                logger.debug(f"Aborted generators for conversation_id={conversation_id}")
        return aborted

    def is_active(self, conversation_id: str) -> bool:
        if not conversation_id:
            return False
        with self._reg_lock:
            if conversation_id in self._generators:
                return any(not gen.is_closed() for gen in self._generators[conversation_id])
        return False

    def get_generator(self, conversation_id: str) -> ThreadedGenerator | None:
        """Get the active generator for a conversation if one exists."""
        if not conversation_id:
            return None
        with self._reg_lock:
            if conversation_id in self._generators:
                for gen in self._generators[conversation_id]:
                    if not gen.is_closed():
                        return gen
                if self._generators[conversation_id]:
                    return next(iter(self._generators[conversation_id]))
        return None
