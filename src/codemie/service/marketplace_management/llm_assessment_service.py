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

"""LLM-based quality assessment of marketplace assistants.

EXECUTION-PLAN 8.3 and 8.4 — traces F-21, F-22, F-23, F-75, S5, S15, R7.

Four properties this module now owns, each of which was previously absent:

**The submission is untrusted input, and is treated as such** (8.3, F-21/S5).
`_format_assistant_input` caps every field, strips the operator's sentinels and
defangs fence-like lines before the text is placed between `UNTRUSTED_BEGIN` and
`UNTRUSTED_END`. Before this, the raw `system_prompt` went in at full length
between two lines of three dashes, so a submitter could both pick the admin's
token bill and close the block early — after which their text read to the model
as operator instructions.

**The call is bounded** (8.4, F-22). A per-request timeout and, where the model
declares it supported, a `max_tokens` ceiling; a `RunnableConfig` naming the run;
and exactly one retry for the transport-level failures that were reaching the
admin as a 500 (`httpx.RemoteProtocolError`, observed live).

**The answer is persisted and reused** (8.4, `design-persistence.md` §4.3).
`uq_mm_llm_assessment_cache` on `(assistant_id, prompt_hash, model, input_hash)
WHERE status='SUCCEEDED'` turns a second reviewer's click into a lookup. Editing
the assistant changes `input_hash`; editing the template changes `prompt_hash`;
both correctly force a fresh call. Failures are written with `status='FAILED'`
and are excluded from that index, so a failure can never poison the cache.

**The LLM is advisory, not load-bearing** (§7 F1). When the provider is
unavailable the eight deterministic gates in `validator/gates.py` are scored into
the same five dimensions and returned with `source=DETERMINISTIC`. The reviewer
gets an answer, the exception text never reaches them (F-75/S15), and the detail
is logged server-side with the correlating assessment row.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from langchain_core.runnables import RunnableConfig

from codemie.configs.logger import current_user_email, logger, logging_user_id
from codemie.core.dependecies import get_project_for_metric
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.routers.utils import check_user_can_access_assistant
from codemie.rest_api.security.user import User
from codemie.service.assistant_generator_service import PromptGeneratorChain
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.marketplace_management.models import (
    AssessmentDimension,
    AssessmentFields,
    AssessmentLLMOutput,
    AssessmentResponse,
    AssessmentSource,
    Bucket,
    EntityKind,
    MarketplaceLLMAssessment,
)
from codemie.service.monitoring.base_monitoring_service import emit_llm_token_metric
from codemie.service.monitoring.metrics_constants import MetricsAttributes
from codemie.templates.agents.marketplace_assessor_prompt import (
    MARKETPLACE_ASSESSOR_PROMPT_NAME,
    MARKETPLACE_ASSESSOR_PROMPT_TEMPLATE,
    MARKETPLACE_ASSESSOR_PROMPT_VERSION,
    UNTRUSTED_BEGIN,
    UNTRUSTED_END,
)

MARKETPLACE_ASSESSOR_TOTAL_METRIC = "codemie_marketplace_assessor_total"

HELP_MESSAGE = "Try again with a different model or contact support."

# The schema handed to `with_structured_output`. Deliberately the LLM-facing
# model and not `AssessmentResponse`: provenance fields must not become fields
# the model is asked to invent.
ASSESSMENT_LLM_SCHEMA = AssessmentLLMOutput

# ---------------------------------------------------------------------------
# 8.3 — caps and neutralisation for the untrusted span
# ---------------------------------------------------------------------------
#
# The system-prompt ceiling is the one that already existed, unused, at
# `validator/config.py:178`; reading it from there rather than re-declaring it
# keeps the assessor and the similarity index agreeing on how much prompt is
# "the prompt".

SYSTEM_PROMPT_LABEL = "system_prompt: |\n"
TRUNCATION_MARKER = "…[truncated]"


def _system_prompt_ceiling() -> int:
    try:
        from codemie.configs.marketplace_management import get_marketplace_management_config
        from codemie.service.marketplace_management.validator.config import Config

        mm_cfg = get_marketplace_management_config("assistants")
        return int(Config.from_marketplace_management(mm_cfg).similarity.system_prompt_ceiling)
    except Exception:  # noqa: BLE001 - a config problem must not disable the cap
        return 12000


SYSTEM_PROMPT_CEILING = _system_prompt_ceiling()

# Per-field caps for everything that is not the system prompt. Generous enough
# that no honest submission is clipped, small enough that a hostile one cannot
# push the operator's own instructions out of the model's attention.
FIELD_CAPS: dict[str, int] = {
    "name": 200,
    "description": 4000,
    "categories": 1000,
    "conversation_starters": 2000,
    "toolkits": 2000,
    "mcp_servers": 2000,
}

# Lines that would otherwise read as a delimiter: markdown rules and code
# fences. Rewritten rather than dropped so the model still sees that the
# submission contained one.
_FENCE_LINE = re.compile(r"(?m)^[ \t]*([-`~=_*])\1{2,}(.*)$")
# C0 control characters other than tab and newline, plus the zero-width and
# bidi-override characters used to smuggle text past a human reviewer.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")


def _neutralise(value: str) -> str:
    """Make submitter text safe to place inside the fenced span.

    Three removals, in order: the operator's sentinels (so they cannot be
    forged), control and bidi characters (so what the reviewer reads is what the
    model read), and delimiter-shaped lines (so the block cannot be closed).
    """
    value = value.replace(UNTRUSTED_BEGIN, "[redacted-marker]").replace(UNTRUSTED_END, "[redacted-marker]")
    value = _CONTROL.sub("", value)
    return _FENCE_LINE.sub(r"[rule]\2", value)


def _cap(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit] + TRUNCATION_MARKER


def _clean(value: Any, limit: int) -> str:
    return _cap(_neutralise("" if value is None else str(value)), limit)


class LLMAssessmentService:
    """Service for LLM-based quality assessment of a single marketplace assistant."""

    @classmethod
    def assess_assistant(
        cls,
        assistant_id: str,
        user: User,
        llm_model: str | None = None,
        request_id: str | None = None,
    ) -> AssessmentResponse:
        """Run a quality assessment for the given assistant.

        Never raises for a provider problem: an unreachable or misbehaving model
        yields the deterministic gate assessment instead (`design-persistence.md`
        §7 F1). The only errors that still surface are the ones the caller can
        act on — an unknown assistant, or a user without access to it.

        Args:
            assistant_id: ID of the assistant to assess.
            user: The user requesting the assessment (used for access control).
            llm_model: Optional LLM model name to use for assessment.
            request_id: Optional request ID for token-metric tracking.

        Returns:
            AssessmentResponse: scores, fixes, overall score, verdict bucket, and
            the provenance of the answer (`source`, `cached`, `assessed_at`).

        Raises:
            ExtendedHTTPException: if the assistant is not found or the user
                lacks access to it.
        """
        from codemie.core.ability import Action

        assistant = cls._load(assistant_id, user, Action.READ)

        model_name = cls._resolve_model_name(llm_model)
        payload = cls._format_assistant_input(assistant)
        input_hash = _sha256(payload)
        prompt_hash = cls.prompt_hash()

        hit = AssessmentCache.lookup(assistant_id, prompt_hash, model_name, input_hash)
        if hit is not None:
            return hit

        started = time.monotonic()
        try:
            raw_output = cls._invoke_with_guards(payload, model_name, request_id)
            output = _clamp_llm_output(raw_output)
        except Exception as exc:  # noqa: BLE001 - classified, logged, then degraded
            latency_ms = int((time.monotonic() - started) * 1000)
            logger.error(
                f"LLM assessment failed for assistant {assistant_id} "
                f"(model={model_name}, {type(exc).__name__}): {exc}",
                exc_info=True,
            )
            AssessmentCache.record_failure(
                assistant_id=assistant_id,
                model=model_name,
                prompt_hash=prompt_hash,
                input_hash=input_hash,
                error=f"{type(exc).__name__}: {exc}",
                latency_ms=latency_ms,
                user=user,
                request_id=request_id,
            )
            # The reviewer gets the deterministic answer, not the provider's
            # exception text (F-75 / S15).
            return cls._deterministic_assessment(assistant)

        latency_ms = int((time.monotonic() - started) * 1000)

        emit_llm_token_metric(
            name=MARKETPLACE_ASSESSOR_TOTAL_METRIC,
            request_id=request_id,
            base_attributes={
                MetricsAttributes.LLM_MODEL: model_name,
                MetricsAttributes.USER_ID: logging_user_id.get("-"),
                MetricsAttributes.USER_NAME: current_user_email.get("-"),
                MetricsAttributes.PROJECT: get_project_for_metric(),
                MetricsAttributes.ASSISTANT_ID: assistant_id,
            },
        )

        assessed_at = datetime.now(timezone.utc)
        AssessmentCache.record_success(
            assistant_id=assistant_id,
            model=model_name,
            prompt_hash=prompt_hash,
            input_hash=input_hash,
            output=output,
            latency_ms=latency_ms,
            user=user,
            request_id=request_id,
        )

        return AssessmentResponse(
            **output.model_dump(),
            cached=False,
            source=AssessmentSource.LLM,
            assessed_at=assessed_at,
            llm_model=model_name,
        )

    # -- loading and access -------------------------------------------------

    @staticmethod
    def _load(assistant_id: str, user: User, action: Any) -> Assistant:
        try:
            assistant = Assistant.get_by_id(assistant_id)
        except ExtendedHTTPException:
            raise
        except KeyError:
            raise ExtendedHTTPException(
                code=404,
                message="Assistant not found",
                details=f"No assistant with id {assistant_id}.",
                help="Refresh the review queue and try again.",
            )
        check_user_can_access_assistant(user, assistant, "view", action)
        return assistant

    # -- 8.3: the untrusted payload ----------------------------------------

    @staticmethod
    def _format_assistant_input(assistant: Assistant) -> str:
        """Build the capped, neutralised block that goes inside the fence.

        Every value here is submitter-authored. `system_prompt` is placed last
        and capped at `SYSTEM_PROMPT_CEILING` — it is both the largest field and
        the one an attacker controls most freely, so trailing it means a
        truncation cannot silently swallow the metadata above it.
        """
        toolkit_names = []
        if assistant.toolkits:
            for tk in assistant.toolkits:
                toolkit_names.append(getattr(tk, "toolkit", None) or getattr(tk, "name", str(tk)))

        mcp_names = []
        if assistant.mcp_servers:
            for mcp in assistant.mcp_servers:
                mcp_names.append(getattr(mcp, "name", str(mcp)))

        has_icon = bool(getattr(assistant, "icon_url", None))

        parts = [
            f"name: {_clean(assistant.name, FIELD_CAPS['name'])}",
            f"description: {_clean(assistant.description, FIELD_CAPS['description'])}",
            f"has_icon: {has_icon}",
            f"categories: {_clean(assistant.categories or [], FIELD_CAPS['categories'])}",
            "conversation_starters: "
            f"{_clean(assistant.conversation_starters or [], FIELD_CAPS['conversation_starters'])}",
            f"toolkits: {_clean(toolkit_names, FIELD_CAPS['toolkits'])}",
            f"mcp_servers: {_clean(mcp_names, FIELD_CAPS['mcp_servers'])}",
            SYSTEM_PROMPT_LABEL + _clean(assistant.system_prompt, SYSTEM_PROMPT_CEILING),
        ]
        return "\n".join(parts)

    @staticmethod
    def _render_prompt(payload: str) -> str:
        """The exact string the model sees. Exists so a test can assert on it."""
        return MARKETPLACE_ASSESSOR_PROMPT_TEMPLATE.format(text=payload)

    @staticmethod
    def prompt_hash() -> str:
        """Half of the §4.3 cache key: the template text, not the rendered prompt."""
        return _sha256(MARKETPLACE_ASSESSOR_PROMPT_TEMPLATE.template)

    # -- 8.4: the bounded call ---------------------------------------------

    @classmethod
    def _invoke_with_guards(cls, payload: str, model_name: str, request_id: str | None) -> AssessmentLLMOutput:
        chain = PromptGeneratorChain.from_prompt_template(
            MARKETPLACE_ASSESSOR_PROMPT_TEMPLATE,
            request_id,
            model_name,
        )
        options = _model_options(model_name)
        if options:
            chain = chain.bind_model_options(**options)
        chain = chain.bind_client_options(max_retries=LLM_SDK_MAX_RETRIES)

        config: RunnableConfig = {
            "run_name": LLM_RUN_NAME,
            "max_concurrency": 1,
            "recursion_limit": 3,
            "tags": ["marketplace", "assessment"],
            "metadata": {
                "prompt_name": MARKETPLACE_ASSESSOR_PROMPT_NAME,
                "prompt_version": MARKETPLACE_ASSESSOR_PROMPT_VERSION,
                "llm_model": model_name,
            },
        }

        for attempt in range(1, LLM_MAX_ATTEMPTS + 1):
            try:
                return chain.invoke_with_model(ASSESSMENT_LLM_SCHEMA, {"text": payload}, config=config)
            except TRANSIENT_LLM_ERRORS as exc:
                if attempt >= LLM_MAX_ATTEMPTS:
                    raise
                logger.warning(
                    f"Transient LLM assessment failure ({type(exc).__name__}), "
                    f"retrying {attempt + 1}/{LLM_MAX_ATTEMPTS}"
                )
        # Unreachable: the loop either returns or raises on the last attempt.
        raise RuntimeError("assessment retry loop exhausted")  # pragma: no cover

    @staticmethod
    def _resolve_model_name(llm_model: str | None) -> str:
        if llm_model:
            return llm_model
        try:
            return llm_service.default_llm_model
        except Exception:  # noqa: BLE001 - the cache key must still be stable
            return "default"

    # -- 8.4: the deterministic floor --------------------------------------

    @classmethod
    def _deterministic_assessment(cls, assistant: Assistant) -> AssessmentResponse:
        """Score the eight deterministic gates into the five LLM dimensions.

        Not a placeholder: `validator/gates.run_all` is the same registry the
        batch auditor and the publish gate run, so a reviewer reading a
        `DETERMINISTIC` assessment is reading the rules the platform actually
        enforces — just without the qualitative judgement.
        """
        from codemie.service.marketplace_management.entity_adapter import assistant_to_record
        from codemie.service.marketplace_management.validator import gates
        from codemie.service.marketplace_management.validator.config import Config
        from codemie.service.marketplace_management.validator.models import Severity

        try:
            cfg = Config.for_assistant_gates()
            findings = gates.run_all(assistant_to_record(assistant), cfg)
        except Exception:  # noqa: BLE001 - the floor cannot itself be a failure mode
            logger.exception("Deterministic assessment fallback failed; returning a neutral score")
            findings = []

        scores: dict[str, int] = {d: 100 for d in _GATE_DIMENSION.values()}
        scores["uniqueness"] = _UNIQUENESS_NEUTRAL_SCORE
        reasons: dict[str, list[str]] = {d: [] for d in AssessmentFields.model_fields}
        fixes: dict[str, list[str]] = {d: [] for d in AssessmentFields.model_fields}

        for finding in findings:
            dimension = _GATE_DIMENSION.get(finding.rule)
            if dimension is None:
                continue
            penalty = 50 if finding.severity is Severity.CRITICAL else 20
            scores[dimension] = max(0, scores[dimension] - penalty)
            reasons[dimension].append(finding.message)
            fixes[dimension].append(finding.message)

        fields_kwargs: dict[str, AssessmentDimension] = {}
        for dimension in AssessmentFields.model_fields:
            score = scores.get(dimension, _UNIQUENESS_NEUTRAL_SCORE)
            if dimension == "uniqueness":
                justification = "Not assessed: duplicate detection is a batch-audit signal, not a gate."
                fix = "Run the marketplace audit to see this assistant's nearest neighbours."
            elif reasons[dimension]:
                justification = "Deterministic checks failed: " + "; ".join(reasons[dimension])
                fix = fixes[dimension][0]
            else:
                justification = "All deterministic checks for this dimension passed."
                fix = "No deterministic issue found; a model review would add qualitative detail."
            fields_kwargs[dimension] = AssessmentDimension(score=score, justification=justification, suggested_fix=fix)

        fields = AssessmentFields(**fields_kwargs)
        dimension_scores = [d.score for d in fields_kwargs.values()]
        overall = round(sum(dimension_scores) / len(dimension_scores))

        return AssessmentResponse(
            fields=fields,
            suggested_fixes=[d.suggested_fix for d in fields_kwargs.values()],
            overall_score=overall,
            verdict_recommendation=bucket_for(overall, dimension_scores),
            cached=False,
            source=AssessmentSource.DETERMINISTIC,
            assessed_at=datetime.now(timezone.utc),
            llm_model=None,
        )


# ---------------------------------------------------------------------------
# Bounds and classification
# ---------------------------------------------------------------------------

LLM_RUN_NAME = "marketplace_assessor"
# One assessment is a single short structured answer; a minute is generous and
# still bounds a worker. `run_in_threadpool` cannot cancel the call, so this is
# the only thing standing between a hung socket and a pinned worker (F-22).
LLM_TIMEOUT_SECONDS = 60
# An unreachable endpoint must fail in seconds, not after the read timeout: the
# connect phase gets its own short bound.
LLM_CONNECT_TIMEOUT_SECONDS = 10
# The deployment-wide client retry (`AZURE_OPENAI_MAX_RETRIES`, 5 by default)
# multiplies the timeout above: 6 attempts x 60s held a reviewer for ~6 minutes
# against a dead endpoint. One SDK retry still absorbs a 429/5xx or a dropped
# connection; the deterministic floor answers after that.
LLM_SDK_MAX_RETRIES = 1
# Two attempts, not three: the failure this retries is a dropped connection, and
# a provider that fails twice is down, not flaky. Beyond that the deterministic
# floor is both faster and free.
LLM_MAX_ATTEMPTS = 2
LLM_MAX_TOKENS = 2000

# Transport-level failures only. A `ValidationError` from structured output is a
# model that cannot follow the schema; retrying it just pays twice for the same
# answer, so it falls straight through to the deterministic floor.
TRANSIENT_LLM_ERRORS: tuple[type[BaseException], ...] = (
    httpx.RemoteProtocolError,
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.NetworkError,
    ConnectionError,
    TimeoutError,
)

# Providers whose client accepts a per-request `timeout` kwarg. Bedrock is
# excluded deliberately: its client is built with `read_timeout` at construction
# (`dependecies.py:185`) and rejects unknown converse parameters.
_TIMEOUT_CAPABLE_PROVIDERS = {"azure_openai", "openai"}

_UNIQUENESS_NEUTRAL_SCORE = 50

# Which of the five dimensions each deterministic gate speaks to.
_GATE_DIMENSION: dict[str, str] = {
    "name_length": "metadata_polish",
    "name_not_banned": "metadata_polish",
    "at_least_one_category": "category_fit",
    "description_length": "description_quality",
    "description_not_equal_name": "description_quality",
    "system_prompt_length": "prompt_substance",
    "substance": "prompt_substance",
    "no_inline_credentials": "prompt_substance",
}


def bucket_for(overall: int, dimension_scores: list[int]) -> Bucket:
    """The verdict rule, in one place, so prompt and fallback cannot disagree."""
    if overall >= 75 and all(s >= 50 for s in dimension_scores):
        return Bucket.VERIFIED
    if overall >= 40:
        return Bucket.COMMUNITY
    return Bucket.DELETE


def _model_options(model_name: str) -> dict[str, Any]:
    """Per-request options this specific model will accept.

    Mirrors `dependecies.filter_params`, which nulls `max_tokens` at
    construction for models whose `features.max_tokens` is False — several
    deployments (gpt-4.1 among them) reject the parameter outright, so forcing a
    ceiling on them would trade a cost bug for an outage. Where the ceiling
    cannot be sent, the deployment's own `max_output_tokens` is the bound.
    """
    options: dict[str, Any] = {}
    details = None
    try:
        details = llm_service.get_model_details(model_name)
    except Exception:  # noqa: BLE001 - an unknown model still gets the defaults
        details = None

    provider = getattr(getattr(details, "provider", None), "value", None)
    if details is None or provider in _TIMEOUT_CAPABLE_PROVIDERS:
        options["timeout"] = httpx.Timeout(LLM_TIMEOUT_SECONDS, connect=LLM_CONNECT_TIMEOUT_SECONDS)

    supports_max_tokens = details is None or getattr(details.features, "max_tokens", None) is not False
    if supports_max_tokens:
        ceiling = getattr(details, "max_output_tokens", None) or LLM_MAX_TOKENS
        options["max_tokens"] = min(LLM_MAX_TOKENS, ceiling)

    return options


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clamp_score(raw: int) -> int:
    """Clamp an LLM-returned score to [0, 100]."""
    return max(0, min(100, raw))


def _clamp_llm_output(output: AssessmentLLMOutput) -> AssessmentLLMOutput:
    """Return a copy of *output* with every numeric score field clamped to [0, 100].

    The LLM can hallucinate scores outside the valid range (e.g. 150).  A score
    above 100 can push ``overall_score`` above 75 and yield ``Bucket.VERIFIED``
    for an assistant that a correctly-scored response would have rejected.
    """
    clamped_fields_kwargs: dict[str, AssessmentDimension] = {}
    for field_name, dim in output.fields:
        clamped_fields_kwargs[field_name] = AssessmentDimension(
            score=_clamp_score(dim.score),
            justification=dim.justification,
            suggested_fix=dim.suggested_fix,
        )
    clamped_fields = AssessmentFields(**clamped_fields_kwargs)
    return AssessmentLLMOutput(
        fields=clamped_fields,
        suggested_fixes=output.suggested_fixes,
        overall_score=_clamp_score(output.overall_score),
        verdict_recommendation=output.verdict_recommendation,
    )


# ---------------------------------------------------------------------------
# §4.3 — the assessment cache
# ---------------------------------------------------------------------------


class AssessmentCache:
    """Read-through cache over `mm_llm_assessment` (design-persistence.md §4.3).

    Deliberately best-effort in both directions. The cache is an optimisation
    and an audit trail, never a correctness requirement: if the database is
    unreachable, an assessment still runs and still returns. That is the
    opposite of the audit-run persistence rule (which must fail loudly), and for
    the opposite reason — nobody's moderation decision is recorded here.
    """

    ENABLED = True

    @staticmethod
    def is_enabled() -> bool:
        return AssessmentCache.ENABLED

    @staticmethod
    def lookup(assistant_id: str, prompt_hash: str, model: str, input_hash: str) -> Optional[AssessmentResponse]:
        if not AssessmentCache.is_enabled():
            return None
        try:
            from sqlalchemy import text as _text

            from codemie.clients.postgres import get_session

            with get_session() as session:
                row = (
                    session.exec(
                        _text(
                            "select fields, suggested_fixes, overall_score, "
                            "       verdict_recommendation, model, created_at "
                            "  from mm_llm_assessment "
                            " where assistant_id = :aid and prompt_hash = :ph "
                            "   and model = :m and input_hash = :ih "
                            "   and status = 'SUCCEEDED' "
                            " order by created_at desc limit 1"
                        ),
                        params={
                            "aid": assistant_id,
                            "ph": prompt_hash,
                            "m": model,
                            "ih": input_hash,
                        },
                    )
                    .mappings()
                    .first()
                )
        except Exception as exc:  # noqa: BLE001 - a cache miss, not a failure
            logger.warning(f"LLM assessment cache lookup unavailable: {type(exc).__name__}: {exc}")
            return None

        if not row or row["fields"] is None:
            return None

        try:
            return AssessmentResponse(
                fields=AssessmentFields(**_as_json(row["fields"])),
                suggested_fixes=_as_json(row["suggested_fixes"]) or [],
                overall_score=row["overall_score"],
                verdict_recommendation=Bucket(row["verdict_recommendation"]),
                cached=True,
                source=AssessmentSource.LLM,
                assessed_at=row["created_at"],
                llm_model=row["model"],
            )
        except Exception as exc:  # noqa: BLE001 - a stale row must not break the call
            logger.warning(f"Discarding unreadable cached assessment: {type(exc).__name__}: {exc}")
            return None

    @staticmethod
    def record_success(
        *,
        assistant_id: str,
        model: str,
        prompt_hash: str,
        input_hash: str,
        output: AssessmentLLMOutput,
        latency_ms: int,
        user: User | None,
        request_id: str | None,
    ) -> None:
        AssessmentCache._insert(
            assistant_id=assistant_id,
            model=model,
            prompt_hash=prompt_hash,
            input_hash=input_hash,
            status="SUCCEEDED",
            overall_score=output.overall_score,
            verdict=output.verdict_recommendation.value,
            fields=output.fields.model_dump(mode="json"),
            suggested_fixes=list(output.suggested_fixes),
            error=None,
            latency_ms=latency_ms,
            user=user,
            request_id=request_id,
        )

    @staticmethod
    def record_failure(
        *,
        assistant_id: str,
        model: str,
        prompt_hash: str,
        input_hash: str,
        error: str,
        latency_ms: int,
        user: User | None,
        request_id: str | None,
    ) -> None:
        AssessmentCache._insert(
            assistant_id=assistant_id,
            model=model,
            prompt_hash=prompt_hash,
            input_hash=input_hash,
            status="FAILED",
            overall_score=None,
            verdict=None,
            fields=None,
            suggested_fixes=None,
            # Server-side only. This string is never returned to a client; the
            # reviewer sees the deterministic assessment instead (F-75 / S15).
            error=error[:4000],
            latency_ms=latency_ms,
            user=user,
            request_id=request_id,
        )

    @staticmethod
    def _insert(**kw: Any) -> None:
        if not AssessmentCache.is_enabled():
            return
        try:
            from sqlmodel import Session

            from codemie.clients.postgres import PostgresClient

            row = MarketplaceLLMAssessment(
                id=str(uuid.uuid4()),
                entity_kind=EntityKind.ASSISTANT.value,
                assistant_id=kw["assistant_id"],
                status=kw["status"],
                model=kw["model"],
                prompt_name=MARKETPLACE_ASSESSOR_PROMPT_NAME,
                prompt_version=MARKETPLACE_ASSESSOR_PROMPT_VERSION,
                prompt_hash=kw["prompt_hash"],
                input_hash=kw["input_hash"],
                overall_score=kw["overall_score"],
                verdict_recommendation=kw["verdict"],
                fields=kw["fields"],
                suggested_fixes=kw["suggested_fixes"],
                latency_ms=kw["latency_ms"],
                error=kw["error"],
                requested_by_id=getattr(kw.get("user"), "id", None),
                request_id=kw.get("request_id"),
            )
            with Session(PostgresClient.get_engine()) as session:
                session.add(row)
                session.commit()
        except Exception as exc:  # noqa: BLE001 - see the class docstring
            # A unique-index collision here is two reviewers assessing the same
            # unchanged assistant concurrently: the first write wins and the
            # second is redundant, so it is logged at debug rather than warned.
            logger.warning(
                f"Could not persist LLM assessment for {kw.get('assistant_id')}: {type(exc).__name__}: {exc}"
            )


def _as_json(value: Any) -> Any:
    """JSONB comes back decoded from psycopg, but a text column would not."""
    if isinstance(value, (str, bytes)):
        return json.loads(value)
    return value
