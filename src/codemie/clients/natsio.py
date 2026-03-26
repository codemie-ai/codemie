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

"""Client module for the NATS.io client."""

import ssl
from typing import Optional, Any, Dict

import nats

from codemie.configs import config, logger


class Client:
    """Client class is the facade for the NATS.io client."""

    # Default NATS server configuration
    nats_servers: str | list[str] = [config.NATS_SERVERS_URI]

    # Default NATS options
    nats_options: Dict[str, Any] = {
        "user": config.NATS_USER,
        "password": config.NATS_PASSWORD,
        "connect_timeout": config.NATS_CONNECT_TIMEOUT,
        "max_reconnect_attempts": config.NATS_MAX_RECONNECT_ATTEMPTS,
        "reconnect_time_wait": config.NATS_RECONNECT_TIME_WAIT,
        "verbose": config.NATS_VERBOSE,
        "ping_interval": config.NATS_PING_INTERVAL,
        "max_outstanding_pings": config.NATS_MAX_OUTSTANDING_PINGS,
    }

    # Configure SSL context if TLS verification should be skipped
    if config.NATS_SKIP_TLS_VERIFY is True:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        # Set minimum TLS version to support older servers
        ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1
        # Add SSL context to options
        nats_options["tls"] = ssl_ctx
        # Set hostname to None to prevent hostname verification issues
        nats_options["tls_hostname"] = None

    def __init__(
        self,
        nats_servers: str | list[str] = None,
        nats_options: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize the NATS client.

        Args:
            nats_servers: NATS server URI(s) to connect to
            nats_options: Additional options for NATS client
        """
        self.nats_servers = nats_servers or self.nats_servers

        if nats_options:
            self.nats_options = {**self.nats_options, **nats_options}

    def _get_loggable_options(self) -> Dict[str, Any]:
        """
        Create a copy of nats_options suitable for logging (without sensitive data).

        Returns:
            Dictionary with options safe for logging
        """
        # Create a shallow copy of options
        loggable_options = dict(self.nats_options)

        # Remove sensitive information
        if "password" in loggable_options:
            loggable_options.pop("password")

        return loggable_options

    def _format_servers_log(self) -> str:
        return f"ServerUri: {self.nats_servers[0]}"

    # Define callback methods at class level
    async def _disconnected_cb(self):
        """Called when the client is disconnected."""
        logger.warning(f'Got disconnected from NATS. {self._format_servers_log()}')

    async def _reconnected_cb(self, nc):
        """Called when the client reconnects to a server."""
        logger.info(f'Reconnected to NATS server: {nc.connected_url.netloc}. {self._format_servers_log()}')

    async def _error_cb(self, e):
        """Called when an error occurs."""
        logger.error(f'NATS client error: {str(e)}. {self._format_servers_log()}', exc_info=True)

    async def _closed_cb(self):
        """Called when the connection is closed."""
        logger.info(f'NATS connection closed. {self._format_servers_log()}')

    async def connect(self) -> nats.NATS:
        """
        Connect to the NATS server.

        Returns:
            NATS client connection object
        """
        # Log connection attempt with server info and options (excluding password)
        logger.info(f'Connecting to NATS. {self._format_servers_log()}')
        logger.info(f'NATS connection options: {self._get_loggable_options()}')

        # Prepare options with callbacks included
        connect_options = {
            "servers": self.nats_servers,
            "error_cb": self._error_cb,
            "disconnected_cb": self._disconnected_cb,
            "closed_cb": self._closed_cb,
            "reconnected_cb": self._reconnected_cb,
            **self.nats_options,
        }

        # Add proper handling for EOF in SSL connections
        if "tls" in connect_options:
            connect_options["allow_reconnect"] = True
            connect_options["dont_randomize"] = False

        try:
            # First try connecting with TLS if configured
            nc = await nats.connect(**connect_options)
        except ssl.SSLError as e:
            # If TLS connection fails, try without TLS
            logger.warning(f"SSL connection failed: {e}. Attempting non-SSL connection.")
            # Remove TLS options and try again
            if "tls" in connect_options:
                del connect_options["tls"]
            if "tls_hostname" in connect_options:
                del connect_options["tls_hostname"]
            # Try connecting without TLS
            nc = await nats.connect(**connect_options)

        logger.info(
            f'Connected to NATS: {nc.connected_url.netloc}. {self._format_servers_log()}. '
            f'Maximum payload is {nc.max_payload} bytes'
        )
        return nc
