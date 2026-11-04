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

"""Speech-to-text service: model selection, client routing (LiteLLM first) and observability."""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, BinaryIO, Mapping

import openai

from codemie.configs import config, logger
from codemie.configs.llm_config import LLMModel, SttConfig
from codemie.configs.logger import current_user_email, logging_user_id
from codemie.core.exceptions import ExtendedHTTPException, UnknownSttModelError
from codemie.enterprise.observability import get_observability_provider
from codemie.service.llm_service.llm_service import llm_service

if TYPE_CHECKING:
    from codemie.rest_api.security.user import User

GENERATION_NAME = "stt.transcribe"
TRACE_NAME = "Audio Transcription"

# LiteLLM relays upstream provider response headers to the client under this prefix.
_GATEWAY_HEADER_PREFIX = "llm_provider-"


def _lazy_observe(**kwargs: Any):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **wkwargs):
            return get_observability_provider().make_observe_decorator()(**kwargs)(fn)(*args, **wkwargs)

        return wrapper

    return decorator


@dataclass
class SttResult:
    text: str
    headers: dict[str, str] = field(default_factory=dict)
    fields: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int | float] | None = field(default=None, compare=False)

    @property
    def metadata(self) -> dict[str, Any]:
        return {**self.headers, **self.fields}


def _name_matches(name: str, patterns: list[str]) -> bool:
    lowered = name.lower()
    for pattern in patterns:
        wanted = pattern.strip().lower()
        if wanted.endswith("*") and lowered.startswith(wanted[:-1]):
            return True
        if lowered == wanted:
            return True
    return False


def _narrow_patterns(patterns: list[str], field: str) -> list[str]:
    """Drop a bare "*" pattern: it would match every header name, bypassing the deny-lists below."""
    narrowed = [p for p in patterns if p.strip() != "*"]
    if len(narrowed) != len(patterns):
        logger.warning(f"STT {field} lists a bare '*' pattern; it is ignored. Name the headers to allow.")
    return narrowed


def _configured_blocklist() -> set[str]:
    return {h.strip().lower() for h in config.FORWARDED_HEADERS_BLOCKLIST.split(",") if h.strip()}


def _blocked_request_headers() -> set[str]:
    from codemie.enterprise.litellm.proxy_router import PROXY_HOP_BY_HOP_HEADERS

    return _configured_blocklist() | PROXY_HOP_BY_HOP_HEADERS


def _blocked_response_headers() -> set[str]:
    from codemie.enterprise.litellm.proxy_router import PROXY_HOP_BY_HOP_HEADERS, PROXY_RESPONSE_HOP_BY_HOP_HEADERS

    return _configured_blocklist() | PROXY_HOP_BY_HOP_HEADERS | PROXY_RESPONSE_HOP_BY_HOP_HEADERS


def _is_safe_response_header(name: str, value: str, blocked: set[str]) -> bool:
    if name in blocked:
        return False
    try:
        value.encode("latin-1")
    except UnicodeEncodeError:
        return False
    return True


def select_forward_headers(request_headers: Mapping[str, str] | None, stt_config: SttConfig | None) -> dict[str, str]:
    patterns = stt_config.forward_request_headers if stt_config else []
    if not request_headers or not patterns:
        return {}
    patterns = _narrow_patterns(patterns, "forward_request_headers")
    blocked = _blocked_request_headers()
    return {k: v for k, v in request_headers.items() if k.lower() not in blocked and _name_matches(k, patterns)}


def _collect_response_headers(headers: Mapping[str, str] | None, stt_config: SttConfig | None) -> dict[str, str]:
    patterns = stt_config.response_headers if stt_config else []
    if not headers or not patterns:
        return {}
    patterns = _narrow_patterns(patterns, "response_headers")
    collected: dict[str, str] = {}
    blocked = _blocked_response_headers()
    # Provider headers (llm_provider- prefix) are handled last, so they win over a gateway header of the same name.
    for name, value in sorted(headers.items(), key=lambda item: item[0].lower().startswith(_GATEWAY_HEADER_PREFIX)):
        bare = name.lower().removeprefix(_GATEWAY_HEADER_PREFIX)
        if _name_matches(bare, patterns) and _is_safe_response_header(bare, value, blocked):
            collected[bare] = value
    return collected


def _collect_response_fields(parsed: Any, stt_config: SttConfig | None) -> dict[str, Any]:
    fields = stt_config.response_body_fields if stt_config else []
    if not fields:
        return {}
    extra = getattr(parsed, "model_extra", None) or {}
    return {k: v for k, v in extra.items() if v is not None and _name_matches(k, fields)}


_PASSTHROUGH_STATUS_CODES = {400, 413, 429, 503, 504}


def _not_configured(details: str) -> ExtendedHTTPException:
    return ExtendedHTTPException(
        code=503,
        message="Speech recognition is not configured",
        details=details,
        help="Ask the administrator to check the speech-to-text settings.",
    )


def _to_http_error(error: Exception) -> ExtendedHTTPException | None:
    """Map OpenAI-style provider errors to the status the caller can act on; None for other errors."""
    if isinstance(error, openai.APIStatusError):
        code = error.status_code if error.status_code in _PASSTHROUGH_STATUS_CODES else 502
        return ExtendedHTTPException(
            code=code,
            message="Speech recognition failed",
            details=getattr(error, "message", None) or str(error),
            help="Check the audio (format, size, duration) and try again.",
        )
    if isinstance(error, openai.APITimeoutError):
        return ExtendedHTTPException(
            code=504, message="Speech recognition timed out", details=str(error), help="Try a shorter recording."
        )
    if isinstance(error, openai.APIConnectionError):
        return ExtendedHTTPException(
            code=502, message="Speech recognition service is unreachable", details=str(error), help=""
        )
    return None


def _usage_details(response: Any) -> dict[str, int | float] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    data = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
    if data.get("type") == "duration" and data.get("seconds") is not None:
        return {"audio_seconds": data["seconds"]}
    mapped = {
        "input": data.get("input_tokens"),
        "output": data.get("output_tokens"),
        "total": data.get("total_tokens"),
    }
    return {key: value for key, value in mapped.items() if value is not None} or None


@dataclass
class _SttCall:
    client: Any
    model_id: str
    language: str | None
    stt_config: SttConfig | None
    request_headers: dict[str, str]
    trace_input: dict[str, Any]
    context: dict[str, Any]


class SttService:
    def resolve_language(self, request_language: str | None, stt_config: SttConfig | None = None) -> str | None:
        model_language = stt_config.default_language if stt_config else None
        return request_language or model_language or None

    @staticmethod
    def _build_legacy_client() -> Any:
        from codemie.core.dependecies import get_stt_openai_client

        if not config.STT_API_KEY:
            raise _not_configured("No speech-to-text model is configured.")
        try:
            return get_stt_openai_client()
        except ValueError as error:
            logger.error(f"STT client from STT_API_* settings could not be created: {error}")
            raise _not_configured("The STT_API_* settings are incomplete.") from error

    def _build_client(self, model: LLMModel | None) -> tuple[Any, str, bool]:
        if model is None or llm_service.is_legacy_stt_model(model):
            return self._build_legacy_client(), config.STT_MODEL_NAME, False

        from codemie.enterprise.litellm import get_litellm_stt_client
        from codemie.core.dependecies import litellm_context

        client = get_litellm_stt_client(
            llm_model_details=model,
            litellm_context=litellm_context.get(None),
            user_email=current_user_email.get(None),
            user_id=logging_user_id.get(None),
        )
        if client is None:
            raise _not_configured(f"LiteLLM is not available for the STT model: {model.base_name}")
        return client, model.base_name, True

    @_lazy_observe(name=TRACE_NAME, as_type="span", capture_input=False, capture_output=False)
    def transcribe(
        self,
        file: BinaryIO,
        user: User | None = None,
        model_name: str | None = None,
        language: str | None = None,
        forward_headers: Mapping[str, str] | None = None,
        conversation_id: str | None = None,
    ) -> SttResult:
        if user is None and model_name:
            raise UnknownSttModelError("A model can be chosen only for an authenticated user.")
        model = llm_service.resolve_stt_model(user, model_name) if user else None
        stt_config = model.stt_config if model else None
        if user and model and not llm_service.is_legacy_stt_model(model):
            from codemie.service.llm_service.utils import set_llm_context

            set_llm_context(None, user.current_project, user)
        call = self._prepare_call(
            file, model, language, select_forward_headers(forward_headers, stt_config), conversation_id
        )

        # Trace-level fields are set here, on the outer span: Phoenix renames the active span for a trace name,
        # so the nested generation keeps its own name.
        provider = get_observability_provider()
        if provider.is_enabled():
            provider.update_current_trace(
                name=TRACE_NAME,
                user_id=getattr(user, "id", None),
                session_id=conversation_id,
                tags=["stt", call.model_id],
                input=call.trace_input,
                metadata={**call.context, **call.request_headers},
            )
        result = self._transcribe(file, call)
        if provider.is_enabled():
            provider.update_current_trace(
                output=result.text, metadata={**call.request_headers, **result.metadata, **_outcome(result)}
            )
        logger.debug(f"STT completed. Model={call.model_id}. Language={call.language}. Chars={len(result.text)}")
        return result

    def _prepare_call(
        self,
        file: BinaryIO,
        model: LLMModel | None,
        language: str | None,
        request_headers: dict[str, str],
        conversation_id: str | None,
    ) -> _SttCall:
        stt_config = model.stt_config if model else None
        lang = self.resolve_language(language, stt_config)
        client, model_id, via_litellm = self._build_client(model)
        audio_name = getattr(file, "name", None)
        context = _trace_context(
            type="speech-to-text",
            model=model_id,
            model_label=getattr(model, "label", None),
            language=lang,
            language_source=_language_source(language, lang),
            via_litellm=via_litellm,
            audio_file=audio_name,
            audio_bytes=_size(file),
            conversation_id=conversation_id,
        )
        return _SttCall(
            client=client,
            model_id=model_id,
            language=lang,
            stt_config=stt_config,
            request_headers=request_headers,
            trace_input=_trace_context(audio_file=audio_name, language=lang),
            context=context,
        )

    @_lazy_observe(name=GENERATION_NAME, as_type="generation", capture_input=False, capture_output=False)
    def _transcribe(self, file: BinaryIO, call: _SttCall) -> SttResult:
        provider = get_observability_provider()
        if provider.is_enabled():
            provider.update_current_generation(
                model=call.model_id,
                model_parameters={"language": call.language} if call.language else None,
                input=call.trace_input,
                metadata={**call.context, **call.request_headers},
            )

        kwargs: dict[str, Any] = {"model": call.model_id, "file": file}
        if call.language:
            kwargs["language"] = call.language
        if call.request_headers:
            kwargs["extra_headers"] = call.request_headers

        try:
            raw = call.client.audio.transcriptions.with_raw_response.create(**kwargs)
            response = raw.parse()
        except Exception as e:
            if provider.is_enabled():
                provider.update_current_generation(level="ERROR", status_message=str(e))
            http_error = _to_http_error(e)
            if http_error:
                raise http_error from e
            raise

        result = SttResult(
            text=response.text,
            headers=_collect_response_headers(raw.headers, call.stt_config),
            fields=_collect_response_fields(response, call.stt_config),
            usage=_usage_details(response),
        )
        if provider.is_enabled():
            provider.update_current_generation(
                output=result.text,
                usage_details=result.usage,
                metadata={**call.request_headers, **result.metadata, **_outcome(result)},
            )
        return result


def _language_source(request_language: str | None, resolved_language: str | None) -> str:
    if request_language:
        return "request"
    return "model_default" if resolved_language else "provider_auto"


def _outcome(result: SttResult) -> dict[str, Any]:
    return _trace_context(transcript_chars=len(result.text), usage=result.usage)


def _trace_context(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _size(file: BinaryIO) -> int | None:
    getbuffer = getattr(file, "getbuffer", None)
    return len(getbuffer()) if getbuffer else None


stt_service = SttService()
