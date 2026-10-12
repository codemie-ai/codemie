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

from codemie_tools.base.models import ToolMetadata
from codemie_tools.ms_exchange.models import ExchangeToolConfig

EXCHANGE_RETRIEVE_FOLDERS_TOOL = ToolMetadata(
    name="exchange_retrieve_folders",
    description="""
    Lists the mail folders of the configured Microsoft Exchange mailbox (read-only).
    Returns a JSON list of folders with id, name, total_count and unread_count. Takes no arguments.
    """.strip(),
    label="Exchange: Retrieve folders",
    user_description="Lists the mail folders of the configured Exchange mailbox. Read-only.",
    settings_config=False,
    config_class=ExchangeToolConfig,
)

EXCHANGE_READ_TOOL = ToolMetadata(
    name="exchange_read",
    description="""
    Lists messages of a mail folder of the configured Microsoft Exchange mailbox, newest first (read-only,
    never marks messages as read). Args: folder (name or well-known name, default 'Inbox'),
    start (zero-based offset, default 0), count (page size, default 10, at most 50).
    Returns a JSON list of message summaries with id, subject, from, received, is_read, has_attachments.
    """.strip(),
    label="Exchange: Read messages",
    user_description="Lists messages of a folder in the configured Exchange mailbox. Read-only.",
    settings_config=False,
    config_class=ExchangeToolConfig,
)

EXCHANGE_RETRIEVE_BY_ID_TOOL = ToolMetadata(
    name="exchange_retrieve_by_id",
    description="""
    Retrieves one message of the configured Microsoft Exchange mailbox by its id (read-only).
    Args: message_id. Returns JSON with the message summary plus to, cc and the plain-text body.
    """.strip(),
    label="Exchange: Retrieve message by id",
    user_description="Retrieves a single message from the configured Exchange mailbox by id. Read-only.",
    settings_config=False,
    config_class=ExchangeToolConfig,
)

EXCHANGE_ENRICH_BODY_TOOL = ToolMetadata(
    name="exchange_enrich_body",
    description="""
    Returns the plain-text body of a message of the configured Microsoft Exchange mailbox followed by one
    labelled section per attachment with its extracted text (read-only, changes nothing in the mailbox).
    Images are OCR-ed, digital PDF and docx are extracted; other, oversized (over 10 attachments, 10 MB each,
    25 MB total) or non-file attachments are listed as skipped with a reason.
    Args: message_id, ocr_model (optional name of a multimodal model used to OCR images; if omitted the
    assistant's chat model is used, and without any model images are skipped as "no OCR model provided").
    Returns a single string.
    """.strip(),
    label="Exchange: Enrich message body",
    user_description="Returns a message body plus text extracted from its attachments. Read-only.",
    settings_config=False,
    config_class=ExchangeToolConfig,
)
