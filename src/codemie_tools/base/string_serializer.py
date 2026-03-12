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

import base64
import binascii
import re

UTF_8 = "utf-8"


class StringSerializer:
    """
    A class used to serialize an array of string to a
    single b64 encoded string and deserialize it back

    Internal serialization format:
    5~hello5~world -> 5 is number of characters, ~ is separator

    Deserialization also supports legacy format (words splitted with _)
    for backwards compatability, e.g. hello_world.

    Input and output is assumed to be a base 64 string
    """

    SEPARATOR_CHAR = '~'
    SEPARATOR_REGEXP = r"^\d+[~]"
    CHAR_COUNT_REGEXP = r"^\d+"

    @staticmethod
    def serialize(data: list[str]) -> str:
        output = ""

        for word in data:
            output += str(len(word)) + StringSerializer.SEPARATOR_CHAR + word

        return base64.b64encode(output.encode(UTF_8)).decode(UTF_8)

    @staticmethod
    def deserialize(encoded_data: str) -> list[str]:
        try:
            data = base64.b64decode(encoded_data).decode(UTF_8)
        except binascii.Error:
            return []

        result = []

        while len(data) > 0:
            match = re.match(StringSerializer.SEPARATOR_REGEXP, data)

            if not match:
                return data.split('_')  # Old format

            num_of_chars = int(re.match(StringSerializer.CHAR_COUNT_REGEXP, data).group())
            separator_index = len(str(num_of_chars)) + 1
            word = data[separator_index : separator_index + num_of_chars]
            result.append(word)
            data = data[separator_index + num_of_chars :]

        return result
