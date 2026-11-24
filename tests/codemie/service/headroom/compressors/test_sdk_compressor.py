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

"""Tests for the Headroom SDK compressor.

The real `headroom` package is never imported: a fake package is injected through `sys.modules`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import types
from unittest.mock import Mock, patch

import pytest

from codemie.service.headroom.compressors import sdk_compressor
from codemie.service.headroom.compressors.base import CompressOutcome, HeadroomCompressionError
from codemie.service.headroom.models import HeadroomStrategy, ResolvedHeadroomConfig
from codemie.service.headroom.compressors.sdk_compressor import SDKCompressor
from codemie.service.headroom.compressors.strategies import sdk_compress_kwargs

LOGGER = "codemie.service.headroom.compressors.sdk_compressor.logger"
SECRET_TEXT = "confidential message body"
MESSAGES = [{"role": "user", "content": SECRET_TEXT}]
COMPRESSED = [{"role": "user", "content": "short"}]
MODEL = "gpt-4o"
ENV_EXPECTED = {
    "HEADROOM_OFFLINE": "1",
    "HEADROOM_BEACON": "off",
    "DO_NOT_TRACK": "1",
    "HEADROOM_CCR_BACKEND": "memory",
}


class FakeCompressConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeResult:
    def __init__(self, messages, tokens_before, tokens_after):
        self.messages = messages
        self.tokens_before = tokens_before
        self.tokens_after = tokens_after


class FakeTransformPipeline:
    def __init__(self, transforms):
        self.transforms = transforms


class FakeContentRouterConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeContentRouter:
    def __init__(self, config):
        self.config = config


class FakeHeadroom(types.ModuleType):
    """Fake `headroom` package; records the env at the moment the SDK names are imported from it."""

    def __init__(self, compress_fn):
        super().__init__("headroom")
        self._compress_fn = compress_fn
        self.env_at_import: dict[str, str | None] | None = None
        self.import_count = 0

    def __getattr__(self, name):
        if name in ("compress", "CompressConfig"):
            self.import_count += 1
            self.env_at_import = {key: os.environ.get(key) for key in ENV_EXPECTED}
            return self._compress_fn if name == "compress" else FakeCompressConfig
        raise AttributeError(name)


class SdkEnv:
    def __init__(self, headroom: FakeHeadroom, compress_module: types.ModuleType):
        self.headroom = headroom
        self.compress_module = compress_module
        self.calls: list[dict] = []


def _cfg(strategy: HeadroomStrategy = HeadroomStrategy.BALANCED, user: bool = True) -> ResolvedHeadroomConfig:
    return ResolvedHeadroomConfig(compress_user_messages=user, compress_tool_outputs=True, strategy=strategy)


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    for key in ENV_EXPECTED:
        monkeypatch.delenv(key, raising=False)
    sdk_compressor._SDKRuntime.reset_for_tests()
    yield
    sdk_compressor._SDKRuntime.reset_for_tests()


@pytest.fixture
def sdk(monkeypatch) -> SdkEnv:
    holder: dict = {}

    def fake_compress(messages, model=None, config=None, **kwargs):
        holder["env"].calls.append({"messages": messages, "model": model, "config": config})
        return holder["result"]

    headroom = FakeHeadroom(fake_compress)
    compress_module = types.ModuleType("headroom.compress")
    compress_module._pipeline = "original-pipeline"
    transforms = types.ModuleType("headroom.transforms")
    transforms.TransformPipeline = FakeTransformPipeline
    router = types.ModuleType("headroom.transforms.content_router")
    router.ContentRouter = FakeContentRouter
    router.ContentRouterConfig = FakeContentRouterConfig

    kompress = types.ModuleType("headroom.transforms.kompress_compressor")
    kompress.is_kompress_available = lambda: holder["kompress"]

    env = SdkEnv(headroom, compress_module)
    holder["env"] = env
    holder["kompress"] = True
    env.set_kompress = lambda available: holder.__setitem__("kompress", available)
    holder["result"] = FakeResult(COMPRESSED, 100, 40)
    env.set_result = lambda result: holder.__setitem__("result", result)
    env.set_compress = lambda fn: setattr(headroom, "_compress_fn", fn)

    monkeypatch.setitem(sys.modules, "headroom", headroom)
    monkeypatch.setitem(sys.modules, "headroom.compress", compress_module)
    monkeypatch.setitem(sys.modules, "headroom.transforms", transforms)
    monkeypatch.setitem(sys.modules, "headroom.transforms.content_router", router)
    monkeypatch.setitem(sys.modules, "headroom.transforms.kompress_compressor", kompress)
    return env


def test_mode_is_sdk():
    assert SDKCompressor.mode == "sdk"


def test_sidecar_mode_never_imports_headroom():
    code = (
        "import sys\n"
        "from unittest.mock import patch\n"
        "from codemie.service.headroom import factory\n"
        "with patch('codemie.service.headroom.factory.config') as c:\n"
        "    c.HEADROOM_MODE = 'sidecar'\n"
        "    factory.get_compressor()\n"
        "sys.exit(1 if 'headroom' in sys.modules else 0)\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr[-500:]


def test_importing_module_does_not_import_headroom_or_create_a_runtime():
    code = (
        "import sys; import codemie.service.headroom.compressors.sdk_compressor as m; "
        "sys.exit(1 if 'headroom' in sys.modules or m._SDKRuntime._instance is not None else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr[-500:]


def test_module_holds_no_mutable_sdk_state():
    for name in ("_executor", "_sdk", "_unavailable", "_lock", "_load_sdk", "_reset_for_tests"):
        assert not hasattr(sdk_compressor, name), name


def test_runtime_is_a_singleton():
    assert sdk_compressor._SDKRuntime() is sdk_compressor._SDKRuntime()


def test_runtime_reset_yields_a_fresh_instance():
    runtime = sdk_compressor._SDKRuntime()
    runtime.unavailable = True

    sdk_compressor._SDKRuntime.reset_for_tests()
    fresh = sdk_compressor._SDKRuntime()

    assert fresh is not runtime
    assert fresh.sdk is None
    assert fresh.unavailable is False


def test_success_returns_outcome_and_passes_model_and_config(sdk):
    outcome = SDKCompressor().compress(MESSAGES, MODEL, _cfg())

    assert outcome == CompressOutcome(COMPRESSED, 100, 40)
    assert len(sdk.calls) == 1
    assert sdk.calls[0]["messages"] == MESSAGES
    assert sdk.calls[0]["model"] == MODEL


@pytest.mark.parametrize("strategy", list(HeadroomStrategy))
@pytest.mark.parametrize("user", [True, False])
def test_compress_config_kwargs_match_strategy_table(sdk, strategy, user):
    SDKCompressor().compress(MESSAGES, MODEL, _cfg(strategy, user))

    config = sdk.calls[0]["config"]
    assert isinstance(config, FakeCompressConfig)
    assert config.kwargs == sdk_compress_kwargs(strategy, user)


def test_offline_env_is_set_before_headroom_is_imported(sdk):
    SDKCompressor().compress(MESSAGES, MODEL, _cfg())

    assert sdk.headroom.env_at_import == ENV_EXPECTED


def test_privacy_env_is_forced_over_inherited_values_and_the_override_is_logged(sdk, monkeypatch):
    for key, value in {"DO_NOT_TRACK": "0", "HEADROOM_BEACON": "on", "HEADROOM_CCR_BACKEND": "sqlite"}.items():
        monkeypatch.setenv(key, value)

    with patch(LOGGER) as logger:
        SDKCompressor().compress(MESSAGES, MODEL, _cfg())

    assert os.environ["DO_NOT_TRACK"] == "1"
    assert os.environ["HEADROOM_BEACON"] == "off"
    assert os.environ["HEADROOM_CCR_BACKEND"] == "memory"
    assert sdk.headroom.env_at_import == ENV_EXPECTED
    logged = " ".join(call.args[0] for call in logger.warning.call_args_list)
    for key in ("DO_NOT_TRACK", "HEADROOM_BEACON", "HEADROOM_CCR_BACKEND"):
        assert key in logged
    assert "sqlite" not in logged  # names only: never the inherited values


def test_operator_controlled_offline_value_is_never_overwritten(sdk, monkeypatch):
    monkeypatch.setenv("HEADROOM_OFFLINE", "0")

    with patch(LOGGER) as logger:
        SDKCompressor().compress(MESSAGES, MODEL, _cfg())

    assert os.environ["HEADROOM_OFFLINE"] == "0"
    assert all("HEADROOM_OFFLINE" not in call.args[0] for call in logger.warning.call_args_list)


def test_offline_by_default_and_online_when_model_download_allowed(sdk, monkeypatch):
    SDKCompressor().compress(MESSAGES, MODEL, _cfg())
    assert os.environ["HEADROOM_OFFLINE"] == "1"

    monkeypatch.delenv("HEADROOM_OFFLINE")
    sdk_compressor._SDKRuntime.reset_for_tests()
    monkeypatch.setattr(sdk_compressor.config, "HEADROOM_SDK_ALLOW_MODEL_DOWNLOAD", True)
    SDKCompressor().compress(MESSAGES, MODEL, _cfg())

    assert os.environ["HEADROOM_OFFLINE"] == "0"


def test_unavailable_kompress_logs_one_warning_for_the_process(sdk):
    sdk.set_kompress(False)
    compressor = SDKCompressor()

    with patch(LOGGER) as mock_logger:
        compressor.compress(MESSAGES, MODEL, _cfg(HeadroomStrategy.BALANCED))
        compressor.compress(MESSAGES, MODEL, _cfg(HeadroomStrategy.AGGRESSIVE))

    mock_logger.warning.assert_called_once()
    assert "Kompress" in mock_logger.warning.call_args.args[0]
    assert len(sdk.calls) == 2  # compression still runs


def test_available_kompress_logs_nothing(sdk):
    with patch(LOGGER) as mock_logger:
        SDKCompressor().compress(MESSAGES, MODEL, _cfg())

    mock_logger.warning.assert_not_called()


def test_failing_kompress_probe_is_fail_open(sdk):
    def boom():
        raise RuntimeError("probe broke")

    sys.modules["headroom.transforms.kompress_compressor"].is_kompress_available = boom

    with patch(LOGGER) as mock_logger:
        outcome = SDKCompressor().compress(MESSAGES, MODEL, _cfg())

    assert outcome == CompressOutcome(COMPRESSED, 100, 40)
    mock_logger.warning.assert_called_once()
    assert "RuntimeError" in mock_logger.warning.call_args.args[0]


def test_slow_sdk_call_times_out_without_content(sdk, monkeypatch):
    release = threading.Event()
    sdk.set_compress(lambda *args, **kwargs: release.wait(5))
    monkeypatch.setattr(sdk_compressor.config, "HEADROOM_TIMEOUT_SECONDS", 0.05)

    try:
        with pytest.raises(HeadroomCompressionError) as exc_info:
            SDKCompressor().compress(MESSAGES, MODEL, _cfg())
    finally:
        release.set()

    assert SECRET_TEXT not in str(exc_info.value)
    assert "TimeoutError" in str(exc_info.value)


def test_headroom_is_imported_once_across_calls(sdk):
    compressor = SDKCompressor()
    compressor.compress(MESSAGES, MODEL, _cfg())
    compressor.compress(MESSAGES, MODEL, _cfg())

    assert sdk.headroom.import_count == 2  # CompressConfig and compress, from the one import statement
    assert len(sdk.calls) == 2


def test_zero_tokens_with_messages_raises(sdk):
    sdk.set_result(FakeResult(MESSAGES, 0, 0))

    with pytest.raises(HeadroomCompressionError):
        SDKCompressor().compress(MESSAGES, MODEL, _cfg())


def test_zero_tokens_with_empty_messages_is_not_an_error(sdk):
    sdk.set_result(FakeResult([], 0, 0))

    outcome = SDKCompressor().compress([], MODEL, _cfg())

    assert outcome == CompressOutcome([], 0, 0)


def test_compress_exception_is_wrapped_without_content(sdk):
    def boom(*args, **kwargs):
        raise RuntimeError(f"failed on {SECRET_TEXT}")

    sdk.set_compress(boom)

    with pytest.raises(HeadroomCompressionError) as exc_info:
        SDKCompressor().compress(MESSAGES, MODEL, _cfg())

    assert SECRET_TEXT not in str(exc_info.value)
    assert "RuntimeError" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_marker_free_pipeline_override_is_applied(sdk):
    SDKCompressor().compress(MESSAGES, MODEL, _cfg())

    pipeline = sdk.compress_module._pipeline
    assert isinstance(pipeline, FakeTransformPipeline)
    assert len(pipeline.transforms) == 1
    router = pipeline.transforms[0]
    assert isinstance(router, FakeContentRouter)
    assert router.config.kwargs == {"ccr_enabled": False, "ccr_inject_marker": False}


def test_pipeline_override_applied_once(sdk):
    compressor = SDKCompressor()
    compressor.compress(MESSAGES, MODEL, _cfg())
    first = sdk.compress_module._pipeline
    compressor.compress(MESSAGES, MODEL, _cfg())

    assert sdk.compress_module._pipeline is first


def test_failing_override_logs_one_warning_and_compress_succeeds(sdk, monkeypatch):
    monkeypatch.setitem(sys.modules, "headroom.transforms", None)  # import raises ImportError

    with patch(LOGGER) as mock_logger:
        compressor = SDKCompressor()
        first = compressor.compress(MESSAGES, MODEL, _cfg())
        second = compressor.compress(MESSAGES, MODEL, _cfg())

    assert first == second == CompressOutcome(COMPRESSED, 100, 40)
    assert sdk.compress_module._pipeline == "original-pipeline"
    mock_logger.warning.assert_called_once()
    assert "ModuleNotFoundError" in mock_logger.warning.call_args.args[0]


def test_missing_compress_module_logs_one_warning_and_compress_succeeds(sdk, monkeypatch):
    monkeypatch.delitem(sys.modules, "headroom.compress")

    with patch(LOGGER) as mock_logger:
        outcome = SDKCompressor().compress(MESSAGES, MODEL, _cfg())

    assert outcome == CompressOutcome(COMPRESSED, 100, 40)
    mock_logger.warning.assert_called_once()


def test_blocked_import_logs_once_and_is_not_retried(sdk, monkeypatch):
    monkeypatch.setitem(sys.modules, "headroom", None)
    compressor = SDKCompressor()

    with patch(LOGGER) as mock_logger:
        with pytest.raises(HeadroomCompressionError) as first:
            compressor.compress(MESSAGES, MODEL, _cfg())
        # Make the import succeed: a retry would now work, so a raise proves it is not retried.
        monkeypatch.setitem(sys.modules, "headroom", sdk.headroom)
        with pytest.raises(HeadroomCompressionError):
            compressor.compress(MESSAGES, MODEL, _cfg())

    assert SECRET_TEXT not in str(first.value)
    assert mock_logger.error.call_count + mock_logger.warning.call_count + mock_logger.info.call_count == 1
    assert sdk.headroom.import_count == 0
    assert sdk.calls == []


class BrokenHeadroom(types.ModuleType):
    """Fake `headroom` whose import fails with a non-ImportError, counting each attempt."""

    def __init__(self):
        super().__init__("headroom")
        self.attempts = 0

    def __getattr__(self, name):
        if name in ("compress", "CompressConfig"):
            self.attempts += 1
            raise OSError("native extension failed to load")
        raise AttributeError(name)


def test_non_import_error_disables_sdk_and_is_not_retried(sdk, monkeypatch):
    broken = BrokenHeadroom()
    monkeypatch.setitem(sys.modules, "headroom", broken)
    compressor = SDKCompressor()

    with patch(LOGGER) as mock_logger:
        for _ in range(3):
            with pytest.raises(HeadroomCompressionError) as exc_info:
                compressor.compress(MESSAGES, MODEL, _cfg())

    assert broken.attempts == 1
    assert SECRET_TEXT not in str(exc_info.value)
    assert mock_logger.error.call_count == 1
    assert "OSError" in mock_logger.error.call_args.args[0]


def test_override_is_skipped_with_one_warning_when_sdk_has_no_pipeline_attribute(sdk):
    del sdk.compress_module._pipeline

    with patch(LOGGER) as mock_logger:
        outcome = SDKCompressor().compress(MESSAGES, MODEL, _cfg())

    assert outcome == CompressOutcome(COMPRESSED, 100, 40)
    assert not hasattr(sdk.compress_module, "_pipeline")
    mock_logger.warning.assert_called_once()
    assert "AttributeError" in mock_logger.warning.call_args.args[0]


# --- contract with the real pinned package (skipped where headroom-ai is not installed) -------------------------


@pytest.fixture
def real_sdk(monkeypatch):
    pytest.importorskip("headroom")
    for key, value in ENV_EXPECTED.items():
        monkeypatch.setenv(key, value)  # registers the keys so the SDK load's os.environ writes are undone
    return sdk_compressor._SDKRuntime().load()


def test_real_sdk_pipeline_override_is_installed_and_read(real_sdk):
    from headroom.transforms import TransformPipeline
    from headroom.transforms.content_router import ContentRouter

    pipeline = sys.modules["headroom.compress"]._pipeline

    assert isinstance(pipeline, TransformPipeline)
    assert len(pipeline.transforms) == 1
    router = pipeline.transforms[0]
    assert isinstance(router, ContentRouter)
    assert router.config.ccr_enabled is False
    assert router.config.ccr_inject_marker is False
    # compress() reads the module attribute lazily through this accessor, so the override must be what it returns.
    assert sys.modules["headroom.compress"]._get_pipeline() is pipeline


@pytest.mark.parametrize("strategy", list(HeadroomStrategy))
@pytest.mark.parametrize("user", [True, False])
def test_real_compress_config_accepts_every_strategy_kwargs(real_sdk, strategy, user):
    real_sdk.compress_config_cls(**sdk_compress_kwargs(strategy, user))


def test_real_balanced_compression_leaves_no_retrieve_marker(real_sdk):
    log = "\n".join(
        f"2026-01-01 12:00:{i % 60:02d} INFO worker-{i % 3} processed request id={i} status=200 latency={i % 50}ms"
        for i in range(400)
    )
    messages = [
        {"role": "user", "content": "analyze the log"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": log},
    ]

    outcome = SDKCompressor().compress(messages, MODEL, _cfg(HeadroomStrategy.BALANCED))

    assert outcome.tokens_after < outcome.tokens_before
    assert "Retrieve more" not in str(outcome.messages)
    assert "hash=" not in str(outcome.messages)


# --- sidecar and SDK compressors: same strategy mapping, same normalized outcome shape (both mocked) --------------


def _sidecar_call(strategy, user, response_json):
    from codemie.service.headroom.compressors.sidecar_compressor import SidecarCompressor

    response = Mock(status_code=200)
    response.json.return_value = response_json
    with patch("codemie.service.headroom.compressors.sidecar_compressor.httpx.post", return_value=response) as post:
        outcome = SidecarCompressor().compress(MESSAGES, MODEL, _cfg(strategy, user))
    return outcome, post.call_args.kwargs["json"]


@pytest.mark.parametrize("strategy", list(HeadroomStrategy))
@pytest.mark.parametrize("user", [True, False])
def test_sidecar_and_sdk_derive_the_same_strategy_from_one_table(sdk, strategy, user):
    from codemie.service.headroom.compressors.strategies import sidecar_config

    sidecar_response = {"messages": COMPRESSED, "tokens_before": 100, "tokens_after": 40}
    _, sidecar_body = _sidecar_call(strategy, user, sidecar_response)
    SDKCompressor().compress(MESSAGES, MODEL, _cfg(strategy, user))
    sdk_kwargs = sdk.calls[0]["config"].kwargs

    # each side receives exactly what the single strategy table builds for it
    assert sidecar_body["config"] == sidecar_config(strategy, user)
    assert sdk_kwargs == sdk_compress_kwargs(strategy, user)
    # and the two bodies agree on everything strategy-dependent
    assert sidecar_body["config"]["compress_user_messages"] == sdk_kwargs["compress_user_messages"] == user
    assert (
        ("target_ratio" in sidecar_body["config"])
        == ("target_ratio" in sdk_kwargs)
        == (strategy == HeadroomStrategy.AGGRESSIVE)
    )
    assert sidecar_body["config"].get("mode") == ("lossy_inline" if strategy != HeadroomStrategy.LOSSLESS else None)
    assert sdk_kwargs.get("mode") == ("agent" if strategy == HeadroomStrategy.LOSSLESS else None)


def test_sidecar_and_sdk_distinct_raw_results_normalize_to_the_same_outcome_shape(sdk):
    sdk_messages = [{"role": "user", "content": "sdk-short"}]
    sdk.set_result(FakeResult(sdk_messages, 200, 50))

    sidecar_outcome, _ = _sidecar_call(
        HeadroomStrategy.BALANCED, True, {"messages": COMPRESSED, "tokens_before": 100, "tokens_after": 40}
    )
    sdk_outcome = SDKCompressor().compress(MESSAGES, MODEL, _cfg(HeadroomStrategy.BALANCED))

    assert type(sidecar_outcome) is type(sdk_outcome) is CompressOutcome
    assert sidecar_outcome == CompressOutcome(COMPRESSED, 100, 40)
    assert sdk_outcome == CompressOutcome(sdk_messages, 200, 50)
    assert [o.tokens_before - o.tokens_after for o in (sidecar_outcome, sdk_outcome)] == [60, 150]


@pytest.mark.skipif(
    os.environ.get("HEADROOM_TEST_KOMPRESS") != "1", reason="opt-in: needs the headroom-sdk-ml extra and a cached model"
)
def test_real_kompress_compresses_prose_in_sdk_mode(monkeypatch):
    """Parity check with the sidecar: prose is compressed in-process. Never runs in CI (no model download)."""
    pytest.importorskip("headroom")
    kompress = pytest.importorskip("headroom.transforms.kompress_compressor")
    assert kompress.is_kompress_available()
    monkeypatch.setenv("HEADROOM_OFFLINE", "0")
    sdk_compressor._SDKRuntime().load()
    prose = " ".join(f"Sentence number {i} explains in plain words why the deployment was delayed." for i in range(300))

    outcome = SDKCompressor().compress([{"role": "user", "content": prose}], MODEL, _cfg(HeadroomStrategy.BALANCED))

    assert outcome.tokens_after < outcome.tokens_before
