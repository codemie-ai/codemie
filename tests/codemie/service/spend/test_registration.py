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

"""Startup registration of the spend ledgers.

The registration is where a deployment's shape becomes the registry's shape, so the cases worth
testing are the deployment shapes - not the happy path.
"""

from unittest.mock import patch

import pytest

from codemie.service.spend.composition import register_spend_ledgers
from codemie.service.spend.ledger_registry import get_spend_ledger, reset_ledgers
from codemie.service.spend.routing import LLMCallClass


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_ledgers()
    yield
    reset_ledgers()


def routing(mode: str, *, embeddings_disabled: bool = False, has_litellm: bool = True, enabled: bool = True):
    """Patch the routing predicate's inputs, which is what decides the split."""
    from codemie.configs import config

    return (
        patch.object(config, "LLM_PROXY_MODE", mode),
        patch.object(config, "LLM_PROXY_ENABLED", enabled),
        patch.object(config, "LLM_PROXY_EMBEDDINGS_DISABLED", embeddings_disabled),
        patch("codemie.enterprise.HAS_LITELLM", has_litellm),
    )


def _apply(patches):
    for one in patches:
        one.start()
    return patches


def _undo(patches):
    for one in patches:
        one.stop()


class TestWhatTheDefaultLedgerIsScopedTo:
    def test_a_wholly_non_litellm_deployment_gets_both_classes(self):
        patches = _apply(routing("internal"))
        try:
            register_spend_ledgers()
            for call_class in LLMCallClass:
                assert get_spend_ledger(call_class).ledger_name == "default"
        finally:
            _undo(patches)

    def test_the_mixed_configuration_registers_a_default_ledger_for_embeddings_only(self):
        """Chat routes to LiteLLM, so a default ledger scoped to chat would double-count it."""
        patches = _apply(routing("lite_llm", embeddings_disabled=True))
        try:
            register_spend_ledgers()
            assert get_spend_ledger(LLMCallClass.EMBEDDINGS).ledger_name == "default"
            # No LiteLLM ledger registered in this test, so chat is unavailable rather than
            # borrowing the embeddings-scoped ledger.
            assert get_spend_ledger(LLMCallClass.CHAT).ledger_name == "noop"
        finally:
            _undo(patches)

    def test_a_fully_litellm_deployment_registers_no_default_ledger_at_all(self):
        """An unscoped default ledger is the double-count hazard, and here it would serve nothing."""
        patches = _apply(routing("lite_llm"))
        try:
            register_spend_ledgers()
            for call_class in LLMCallClass:
                assert get_spend_ledger(call_class).ledger_name == "noop"
        finally:
            _undo(patches)

    def test_registration_is_idempotent(self):
        patches = _apply(routing("internal"))
        try:
            register_spend_ledgers()
            register_spend_ledgers()
            assert get_spend_ledger(LLMCallClass.CHAT).ledger_name == "default"
        finally:
            _undo(patches)


class TestADeploymentWithoutElasticsearch:
    def test_registers_no_default_ledger_so_the_native_classes_answer_unavailable_quietly(self):
        """Standalone mode (EPMCDME-14347): a default ledger would fail, log and count every read."""
        patches = _apply(routing("internal"))
        try:
            with patch("codemie.clients.elasticsearch.ElasticSearchClient.is_configured", return_value=False):
                register_spend_ledgers()
            for call_class in LLMCallClass:
                assert get_spend_ledger(call_class).ledger_name == "noop"
        finally:
            _undo(patches)
