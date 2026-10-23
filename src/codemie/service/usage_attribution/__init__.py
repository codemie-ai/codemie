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

"""Usage Attribution analytics: who pays for Web Platform and CLI usage, and what kind of CLI work it is.

Everything is computed on request from existing Elasticsearch metrics and the app DB. The heavy analysis runs in a
worker process and is cached (Redis, or in-process without it); nothing is stored beyond the cache TTL.
Keep this package import-light: the worker process imports it on start.
"""
