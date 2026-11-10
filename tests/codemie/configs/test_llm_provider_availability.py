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

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from codemie.configs.llm_config import LLMProvider, ModelCategory
from codemie.configs.llm_provider_availability import core_availability
from codemie.rest_api.dependencies.core import require_core

AZURE_PLACEHOLDER = "<your-azure-openai-key>"


@pytest.fixture(autouse=True)
def _clear_cache():
    core_availability.cache_clear()
    yield
    core_availability.cache_clear()


def _cfg(**kw):
    base = {
        "AZURE_OPENAI_API_KEY": "",
        "AZURE_OPENAI_URL": "",
        "AWS_BEDROCK_REGION": "",
        "GOOGLE_PROJECT_ID": "",
        "GOOGLE_VERTEXAI_REGION": "",
        "GOOGLE_CLAUDE_VERTEXAI_REGION": "",
        "ANTHROPIC_API_KEY": "",
        "LITE_LLM_URL": "",
        "LITE_LLM_APP_KEY": "",
        "LLM_PROXY_ENABLED": False,
        "LLM_PROXY_MODE": "internal",
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _run(cfg, provider, litellm=False, aws_region=None):
    models = []
    if provider is not None:
        models = [SimpleNamespace(enabled=True, provider=provider, is_default_for=lambda c: c == ModelCategory.GLOBAL)]
    fake_llm_config = SimpleNamespace(llm_models=models)
    with (
        patch("codemie.configs.llm_config.llm_config", fake_llm_config),
        patch("codemie.enterprise.litellm.is_litellm_enabled", return_value=litellm),
        patch("boto3.Session", return_value=SimpleNamespace(region_name=aws_region)),
    ):
        return core_availability(cfg)


def test_azure_key_and_url_available():
    cfg = _cfg(AZURE_OPENAI_API_KEY="k", AZURE_OPENAI_URL="https://x")
    assert _run(cfg, LLMProvider.AZURE_OPENAI) == (True, "azure_openai", [])


def test_azure_key_without_url_is_unavailable():
    cfg = _cfg(AZURE_OPENAI_API_KEY="k")
    assert _run(cfg, LLMProvider.AZURE_OPENAI) == (False, "azure_openai", ["AZURE_OPENAI_URL"])


def test_azure_nothing_set_lists_both():
    ok, _, missing = _run(_cfg(), LLMProvider.AZURE_OPENAI)
    assert not ok and missing == ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_URL"]


def test_placeholder_and_whitespace_count_as_set():
    cfg = _cfg(AZURE_OPENAI_API_KEY=AZURE_PLACEHOLDER, AZURE_OPENAI_URL="   ")
    assert _run(cfg, LLMProvider.AZURE_OPENAI)[0] is True


def test_bedrock_region_setting_available_without_credentials():
    assert _run(_cfg(AWS_BEDROCK_REGION="us-east-1"), LLMProvider.AWS_BEDROCK) == (True, "aws_bedrock", [])


def test_bedrock_region_from_aws_config():
    assert _run(_cfg(), LLMProvider.AWS_BEDROCK, aws_region="eu-west-1")[0] is True


def test_bedrock_no_region_unavailable():
    ok, provider, missing = _run(_cfg(), LLMProvider.AWS_BEDROCK)
    assert (ok, provider) == (False, "aws_bedrock") and missing == ["AWS_BEDROCK_REGION"]


def test_vertex_needs_project_and_region():
    assert _run(_cfg(GOOGLE_PROJECT_ID="p", GOOGLE_VERTEXAI_REGION="r"), LLMProvider.GOOGLE_VERTEX_AI)[0]
    ok, _, missing = _run(_cfg(GOOGLE_PROJECT_ID="p"), LLMProvider.GOOGLE_VERTEX_AI)
    assert not ok and missing == ["GOOGLE_VERTEXAI_REGION"]


def test_vertex_anthropic_needs_project_and_claude_region():
    cfg = _cfg(GOOGLE_PROJECT_ID="p", GOOGLE_VERTEXAI_REGION="r")
    ok, _, missing = _run(cfg, LLMProvider.VERTEX_AI_ANTHROPIC)
    assert not ok and missing == ["GOOGLE_CLAUDE_VERTEXAI_REGION"]
    cfg = _cfg(GOOGLE_PROJECT_ID="p", GOOGLE_CLAUDE_VERTEXAI_REGION="r")
    assert _run(cfg, LLMProvider.VERTEX_AI_ANTHROPIC)[0]


def test_anthropic_key():
    assert _run(_cfg(ANTHROPIC_API_KEY="k"), LLMProvider.ANTHROPIC) == (True, "anthropic", [])
    assert _run(_cfg(), LLMProvider.ANTHROPIC) == (False, "anthropic", ["ANTHROPIC_API_KEY"])


def test_litellm_proxy_takes_precedence_over_model_provider():
    cfg = _cfg(LLM_PROXY_MODE="lite_llm", LITE_LLM_URL="u", LITE_LLM_APP_KEY="k")
    assert _run(cfg, LLMProvider.AZURE_OPENAI, litellm=True) == (True, "litellm_proxy", [])
    ok, provider, missing = _run(_cfg(LLM_PROXY_MODE="lite_llm", LITE_LLM_URL="u"), None, litellm=True)
    assert (ok, provider, missing) == (False, "litellm_proxy", ["LITE_LLM_APP_KEY"])


def test_proxy_enabled_with_other_mode_uses_model_rule():
    cfg = _cfg(LLM_PROXY_ENABLED=True, LLM_PROXY_MODE="internal", ANTHROPIC_API_KEY="k")
    assert _run(cfg, LLMProvider.ANTHROPIC, litellm=True) == (True, "anthropic", [])


def test_no_default_model_unavailable():
    ok, provider, missing = _run(_cfg(), None)
    assert not ok and provider is None and missing


def test_unknown_provider_unavailable():
    ok, provider, missing = _run(_cfg(), "weird")
    assert not ok and missing


def test_only_stored_credentials_is_unavailable():
    # Stored per-project/per-user credentials are never read: empty settings -> unavailable.
    assert _run(_cfg(), LLMProvider.AZURE_OPENAI)[0] is False


def test_exception_yields_unavailable_and_error_log(caplog, monkeypatch):
    monkeypatch.setattr(logging.getLogger("codemie.configs.llm_provider_availability"), "propagate", True)
    monkeypatch.setattr(logging.getLogger("codemie"), "propagate", True)
    with (
        patch("codemie.enterprise.litellm.is_litellm_enabled", side_effect=RuntimeError("boom")),
        caplog.at_level(logging.ERROR),
    ):
        result = core_availability(_cfg())
    assert result[0] is False and result[1] is None
    assert any(r.levelno == logging.ERROR and "Core gating:" in r.getMessage() for r in caplog.records)


def test_failure_is_not_cached(monkeypatch):
    cfg = _cfg(AZURE_OPENAI_API_KEY="k", AZURE_OPENAI_URL="u")
    with patch("codemie.enterprise.litellm.is_litellm_enabled", side_effect=RuntimeError("boom")):
        assert core_availability(cfg)[0] is False
    assert _run(cfg, LLMProvider.AZURE_OPENAI)[0] is True


def test_require_core_noop_when_not_standalone(monkeypatch):
    from codemie.configs.config import config

    monkeypatch.setattr(config, "STANDALONE", False, raising=False)
    with patch("codemie.configs.llm_provider_availability.core_availability", return_value=(False, None, ["x"])):
        assert require_core() is None


def test_require_core_404_when_off(monkeypatch):
    from codemie.configs.config import config

    monkeypatch.setattr(config, "STANDALONE", True, raising=False)
    with patch("codemie.configs.llm_provider_availability.core_availability", return_value=(False, None, ["x"])):
        with pytest.raises(HTTPException) as exc:
            require_core()
    assert exc.value.status_code == 404


def test_require_core_passes_when_on(monkeypatch):
    from codemie.configs.config import config

    monkeypatch.setattr(config, "STANDALONE", True, raising=False)
    with patch("codemie.configs.llm_provider_availability.core_availability", return_value=(True, "a", [])):
        assert require_core() is None
