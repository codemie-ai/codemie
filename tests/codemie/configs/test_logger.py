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

import json
import logging
import sys

from codemie.configs import config
from codemie.configs.logger import process_record_msg, LogFormatter

config.ENV = "dev"


def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, Exception):
        exception_data = {'type': obj.__class__.__name__, 'message': str(obj)}
        if isinstance(obj, json.JSONDecodeError):
            exception_data['lineno'] = obj.lineno
            exception_data['colno'] = obj.colno
            exception_data['pos'] = obj.pos
            exception_data['doc'] = obj.doc
        return exception_data
    return str(obj)


def test_basic_message_handling():
    """Test handling of basic data types"""
    msg = "Test message"
    expected_result = json.dumps(msg)[1:-1]
    assert process_record_msg(msg) == expected_result


def test_nested_objects():
    """Test handling of nested dictionaries"""
    msg = {"key": {"subkey": "value"}}
    expected_result = json.dumps(msg, default=json_serial)[1:-1]
    assert process_record_msg(msg) == expected_result


def test_list_handling():
    """Test handling of lists, including lists of dictionaries"""
    msg = ["item1", {"key": "value"}]
    expected_result = json.dumps(msg, default=json_serial)[1:-1]
    assert process_record_msg(msg) == expected_result


def test_json_decode_error_handling():
    """Test handling of JSONDecodeError with additional details"""

    class SimulatedJSONDecodeError(json.JSONDecodeError):
        def __init__(self, msg, doc, pos):
            super().__init__(msg, doc, pos)

    simulated_error = SimulatedJSONDecodeError("Error parsing JSON", "{}", 0)
    processed_error_msg = process_record_msg(simulated_error)
    expected_result = json.dumps(
        {
            'type': 'SimulatedJSONDecodeError',
            'message': "Error parsing JSON: line 1 column 1 (char 0)",
            'lineno': 1,
            'colno': 1,
            'pos': 0,
            'doc': '{}',
        },
        default=json_serial,
    )[1:-1]

    assert processed_error_msg == expected_result


def test_format_regular_message():
    formatter = LogFormatter()
    record = logging.LogRecord(
        name='test',
        level=logging.INFO,
        pathname='test.py',
        lineno=1,
        msg='Test message 1',
        args=None,
        exc_info=None,
    )
    formatted = formatter.format(record)
    assert formatted == 'Test message 1'


def test_format_exception():
    formatter = LogFormatter()

    try:
        raise ValueError("Test exception")
    except ValueError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name='test',
        level="ERROR",
        pathname='test.py',
        lineno=1,
        msg="Test message 2",
        args=None,
        exc_info=exc_info,
    )
    formatted = formatter.format(record)
    assert formatted.startswith('\'Traceback (most recent call last):')
