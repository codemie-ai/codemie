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

"""Converts existing assistants' uploaded icons into assistant-owned entity icons.

    poetry run python -m codemie.service.assistant.assistant_icon_backfill [--dry-run]

Each assistant whose icon_url points at an uploaded image gets that image copied to
``assistants/{id}/icon<ext>`` and its icon_url rewritten, so every reader of the assistant can fetch it.
Entity icons and external URLs are skipped, a missing source file leaves the assistant unchanged, and a
second run converts nothing because converted rows are entity icons. --dry-run only counts.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.user_project_repository import user_project_repository
from codemie.repository.user_repository import user_repository
from codemie.rest_api.models.assistant import Assistant
from codemie.service.assistant.assistant_icon_service import AssistantIconService
from codemie.service.file_service.download_rules import ASSISTANT_ICON_PREFIX

_BATCH_SIZE = 200
_FILES_MARKER = "/v1/files/"


@dataclass
class BackfillResult:
    converted: int = 0
    skipped: int = 0
    missing: int = 0


def _owner_may_publish(sharers: list[Assistant], file_object) -> bool:
    """The file may be published only if its owner could have saved it as the icon of a sharing assistant.

    That is anyone with WRITE on one of the assistants sharing this icon_url, the roles ``update_assistant``
    checks: its creator, an admin of its project, or a platform admin or maintainer. This mirrors the save
    path, where the saver must be able to download the file: stored tokens were never validated before, so
    a token can name any owner's file and the backfill must not publish it.
    """
    owner = file_object.owner
    if not owner:
        return False
    if any(_created_by(a) == owner for a in sharers):
        return True
    return _is_admin_of_any(owner, {a.project for a in sharers if a.project})


def _created_by(assistant: Assistant) -> str | None:
    """The creator's id, falling back to the legacy ``creator`` field as ``Assistant.is_owned_by`` does."""
    return assistant.created_by.id if assistant.created_by else assistant.creator


def _is_admin_of_any(user_id: str, projects: set[str]) -> bool:
    """Whether the user is a platform admin or maintainer, or an admin of one of the projects."""
    from codemie.clients.postgres import get_session

    try:
        with get_session() as session:
            user = user_repository.get_by_id(session, user_id)
            if user is None:
                return False
            if user.is_admin or user.is_maintainer:
                return True
            return any(user_project_repository.is_admin(session, user_id, project) for project in projects)
    except Exception:  # noqa: BLE001 - an unknown owner never publishes
        logger.warning("Assistant icon backfill: could not check the icon file owner's roles", exc_info=True)
        return False


def _copy_icon(assistant: Assistant, prefix: str, file_object, dry_run: bool) -> str:
    try:
        new_token = AssistantIconService.copy_as_entity_icon(str(assistant.id), file_object, write=not dry_run)
    except ExtendedHTTPException:
        logger.warning(f"Assistant icon backfill: source file missing or unreadable for assistant_id={assistant.id}")
        return "missing"

    if not dry_run:
        assistant.icon_url = f"{prefix}{_FILES_MARKER}{new_token}"
        assistant.update(validate=False, touch_timestamp=False)
    return "converted"


def _convert(assistant: Assistant, dry_run: bool, handled: set[str]) -> list[str]:
    """Return the outcomes of converting this assistant and every other one sharing its icon_url.

    The assistants sharing a legacy icon_url are decided together: converting the creator's original first
    would otherwise remove the evidence a later clone needs. Each gets its own copy and is added to ``handled``.
    """
    prefix, _, token = (assistant.icon_url or "").rpartition(_FILES_MARKER)
    try:
        file_object = AssistantIconService.decode_token(token)
    except ExtendedHTTPException:
        return ["skipped"]

    bucket = config.CODEMIE_STORAGE_BUCKET_NAME
    is_entity_icon = bool(bucket) and file_object.owner == bucket and file_object.name.startswith(ASSISTANT_ICON_PREFIX)
    if is_entity_icon or not (file_object.mime_type or "").startswith("image/"):
        return ["skipped"]

    sharers = list({str(a.id): a for a in [assistant, *Assistant.get_by_icon_url(assistant.icon_url)]}.values())
    if not _owner_may_publish(sharers, file_object):
        logger.warning(
            f"Assistant icon backfill: icon file owner may not edit the assistant, assistant_id={assistant.id}"
        )
        return ["skipped"]

    outcomes = []
    for sharer in sharers:
        if str(sharer.id) in handled:
            continue
        handled.add(str(sharer.id))
        outcomes.append(_copy_icon(sharer, prefix, file_object, dry_run))
    return outcomes


def backfill_assistant_icons(dry_run: bool = False) -> BackfillResult:
    result = BackfillResult()
    after_id: str | None = None
    handled: set[str] = set()
    while batch := Assistant.get_uploaded_icon_batch(after_id, _BATCH_SIZE):
        for assistant in batch:
            if str(assistant.id) in handled:
                continue
            for outcome in _convert(assistant, dry_run, handled):
                setattr(result, outcome, getattr(result, outcome) + 1)
        next_after_id = str(batch[-1].id)
        if after_id is not None and next_after_id <= after_id:
            raise RuntimeError(
                f"Assistant icon backfill: paging cursor did not advance (after_id={after_id}, last_id={next_after_id})"
            )
        after_id = next_after_id
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="count what would change without writing")
    args = parser.parse_args(argv)

    result = backfill_assistant_icons(dry_run=args.dry_run)
    logger.info(
        f"assistant icon backfill{' (dry run)' if args.dry_run else ''}: "
        f"{result.converted} converted, {result.skipped} skipped, {result.missing} missing"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
