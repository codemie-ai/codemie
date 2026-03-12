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

import contextvars
import json
import logging
import uvicorn.logging

from logging.config import dictConfig
from typing import Dict, Any

from codemie.configs.config import config
from pydantic import BaseModel


class LogFormatter(uvicorn.logging.DefaultFormatter):
    def format(self, record):
        if record.exc_info:
            record.msg = repr(super().formatException(record.exc_info))
            if config.is_local:
                record.msg = record.msg.replace("\\n", "\n")
            record.exc_info = None
            record.exc_text = None
            record.levelname = "ERROR"

        result = super().format(record)
        return result


class LogConfig(BaseModel):
    """Logging configuration to be set for the server"""

    LOGGER_NAME: str = "codemie"
    LOCAL_LOG_FORMAT: str = (
        'Timestamp: %(asctime)s | Level: %(levelname)s | UUID: %(uuid)s \n'
        'User ID: %(user_id)s | Conversation ID: %(conversation_id)s\n'
        'Message: %(message)s\n'
    )

    LOG_FORMAT: str = (
        '{"timestamp": "%(asctime)s", "level": "%(levelname)s", '
        '"uuid": "%(uuid)s", "user_id": "%(user_id)s", '
        '"conversation_id": "%(conversation_id)s", "message": "%(message)s"}'
    )

    version: int = 1
    disable_existing_loggers: bool = False
    formatters: Dict[str, Any] = {}
    handlers: Dict[str, Any] = {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
    }
    loggers: Dict[str, Any] = {
        LOGGER_NAME: {"handlers": ["default"], "level": config.LOG_LEVEL.upper(), "propagate": False},
        LOGGER_NAME + "_tools": {"handlers": ["default"], "level": config.LOG_LEVEL.upper(), "propagate": False},
        LOGGER_NAME + "_enterprise": {"handlers": ["default"], "level": config.LOG_LEVEL.upper(), "propagate": False},
    }

    def set_formatters(self):
        """Set the format according to the environment"""
        fmt = self.LOCAL_LOG_FORMAT if config.is_local else self.LOG_FORMAT

        self.formatters = {
            "default": {
                "()": LogFormatter,
                "fmt": fmt,
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        }


logging_uuid = contextvars.ContextVar("uuid")
logging_user_id = contextvars.ContextVar("user_id")
current_user_email = contextvars.ContextVar("user_email", default="unknown")
logging_conversation_id = contextvars.ContextVar("conversation_id")
old_factory = logging.getLogRecordFactory()


def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, Exception):
        # Create a dictionary to represent the exception
        exception_data = {'type': obj.__class__.__name__, 'message': str(obj)}
        # For JSONDecodeError, include additional details
        if isinstance(obj, json.JSONDecodeError):
            exception_data['lineno'] = obj.lineno
            exception_data['colno'] = obj.colno
            exception_data['pos'] = obj.pos
            exception_data['doc'] = obj.doc
        return exception_data
    return str(obj)


def record_factory(*args, **kwargs):
    """
    Set a UUID for each log record
    """
    record = old_factory(*args, **kwargs)
    record.uuid = logging_uuid.get('-')
    record.user_id = logging_user_id.get('-')
    record.conversation_id = logging_conversation_id.get('-')

    # make message json safe
    record.msg = process_record_msg(record.msg)

    return record


def process_record_msg(msg):
    if config.is_local:
        return msg

    return json.dumps(msg, default=json_serial)[1:-1]


def set_logging_info(uuid: str = '-', user_id: str = '-', conversation_id: str = '-', user_email: str = "-"):
    """
    Set a UUID for the current log record
    """
    # Prevent sending nullable attributes
    uuid = uuid if uuid is not None else '-'
    user_id = user_id if user_id is not None else '-'
    conversation_id = conversation_id if conversation_id is not None else '-'
    logging_uuid.set(uuid)
    logging_user_id.set(user_id)
    current_user_email.set(user_email)
    logging_conversation_id.set(conversation_id)

    logging.setLogRecordFactory(record_factory)


logging.setLogRecordFactory(record_factory)

# Setup logger
log_config = LogConfig(LOG_LEVEL=config.LOG_LEVEL)
log_config.set_formatters()
dictConfig(log_config.model_dump())
logger = logging.getLogger(log_config.LOGGER_NAME)
