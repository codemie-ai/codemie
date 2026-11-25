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

"""PostgreSQL predicate shared by the warmup query and the partial index."""

# Pydantic writes booleans and also accepts these string and numeric values on older rows.
# The fixed expression lets PostgreSQL match the query to the partial index.
POOL_ENABLED_PREDICATE = (
    "(lower(pool_config ->> 'enabled') IN ('1', 'on', 't', 'true', 'y', 'yes') "
    "OR pool_config -> 'enabled' = '1'::jsonb)"
)
