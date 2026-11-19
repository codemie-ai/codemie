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

"""The one call producers make to publish a file: delegates to the injected publisher, if any."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from codemie_tools.base.artifact_publishing import ArtifactPublisher, PublishContext, PublishResult


def publish_artifact(
    publisher: ArtifactPublisher | None, ctx: PublishContext | None, name: str, **kwargs: Any
) -> PublishResult:
    """Publish through `publisher`, or return a not-delivered reason when none is injected."""
    if publisher is None or ctx is None:
        name = PurePosixPath(name.replace("\\", "/")).name or name
        return PublishResult(name=name, reason=f"`{name}` was not delivered: storage not configured")
    return publisher.publish(ctx, name, **kwargs)
