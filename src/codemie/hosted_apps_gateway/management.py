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

"""Bounded management bodies and safe hosted errors before platform handlers."""

from __future__ import annotations

import asyncio
import re
from uuid import uuid4

from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.hosted_applications.errors import HostedApplicationError

_BOUNDED_PATHS = frozenset({"/v1/hosted-applications"})
_BOUNDED_PREFIXES = ("/v1/hosted-applications/", "/v1/admin/hosted-applications/")
_STREAMED_ARTIFACT = re.compile(r"/v1/hosted-applications/[0-9a-fA-F-]{36}/versions/[0-9a-fA-F-]{36}/artifact")
_COVER_UPLOAD = re.compile(r"/v1/hosted-applications/[0-9a-fA-F-]{36}/cover")

# Pydantic renders these error types from the schema alone; their messages never quote the submitted value.
_SCHEMA_MESSAGES = frozenset(
    {
        "missing",
        "extra_forbidden",
        "string_type",
        "string_too_short",
        "string_too_long",
        "string_pattern_mismatch",
        "string_unicode",
        "int_type",
        "int_parsing",
        "int_from_float",
        "float_type",
        "float_parsing",
        "bool_type",
        "bool_parsing",
        "greater_than",
        "greater_than_equal",
        "less_than",
        "less_than_equal",
        "too_short",
        "too_long",
        "list_type",
        "dict_type",
        "model_type",
        "model_attributes_type",
        "literal_error",
        "enum",
        "uuid_type",
        "uuid_version",
        "none_required",
        "json_type",
    }
)
# Types whose pydantic message interpolates parser detail derived from the input.
_FIXED_MESSAGES = {
    "uuid_parsing": "Input should be a valid UUID",
    "json_invalid": "Request body must be valid JSON",
    "datetime_parsing": "Input should be a valid datetime",
    "date_parsing": "Input should be a valid date",
}
# Validator-authored messages; kept only while they do not quote the rejected value.
_VALIDATOR_MESSAGES = frozenset({"value_error", "assertion_error"})


def failure(code: str, message: str, status: int, **details) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "details": {"code": code, **details}}},
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            **({"Retry-After": "5"} if status in (429, 503) or code == "AUTHENTICATION_FRESHNESS_REQUIRED" else {}),
        },
    )


def route_path(scope) -> str:
    """Path below the mount point; uvicorn --root-path prefixes both path and raw_path with root_path."""
    path = scope.get("path", "")
    root = scope.get("root_path", "")
    if root and path.startswith(root) and (len(path) == len(root) or path[len(root)] == "/"):
        return path[len(root) :] or "/"
    return path


def _route_label(request) -> str:
    """Route template when routing matched (no identifiers), otherwise the path below the mount point."""
    route = request.scope.get("route")
    return getattr(route, "path", None) or route_path(request.scope)


def _field_message(error: dict) -> str:
    kind = error.get("type", "")
    message = str(error.get("msg", ""))
    if kind in _FIXED_MESSAGES:
        return _FIXED_MESSAGES[kind]
    if kind in _SCHEMA_MESSAGES:
        return message[:200]
    if kind in _VALIDATOR_MESSAGES:
        value = error.get("input")
        quoted = isinstance(value, str) and len(value.strip()) >= 3 and value.strip() in message
        if not quoted and message:
            return message[:200]
    return "Invalid value"


def invalid_request(exc: RequestValidationError) -> JSONResponse:
    """422 with the location and a safe message per field; submitted values are never echoed."""
    fields = [
        {
            "location": [str(part)[:64] for part in error.get("loc", ())][:8],
            "message": _field_message(error),
            "type": str(error.get("type", "invalid"))[:64],
        }
        for error in exc.errors()[:20]
    ]
    return failure("INVALID_REQUEST", "Invalid application request", 422, fields=fields)


def hosted_error_response(request, exc: HostedApplicationError) -> JSONResponse:
    """Global HostedApplicationError handler body: bounded public error, server failures logged with context."""
    if exc.status < 500:
        return failure(exc.code, exc.message, exc.status)
    error_id = uuid4().hex
    logger.error(
        f"Hosted request unavailable: error_id={error_id}, code={exc.code}, status={exc.status}, "
        f"method={request.method}, route={_route_label(request)}",
        exc_info=exc,
    )
    return failure(exc.code, exc.message, exc.status, error_id=error_id)


class HostedAPIRoute(APIRoute):
    """Hosted routes keep platform and hosted error handling; only unexpected failures become a safe 503."""

    def get_route_handler(self):
        handler = super().get_route_handler()
        template = self.path

        async def safe(request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # The platform 422 handler echoes submitted values; hosted requests report fields only.
                return invalid_request(exc)
            except (HostedApplicationError, ExtendedHTTPException, HTTPException):
                raise
            except Exception as exc:
                error_id = uuid4().hex
                logger.error(
                    f"Hosted request failed unexpectedly: error_id={error_id}, method={request.method}, "
                    f"route={template}, error={type(exc).__name__}",
                    exc_info=exc,
                )
                return failure(
                    "APPLICATION_UNAVAILABLE",
                    "Application service is unavailable; please retry",
                    503,
                    error_id=error_id,
                )

        return safe


class HostedManagementBodyBoundary:
    """Buffers hosted JSON mutations up to a small bound before any handler reads them.

    Cover uploads (``PUT .../{app_id}/cover``, a raw image) get their own 1 MiB bound and time budget.
    Registered inside CORSMiddleware so 408/413 refusals carry the same CORS headers as every other
    management response and browsers can read them.
    """

    def __init__(
        self, app, *, maximum=32768, idle_seconds=5, total_seconds=10, cover_maximum=1024 * 1024, cover_seconds=30
    ):
        self.app = app
        self.maximum, self.idle_seconds, self.total_seconds = maximum, idle_seconds, total_seconds
        self.cover_maximum, self.cover_seconds = cover_maximum, cover_seconds

    @staticmethod
    def bounded(scope) -> bool:
        if scope["type"] != "http" or scope.get("method") not in ("POST", "PUT", "PATCH", "DELETE"):
            return False
        path = route_path(scope)
        if path not in _BOUNDED_PATHS and not path.startswith(_BOUNDED_PREFIXES):
            return False
        # Artifact bytes stream to storage under their own limits.
        return not (scope.get("method") == "PUT" and _STREAMED_ARTIFACT.fullmatch(path))

    @staticmethod
    def cover_upload(scope) -> bool:
        return scope.get("method") == "PUT" and _COVER_UPLOAD.fullmatch(route_path(scope)) is not None

    @staticmethod
    def too_large(cover: bool) -> tuple[str, str]:
        if cover:
            return "COVER_TOO_LARGE", "Cover images are limited to 1 MiB"
        return "REQUEST_TOO_LARGE", "Application request exceeds its limit"

    async def buffered_body(self, scope, receive, send) -> bytearray | None:
        """The whole request body within its bounds; None when the request was refused or the client left."""
        cover = self.cover_upload(scope)
        maximum = self.cover_maximum if cover else self.maximum
        body = bytearray()
        try:
            async with asyncio.timeout(self.cover_seconds if cover else self.total_seconds):
                while True:
                    event = await asyncio.wait_for(receive(), timeout=self.idle_seconds)
                    if event["type"] == "http.disconnect":
                        return None
                    body.extend(event.get("body", b""))
                    if len(body) > maximum:
                        await failure(*self.too_large(cover), 413)(scope, receive, send)
                        return None
                    if not event.get("more_body", False):
                        break
        except TimeoutError:
            await failure("REQUEST_TIMEOUT", "Application request timed out", 408)(scope, receive, send)
            return None
        return body

    async def __call__(self, scope, receive, send):
        if not self.bounded(scope):
            await self.app(scope, receive, send)
            return
        body = await self.buffered_body(scope, receive, send)
        if body is None:
            return
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)
