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

import psycopg
from sqlalchemy.engine import URL, make_url

from codemie.clients.postgres import PostgresClient, _get_iam_token
from codemie.configs import config, logger
from codemie.enterprise.loader import HAS_SPEND_UPLOADER, SpendUploaderConfig, SpendUploaderService

_CONNECT_TIMEOUT_S = 10

if TYPE_CHECKING:
    from codemie_enterprise.spend_uploader import SpendUploaderService as SpendUploaderServiceType


def is_spend_uploader_available() -> bool:
    """True when the installed enterprise package ships the spend uploader (SOURCE OF TRUTH for availability)."""
    return HAS_SPEND_UPLOADER


def is_litellm_db_configured() -> bool:
    """True when the LiteLLM database is named by LITELLM_SPEND_UPLOADER_LITELLM_DB_URL or by _DB_HOST and _DB_USER.

    Never fetches an IAM token, unlike get_litellm_db_url(), so it is safe to call at startup.
    """
    return bool(
        config.LITELLM_SPEND_UPLOADER_LITELLM_DB_URL
        or (config.LITELLM_SPEND_UPLOADER_LITELLM_DB_HOST and config.LITELLM_SPEND_UPLOADER_LITELLM_DB_USER)
    )


def _litellm_db_url_from_parts() -> URL | None:
    """The LiteLLM database URL built from _DB_HOST, _DB_USER and the other parts; None without a host or user."""
    host = config.LITELLM_SPEND_UPLOADER_LITELLM_DB_HOST
    user = config.LITELLM_SPEND_UPLOADER_LITELLM_DB_USER
    if not host or not user:
        return None
    password = config.LITELLM_SPEND_UPLOADER_LITELLM_DB_PASSWORD
    # As PostgresClient for POSTGRES_*: a token login is refused over an unencrypted connection.
    sslmode = config.LITELLM_SPEND_UPLOADER_LITELLM_DB_SSLMODE or (
        "require" if config.PG_IAM_AUTH_PROVIDER and not password else ""
    )
    # Built like the application database URL (PostgresClient): escaped by SQLAlchemy.
    return URL.create(
        drivername="postgresql",
        username=user,
        password=password,
        host=host,
        port=config.LITELLM_SPEND_UPLOADER_LITELLM_DB_PORT,
        database=config.LITELLM_SPEND_UPLOADER_LITELLM_DB_NAME,
        query={"sslmode": sslmode} if sslmode else {},
    )


def _with_iam_token(url: URL) -> URL:
    """The URL with a fresh IAM token as its password, when PG_IAM_AUTH_PROVIDER is set and it carries none."""
    if not config.PG_IAM_AUTH_PROVIDER or url.password:
        return url
    # An AWS token is signed for one host, port and user; without them _get_iam_token would sign for POSTGRES_*.
    has_network_host = bool(url.host) and not url.host.startswith("/")
    if config.PG_IAM_AUTH_PROVIDER == "aws" and not (has_network_host and url.username):
        raise ValueError("AWS IAM authentication needs a network host and a user in the LiteLLM database URL")
    return url.set(password=_get_iam_token(host=url.host, port=url.port or 5432, user=url.username))


def get_litellm_db_url() -> str:
    """The LiteLLM (source) database URL: LITELLM_SPEND_UPLOADER_LITELLM_DB_URL, else one built from the parts.

    Empty when neither is configured. As for the application and analytics databases, with PG_IAM_AUTH_PROVIDER a
    password-less URL gets a fresh IAM token as its password (one built from the parts also gets sslmode=require).
    Build it per use and never cache it. The result carries a password or token, never log it.
    """
    if config.LITELLM_SPEND_UPLOADER_LITELLM_DB_URL:
        url = make_url(config.LITELLM_SPEND_UPLOADER_LITELLM_DB_URL)
    else:
        url = _litellm_db_url_from_parts()
        if url is None:
            return ""
    # SQLAlchemy leaves spaces in credentials unescaped, which libpq rejects (see PostgresClient.get_libpq_url).
    return _with_iam_token(url).render_as_string(hide_password=False).replace(" ", "%20")


class LiteLLMDatabaseConnectionError(RuntimeError):
    """The LiteLLM database rejected the configured credentials or could not be reached."""


def verify_litellm_db_connection() -> None:
    """Open and close one connection to the LiteLLM database, so a bad setting fails at startup, not on a tick.

    Like the application database (its migrations connect at startup), the caller lets the error stop the backend.
    The message quotes nothing from the URL or the driver: both can echo connection details.
    """
    try:
        with psycopg.connect(get_litellm_db_url(), connect_timeout=_CONNECT_TIMEOUT_S) as connection:
            connection.execute("SELECT 1")
    except Exception as e:
        logger.error(f"Spend uploader cannot connect to the LiteLLM database ({type(e).__name__})")
        raise LiteLLMDatabaseConnectionError(
            "LITELLM_SPEND_UPLOADER_ENABLED is set but the LiteLLM database connection failed; check "
            "LITELLM_SPEND_UPLOADER_LITELLM_DB_URL (or _DB_HOST/_DB_USER/_DB_PASSWORD) and PG_IAM_AUTH_PROVIDER"
        ) from None


def build_spend_uploader_service() -> Optional["SpendUploaderServiceType"]:
    """Build the spend uploader from application config, or None when it cannot run.

    Fails open: a missing package, a missing LiteLLM database URL or an invalid setting is logged and the caller
    skips the run. Built per run rather than once at startup: both URLs can embed a short-lived IAM token.
    """
    if not HAS_SPEND_UPLOADER:
        logger.warning("Spend uploader requested but the installed codemie-enterprise package does not provide it")
        return None
    if not is_litellm_db_configured():
        logger.error(
            "Spend uploader requires the LiteLLM database: set LITELLM_SPEND_UPLOADER_LITELLM_DB_URL or _HOST/_USER"
        )
        return None

    try:
        return SpendUploaderService(
            SpendUploaderConfig(
                litellm_db_url=get_litellm_db_url(),
                codemie_db_url=PostgresClient.get_libpq_url(),
                codemie_db_schema=config.DEFAULT_DB_SCHEMA,
            )
        )
    except Exception as e:
        # Type only: a validation message can echo the offending value, and these values are connection strings.
        logger.error(f"Spend uploader could not be built ({type(e).__name__}); skipping")
        return None
