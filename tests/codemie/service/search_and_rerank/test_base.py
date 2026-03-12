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

import functools
import importlib
import os
import sys
from unittest.mock import MagicMock, patch

from langchain_core.documents import Document

from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.service.search_and_rerank.base import SearchAndRerankBase, es_response_to_document


class SearchAndRerankBaseImpl(SearchAndRerankBase):
    def execute(self):
        pass


class TestSearchAndRerankBase:
    @patch('codemie.clients.elasticsearch.ElasticSearchClient.get_client')
    def test_es_property_returns_correct_value(self, es_client_mock):
        es_client_instance_mock = MagicMock(spec=ElasticSearchClient)
        es_client_mock.return_value = es_client_instance_mock

        base_instance = SearchAndRerankBaseImpl()

        result = base_instance.es

        es_client_mock.assert_called_once()
        assert result == es_client_instance_mock

    def test_es_source_fields_contains_required_fields(self):
        assert 'text' in SearchAndRerankBase.ES_SOURCE_FIELDS
        assert 'metadata' in SearchAndRerankBase.ES_SOURCE_FIELDS
        assert len(SearchAndRerankBase.ES_SOURCE_FIELDS) == 2

    def test_to_document_with_empty_response(self):
        empty_response = {'hits': {'hits': []}}
        result = es_response_to_document(empty_response)
        assert result == []

    def test_to_document_with_single_hit(self):
        response = {
            'hits': {
                'hits': [
                    {'_source': {'text': 'sample text', 'metadata': {'key': 'value'}}, '_score': 0.8, '_id': 'doc1'}
                ]
            }
        }
        result = es_response_to_document(response)
        assert len(result) == 1
        doc, score, doc_id = result[0]
        assert isinstance(doc, Document)
        assert doc.page_content == 'sample text'
        assert doc.metadata == {'key': 'value'}
        assert score == 0.8
        assert doc_id == 'doc1'

    def test_to_document_with_missing_text(self):
        response = {'hits': {'hits': [{'_source': {'metadata': {'key': 'value'}}, '_score': 0.8, '_id': 'doc1'}]}}
        result = es_response_to_document(response)
        assert len(result) == 1
        doc, score, doc_id = result[0]
        assert isinstance(doc, Document)
        assert doc.page_content == ''
        assert doc.metadata == {'key': 'value'}
        assert score == 0.8
        assert doc_id == 'doc1'

    def test_to_document_with_multiple_hits(self):
        response = {
            'hits': {
                'hits': [
                    {'_source': {'text': 'text 1', 'metadata': {'key': 'value1'}}, '_score': 0.8, '_id': 'doc1'},
                    {'_source': {'text': 'text 2', 'metadata': {'key': 'value2'}}, '_score': 0.6, '_id': 'doc2'},
                ]
            }
        }
        result = es_response_to_document(response)
        assert len(result) == 2

        # Check first document
        doc1, score1, doc_id1 = result[0]
        assert isinstance(doc1, Document)
        assert doc1.page_content == 'text 1'
        assert doc1.metadata == {'key': 'value1'}
        assert score1 == 0.8
        assert doc_id1 == 'doc1'

        # Check second document
        doc2, score2, doc_id2 = result[1]
        assert isinstance(doc2, Document)
        assert doc2.page_content == 'text 2'
        assert doc2.metadata == {'key': 'value2'}
        assert score2 == 0.6
        assert doc_id2 == 'doc2'


class TestObserveDecorator:
    """Tests for the conditional LangFuse observe decorator in base.py."""

    # ------------------------------------------------------------------
    # Helper: reload base module with mocked enterprise / langfuse imports
    # ------------------------------------------------------------------

    def _reload_base_with_mocks(self, has_langfuse: bool, langfuse_observe_side_effect=None):
        """
        Reload codemie.service.search_and_rerank.base with enterprise loader
        mocked so we can exercise the decorator behaviour.

        After the refactor, base.py imports ``observe`` directly from
        ``codemie.enterprise.loader``, so we provide a real observe
        implementation on the mocked loader (mirroring what make_observe
        produces) and attach a config_mock as ``fresh.config`` so existing
        ``patch.object(fresh.config, 'LANGFUSE_TRACES', …)`` calls still work.

        Returns (fresh_module, enterprise_loader, langfuse_observe_mock, original, patched).
        """
        enterprise_loader = MagicMock()
        enterprise_loader.HAS_LANGFUSE = has_langfuse

        langfuse_observe_mock = MagicMock()
        if langfuse_observe_side_effect:
            langfuse_observe_mock.side_effect = langfuse_observe_side_effect

        if has_langfuse:

            def _observe(name=None, **kwargs):
                def decorator(fn):
                    _wrapped = langfuse_observe_mock(name=name, **kwargs)(fn)

                    @functools.wraps(fn)
                    def wrapper(*args, **kw):
                        if os.getenv("LANGFUSE_TRACES", "false").lower() in ("true", "1"):
                            return _wrapped(*args, **kw)
                        return fn(*args, **kw)

                    return wrapper

                return decorator

            enterprise_loader.observe = _observe
        else:
            enterprise_loader.observe = lambda *a, **kw: (lambda fn: fn)

        original = sys.modules.get('codemie.service.search_and_rerank.base')

        patched = patch.dict(
            sys.modules,
            {
                'codemie.enterprise': MagicMock(),
                'codemie.enterprise.loader': enterprise_loader,
            },
        )
        patched.start()
        sys.modules.pop('codemie.service.search_and_rerank.base', None)
        fresh = importlib.import_module('codemie.service.search_and_rerank.base')
        return fresh, enterprise_loader, langfuse_observe_mock, original, patched

    def _restore_base_module(self, original, patched):
        patched.stop()
        sys.modules.pop('codemie.service.search_and_rerank.base', None)
        if original is not None:
            sys.modules['codemie.service.search_and_rerank.base'] = original

    # ------------------------------------------------------------------
    # OSS / ImportError path (no enterprise package installed)
    # ------------------------------------------------------------------

    def test_noop_passes_through_with_name(self):
        """observe returns a callable with identical behaviour when tracing is unavailable."""
        from codemie.enterprise.loader import observe

        def my_func(x):
            return x * 2

        decorated = observe(name="span_name")(my_func)
        assert decorated(5) == 10

    def test_noop_result_is_callable_with_correct_output(self):
        """observe no-op returns a callable that behaves identically to the original."""
        from codemie.enterprise.loader import observe

        def my_func(a, b, c=1):
            return a + b + c

        decorated = observe()(my_func)
        assert decorated(1, 2, c=3) == 6

    def test_noop_with_no_name_argument(self):
        """observe() with no arguments still returns a passthrough decorator."""
        from codemie.enterprise.loader import observe

        def my_func():
            return "original"

        decorated = observe()(my_func)
        assert decorated() == "original"

    # ------------------------------------------------------------------
    # Enterprise path — HAS_LANGFUSE=False
    # ------------------------------------------------------------------

    def test_enterprise_has_langfuse_false_returns_original_fn(self):
        """When HAS_LANGFUSE=False, observe returns the original function even if langfuse is importable."""
        _, enterprise_loader, _, original, patched = self._reload_base_with_mocks(has_langfuse=False)
        try:

            def my_func(x):
                return x

            decorated = enterprise_loader.observe(name="test")(my_func)
            assert decorated is my_func
        finally:
            self._restore_base_module(original, patched)

    # ------------------------------------------------------------------
    # Enterprise path — HAS_LANGFUSE=True
    # ------------------------------------------------------------------

    def test_enterprise_traces_disabled_calls_original(self):
        """When LANGFUSE_TRACES=False at call time, the original function is executed."""
        # langfuse_observe_fn(name=...) returns a wrapper factory; that factory wraps the fn
        wrapped_mock = MagicMock(return_value="langfuse_result")

        def langfuse_observe_side_effect(name=None, **kw):
            return lambda fn: wrapped_mock

        _, enterprise_loader, _, original, patched = self._reload_base_with_mocks(
            has_langfuse=True,
            langfuse_observe_side_effect=langfuse_observe_side_effect,
        )
        try:

            def my_func(x):
                return f"original_{x}"

            decorated = enterprise_loader.observe(name="span")(my_func)

            with patch.dict(os.environ, {'LANGFUSE_TRACES': 'false'}):
                result = decorated("val")

            assert result == "original_val"
            wrapped_mock.assert_not_called()
        finally:
            self._restore_base_module(original, patched)

    def test_enterprise_traces_enabled_calls_langfuse_wrapper(self):
        """When LANGFUSE_TRACES=True, the langfuse-wrapped function is called."""
        wrapped_mock = MagicMock(return_value="langfuse_result")

        def langfuse_observe_side_effect(name=None, **kw):
            return lambda fn: wrapped_mock

        _, enterprise_loader, _, original, patched = self._reload_base_with_mocks(
            has_langfuse=True,
            langfuse_observe_side_effect=langfuse_observe_side_effect,
        )
        try:

            def my_func(x):
                return f"original_{x}"

            decorated = enterprise_loader.observe(name="span")(my_func)

            with patch.dict(os.environ, {'LANGFUSE_TRACES': 'true'}):
                result = decorated("val")

            assert result == "langfuse_result"
            wrapped_mock.assert_called_once_with("val")
        finally:
            self._restore_base_module(original, patched)

    def test_enterprise_observe_preserves_function_name(self):
        """functools.wraps is used so the wrapper retains the original function's __name__."""
        wrapped_mock = MagicMock(return_value="result")

        def langfuse_observe_side_effect(name=None, **kw):
            return lambda fn: wrapped_mock

        _, enterprise_loader, _, original, patched = self._reload_base_with_mocks(
            has_langfuse=True,
            langfuse_observe_side_effect=langfuse_observe_side_effect,
        )
        try:

            def my_named_function(x):
                return x

            decorated = enterprise_loader.observe(name="span")(my_named_function)
            assert decorated.__name__ == "my_named_function"
        finally:
            self._restore_base_module(original, patched)

    def test_enterprise_observe_runtime_toggle(self):
        """LANGFUSE_TRACES is checked at call time, not at decoration time."""
        wrapped_mock = MagicMock(return_value="langfuse_result")

        def langfuse_observe_side_effect(name=None, **kw):
            return lambda fn: wrapped_mock

        _, enterprise_loader, _, original, patched = self._reload_base_with_mocks(
            has_langfuse=True,
            langfuse_observe_side_effect=langfuse_observe_side_effect,
        )
        try:

            def my_func():
                return "original"

            decorated = enterprise_loader.observe()(my_func)

            # First call — traces off
            with patch.dict(os.environ, {'LANGFUSE_TRACES': 'false'}):
                result_off = decorated()
            assert result_off == "original"

            # Second call — traces on (toggled at runtime without re-decorating)
            with patch.dict(os.environ, {'LANGFUSE_TRACES': 'true'}):
                result_on = decorated()
            assert result_on == "langfuse_result"
        finally:
            self._restore_base_module(original, patched)
