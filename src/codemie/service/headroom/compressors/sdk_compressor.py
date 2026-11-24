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

"""Headroom compressor that runs the Headroom SDK in-process.

The SDK is imported lazily, on first use, because the offline/no-telemetry environment must be in place
before `headroom` is imported and because `headroom-ai` may be absent from the environment.
"""

from __future__ import annotations

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, ClassVar, NamedTuple

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.service.headroom.compressors.base import CompressOutcome, HeadroomCompressionError
from codemie.service.headroom.models import ResolvedHeadroomConfig
from codemie.service.headroom.compressors.strategies import sdk_compress_kwargs


# Forced: no telemetry and an in-memory CCR store (the SDK default would create $HOME/.headroom/ccr_store.db).
_FORCED_SDK_ENV = {
    "HEADROOM_BEACON": "off",
    "DO_NOT_TRACK": "1",
    "HEADROOM_CCR_BACKEND": "memory",
}


def _operator_sdk_env_defaults() -> dict[str, str]:
    """Operator-controllable default: offline unless the operator allows the Kompress model download."""
    return {"HEADROOM_OFFLINE": "0" if config.HEADROOM_SDK_ALLOW_MODEL_DOWNLOAD else "1"}


class _SDK(NamedTuple):
    compress: Any
    compress_config_cls: Any


class _SDKRuntime:
    """Process-wide SDK state: lazy lock-guarded load, sticky failure flag, timeout worker pool."""

    _instance: ClassVar[_SDKRuntime | None] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()

    sdk: _SDK | None
    unavailable: bool
    executor: ThreadPoolExecutor
    _load_lock: threading.Lock

    def __new__(cls) -> _SDKRuntime:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialize()
                    cls._instance = instance
        return cls._instance

    def _initialize(self) -> None:
        self.sdk = None
        self.unavailable = False
        self._load_lock = threading.Lock()
        # The SDK call is synchronous and cannot be interrupted, so it runs in a worker to honor
        # HEADROOM_TIMEOUT_SECONDS. A timed-out call keeps its worker until the SDK returns; the pool bound caps
        # how many can pile up.
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="headroom-sdk")

    def load(self) -> _SDK:
        """Import the SDK once per process; after a failed import the compressor stays disabled."""
        if self.sdk is not None:
            return self.sdk
        with self._load_lock:
            if self.sdk is not None:
                return self.sdk
            if self.unavailable:
                raise HeadroomCompressionError("Headroom SDK is unavailable")

            _apply_sdk_env()
            try:
                from headroom import CompressConfig, compress
            except Exception as exc:  # not only ImportError: a broken native extension must disable the SDK once too
                self.unavailable = True
                logger.error(f"Headroom SDK import failed ({type(exc).__name__}); SDK compression is disabled")
                raise HeadroomCompressionError("Headroom SDK is unavailable") from exc

            _apply_marker_free_pipeline()
            _warn_if_kompress_unavailable()
            self.sdk = _SDK(compress=compress, compress_config_cls=CompressConfig)
            return self.sdk

    @classmethod
    def reset_for_tests(cls) -> None:
        with cls._lock:
            instance, cls._instance = cls._instance, None
        if instance is not None:
            instance.executor.shutdown(wait=False, cancel_futures=True)


def _apply_sdk_env() -> None:
    """Set the SDK's environment; it is process-wide and read by the SDK on every call, so it stays set.

    The privacy keys are forced (an inherited value is overridden and its name logged, never the value);
    HEADROOM_OFFLINE keeps an operator-set value.
    """
    for key, value in _FORCED_SDK_ENV.items():
        inherited = os.environ.get(key)
        if inherited is not None and inherited != value:
            logger.warning(f"Headroom SDK mode overrides inherited {key}; it is forced for privacy")
        os.environ[key] = value
    for key, value in _operator_sdk_env_defaults().items():
        os.environ.setdefault(key, value)


def _warn_if_kompress_unavailable() -> None:
    """Log one warning when the Kompress ML compressor cannot run; never raises.

    Without it `balanced` and `aggressive` compress structured content only (the sidecar image ships Kompress).
    """
    try:
        from headroom.transforms.kompress_compressor import is_kompress_available

        if is_kompress_available():
            return
        reason = "unavailable"
    except Exception as exc:
        reason = f"check failed ({type(exc).__name__})"
    logger.warning(
        f"Headroom Kompress is {reason} in SDK mode: balanced/aggressive compress structured content only. "
        "Install the headroom-sdk-ml extra and set HEADROOM_SDK_ALLOW_MODEL_DOWNLOAD=true (or pre-cache the model)."
    )


def _apply_marker_free_pipeline() -> None:
    """Replace the SDK's default pipeline with one that emits no unresolvable CCR retrieve markers.

    Touches a private SDK attribute, so a failure only logs a warning and compression continues unchanged.
    """
    try:
        from headroom.transforms import TransformPipeline
        from headroom.transforms.content_router import ContentRouter, ContentRouterConfig

        # `headroom.compress` on the package is the function, so the module is reached via sys.modules.
        compress_module = sys.modules["headroom.compress"]
        if not hasattr(compress_module, "_pipeline"):
            raise AttributeError("_pipeline")  # the SDK no longer exposes the attribute this override relies on
        compress_module._pipeline = TransformPipeline(
            transforms=[ContentRouter(ContentRouterConfig(ccr_enabled=False, ccr_inject_marker=False))]
        )
    except Exception as exc:
        logger.warning(f"Headroom SDK marker-free pipeline override not applied ({type(exc).__name__})")


class SDKCompressor:
    """In-process compressor backed by the Headroom SDK.

    Error messages carry only exception types: never message content.
    """

    mode = "sdk"

    def compress(self, messages: list[dict[str, Any]], model: str, cfg: ResolvedHeadroomConfig) -> CompressOutcome:
        runtime = _SDKRuntime()
        sdk = runtime.load()
        try:
            compress_config = sdk.compress_config_cls(**sdk_compress_kwargs(cfg.strategy, cfg.compress_user_messages))
            future = runtime.executor.submit(sdk.compress, messages, model=model, config=compress_config)
            try:
                result = future.result(timeout=config.HEADROOM_TIMEOUT_SECONDS)
            except FutureTimeoutError:
                future.cancel()
                raise
            outcome = CompressOutcome(result.messages, result.tokens_before, result.tokens_after)
        except Exception as exc:
            raise HeadroomCompressionError(f"Headroom SDK compression failed ({type(exc).__name__})") from exc

        # The SDK swallows its own errors and returns the original messages with zero token counts.
        if messages and outcome.tokens_before == 0 and outcome.tokens_after == 0:
            raise HeadroomCompressionError("Headroom SDK reported a swallowed compression failure")
        return outcome
