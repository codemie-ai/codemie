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

from typing import TYPE_CHECKING, Optional

from sqlalchemy.engine import URL

from codemie.clients.postgres import PostgresClient
from codemie.configs import config, logger
from codemie.enterprise.loader import HAS_SPEND_UPLOADER, SpendUploaderConfig, SpendUploaderService

if TYPE_CHECKING:
    from codemie_enterprise.spend_uploader import SpendUploaderService as SpendUploaderServiceType


def is_spend_uploader_available() -> bool:
    """True when the installed enterprise package ships the spend uploader (SOURCE OF TRUTH for availability)."""
    return HAS_SPEND_UPLOADER


def get_litellm_db_url() -> str:
    """The LiteLLM (source) database URL: LITELLM_SPEND_UPLOADER_LITELLM_DB_URL, else one built from the parts.

    Empty when neither is configured. Credentials are percent-encoded; the result carries a password, never log it.
    """
    if config.LITELLM_SPEND_UPLOADER_LITELLM_DB_URL:
        return config.LITELLM_SPEND_UPLOADER_LITELLM_DB_URL
    host = config.LITELLM_SPEND_UPLOADER_LITELLM_DB_HOST
    user = config.LITELLM_SPEND_UPLOADER_LITELLM_DB_USER
    if not host or not user:
        return ""
    # Built like the application database URL (PostgresClient): user and password always set, escaped by SQLAlchemy.
    url = URL.create(
        drivername="postgresql",
        username=user,
        password=config.LITELLM_SPEND_UPLOADER_LITELLM_DB_PASSWORD,
        host=host,
        port=config.LITELLM_SPEND_UPLOADER_LITELLM_DB_PORT,
        database=config.LITELLM_SPEND_UPLOADER_LITELLM_DB_NAME,
        query={"sslmode": config.LITELLM_SPEND_UPLOADER_LITELLM_DB_SSLMODE}
        if config.LITELLM_SPEND_UPLOADER_LITELLM_DB_SSLMODE
        else {},
    )
    # SQLAlchemy leaves spaces in credentials unescaped, which libpq rejects (see PostgresClient.get_libpq_url).
    return url.render_as_string(hide_password=False).replace(" ", "%20")


def build_spend_uploader_service() -> Optional["SpendUploaderServiceType"]:
    """Build the spend uploader from application config, or None when it cannot run.

    Fails open: a missing package, a missing LiteLLM database URL or an invalid setting is logged and the caller
    skips the run. Built per run rather than once at startup: the CodeMie URL can embed a short-lived IAM token.
    """
    if not HAS_SPEND_UPLOADER:
        logger.warning("Spend uploader requested but the installed codemie-enterprise package does not provide it")
        return None
    litellm_db_url = get_litellm_db_url()
    if not litellm_db_url:
        logger.error(
            "Spend uploader requires the LiteLLM database: set LITELLM_SPEND_UPLOADER_LITELLM_DB_URL or _HOST/_USER"
        )
        return None

    try:
        return SpendUploaderService(
            SpendUploaderConfig(
                litellm_db_url=litellm_db_url,
                codemie_db_url=PostgresClient.get_libpq_url(),
                codemie_db_schema=config.DEFAULT_DB_SCHEMA,
            )
        )
    except Exception as e:
        # Type only: a validation message can echo the offending value, and these values are connection strings.
        logger.error(f"Spend uploader configuration is invalid ({type(e).__name__}); skipping")
        return None
