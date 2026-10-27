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

"""Validator library ported from assistants-triage-poc.

INTENTIONALLY EMPTY. Do not eagerly import Engine / similarity here — a
re-export would make every importer of this package pay for the engine.
Callers that need the audit engine import it directly via the submodule:

    from codemie.service.marketplace_management.validator.engine import Engine
    from codemie.service.marketplace_management.validator.config import load_config

Lightweight modules (models, config) can be imported freely:

    from codemie.service.marketplace_management.validator.models import (
        AssistantRecord, Verdict, Bucket,
    )

Historical note: this docstring used to warn that `similarity.py` required
sklearn and that the MiniLM backend needed sentence-transformers. Neither is
true any more. The dense-vector path was deleted in EXECUTION-PLAN 9.1 (F-71)
because no wired backend ever produced a vector, and with it went the last
sklearn and scipy imports in the package. `numpy` remains, for the usage-tier
percentiles.
"""
