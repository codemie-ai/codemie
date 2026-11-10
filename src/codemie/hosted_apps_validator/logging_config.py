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

"""The validator's own logging setup: same shape as ``codemie.configs.logger``, without its
application-wide dependencies (no ``Config``, no OpenTelemetry, no script-call guard) so the
validator stays importable with only its own environment variables."""

from __future__ import annotations

import logging
import os
import uvicorn.logging

from logging.config import dictConfig

LOGGER_NAME = "codemie.hosted_apps_validator"

_LOCAL_LOG_FORMAT = "Timestamp: %(asctime)s | Level: %(levelname)s | Message: %(message)s"
_LOG_FORMAT = '{"timestamp": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}'


class LogFormatter(uvicorn.logging.DefaultFormatter):
    def format(self, record):
        if record.exc_info:
            record.msg = repr(self.formatException(record.exc_info))
            record.exc_info = None
            record.exc_text = None
            record.levelname = "ERROR"
        return super().format(record)


def configure_logging(environ=os.environ) -> logging.Logger:
    """Install a handler and level for the validator's logger from ``LOG_LEVEL`` (default INFO)."""
    is_local = environ.get("ENV", "local") == "local"
    level = environ.get("LOG_LEVEL", "INFO").upper()
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "()": LogFormatter,
                    "fmt": _LOCAL_LOG_FORMAT if is_local else _LOG_FORMAT,
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
            },
            "handlers": {
                "default": {
                    "formatter": "default",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stderr",
                },
            },
            "loggers": {
                LOGGER_NAME: {"handlers": ["default"], "level": level, "propagate": False},
            },
        }
    )
    return logging.getLogger(LOGGER_NAME)
