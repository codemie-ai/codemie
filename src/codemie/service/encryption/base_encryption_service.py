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

from abc import ABC, abstractmethod


class BaseEncryptionService(ABC):
    @abstractmethod
    def encrypt(self, data):
        pass

    @abstractmethod
    def decrypt(self, data):
        pass


class PlainEncryptionService(BaseEncryptionService):
    def encrypt(self, data):
        return data

    def decrypt(self, data):
        return data


class Base64EncryptionService(BaseEncryptionService):
    def encrypt(self, data):
        return base64.b64encode(data.encode('utf-8')).decode('ascii')

    def decrypt(self, data):
        return base64.b64decode(data.encode('ascii')).decode('utf-8')
