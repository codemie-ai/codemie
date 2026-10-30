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
"""The write transport of the EWS client: a private protocol per write call and the evidence it records.

Nothing here is shared with the read client. ``DeadlineAdapter`` clamps every HTTP request to the call's
``Deadline`` and records on ``TransportEvidence`` what the reply proved; ``ews_client`` builds an
``EwsWriteSession`` from them (``build_ews_write_session``) and classifies failures from that evidence.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from exchangelib import Account, Configuration
from exchangelib.protocol import Protocol
from exchangelib.util import to_xml
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError as RequestsConnectionError

from codemie_tools.ms_exchange.errors import DeadlineExhaustedError
from codemie_tools.ms_exchange.write_retry import Deadline

REQUEST_TIMEOUT_SECONDS = 60

_SERVER_BUSY = "ErrorServerBusy"
_BACK_OFF_NAME = "BackOffMilliseconds"
# exchangelib parses the body as SOAP only for these statuses (``RetryPolicy.raise_response_errors``). For every
# other status it raises its own synthesized error before looking at the body, so a body is no proof there.
_SOAP_STATUSES = frozenset({200, 500})


def _milliseconds(text: str | None) -> int | None:
    value = (text or "").strip()
    return int(value) if value.isascii() and value.isdigit() else None


def server_busy_evidence(content: bytes) -> tuple[bool, int | None]:
    """Whether a response body carries a SOAP ``ResponseCode`` ``ErrorServerBusy``, and its server back-off in ms.

    The back-off is ``None`` unless the server sent a non-negative integer ``BackOffMilliseconds`` value. Anything
    that is not parseable XML is no evidence. Only the body counts: exchangelib's exception attributes are not
    evidence, because it fills ``back_off`` with its own 10 s ``RETRY_WAIT`` when the server gave none.
    """
    if not content or _SERVER_BUSY.encode() not in content:
        return False, None
    try:
        root = to_xml(content).getroot()
        busy, back_off = False, None
        for element in root.iter():
            if not isinstance(element.tag, str):
                continue  # comments and processing instructions
            name = element.tag.rsplit("}", 1)[-1]
            if name == "ResponseCode" and (element.text or "").strip() == _SERVER_BUSY:
                busy = True
            elif name == "Value" and element.get("Name") == _BACK_OFF_NAME:
                back_off = _milliseconds(element.text)
    except Exception:  # noqa: BLE001 - unparseable bodies are simply not evidence
        return False, None
    return busy, (back_off if busy else None)


@dataclass
class HttpAttempt:
    """What one HTTP request of a write call showed: the status and what its body proved."""

    status: int | None = None  # None: no response arrived (connection error, timeout, deadline)
    refused: bool = False  # DeadlineAdapter refused to send it: the Deadline was spent, nothing left the process
    server_busy: bool = False
    back_off_ms: int | None = None

    @property
    def proves_retryable_busy(self) -> bool:
        """A SOAP ErrorServerBusy with a server back-off that exchangelib itself read from the body."""
        return self.status in _SOAP_STATUSES and self.server_busy and self.back_off_ms is not None


@dataclass
class TransportEvidence:
    """Per-request records of one write call, filled by ``DeadlineAdapter`` and read by the classifier."""

    attempts: list[HttpAttempt] = field(default_factory=list)

    def begin(self) -> HttpAttempt:
        """Start the record of a new request, so a failure before any reply never reuses an older record."""
        attempt = HttpAttempt()
        self.attempts.append(attempt)
        return attempt

    def reset(self) -> None:
        self.attempts.clear()

    @property
    def deadline_refused(self) -> bool:
        """Whether ``DeadlineAdapter`` refused a request of this call because the Deadline was spent."""
        return any(attempt.refused for attempt in self.attempts)

    @property
    def last(self) -> HttpAttempt | None:
        return self.attempts[-1] if self.attempts else None


def _clamp_timeout(timeout: Any, remaining: float) -> tuple[float, float]:
    """A ``(connect, read)`` timeout no larger than ``min(REQUEST_TIMEOUT_SECONDS, remaining)``."""
    cap = min(float(REQUEST_TIMEOUT_SECONDS), remaining)
    connect, read = timeout if isinstance(timeout, tuple) and len(timeout) == 2 else (timeout, timeout)

    def one(value: Any) -> float:
        return min(float(value), cap) if isinstance(value, (int, float)) else cap

    return one(connect), one(read)


class _DeadlineRefusal(RequestsConnectionError):
    """What ``DeadlineAdapter`` raises for a request it refuses to send.

    A ``ConnectionError``, because exchangelib's ``post_ratelimited`` logs any other exception at ERROR together
    with the whole SOAP request (recipients, subject, body) but handles a connection error quietly. It never leaves
    the client: the refusal is recorded in ``TransportEvidence`` and the callers turn the error exchangelib raises
    for it back into ``DeadlineExhaustedError``.
    """


class DeadlineAdapter(HTTPAdapter):
    """HTTP adapter of a private write protocol: clamps timeouts to the call's Deadline and records evidence.

    The Deadline and the evidence are held by the adapter (one adapter set per private protocol, i.e. per
    write call), not in a process-wide or context variable, so concurrent calls and thread hops cannot see each
    other's deadline. A request that finds no time left is refused before it is sent, so it is Not applied.
    ``WriteProtocol.get_session`` refuses first, once per request. This check covers what that cannot see: a
    deadline that expires between the two, and the NTLM handshake steps that ``requests_ntlm`` sends itself inside
    one POST (each resends the whole body). It raises a ``_DeadlineRefusal`` and marks the attempt, see there.
    """

    def __init__(self, *args: Any, deadline: Deadline, evidence: TransportEvidence, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._deadline = deadline
        self._evidence = evidence

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):  # noqa: ANN001
        attempt = self._evidence.begin()
        remaining = self._deadline.remaining()
        if remaining <= 0:
            attempt.refused = True
            raise _DeadlineRefusal
        response = super().send(
            request,
            stream=stream,
            timeout=_clamp_timeout(timeout, remaining),
            verify=verify,
            cert=cert,
            proxies=proxies,
        )
        attempt.status = response.status_code
        if not stream and response.status_code in _SOAP_STATUSES:
            attempt.server_busy, attempt.back_off_ms = server_busy_evidence(response.content)
        return response


@dataclass
class EwsWriteSession:
    """The state of one write call: an account on a private protocol, the call's Deadline and its evidence."""

    account: Account
    deadline: Deadline
    evidence: TransportEvidence
    # Ids of folders proven to belong to the configured mailbox; None until the first mailbox check of the call.
    known_folders: set[str] | None = None

    def close(self) -> None:
        """Release the private protocol. A failure here must never replace or mask the call's real outcome."""
        with suppress(Exception):  # cleanup failure must not surface over the already-decided outcome
            self.account.protocol.close()


def private_protocol(config: Configuration, deadline: Deadline, evidence: TransportEvidence) -> Protocol:
    """A ``Protocol`` that belongs to one write call: no cache, no shared pool, adapter bound to the call."""

    class WriteProtocol(Protocol):
        TIMEOUT = REQUEST_TIMEOUT_SECONDS
        HTTP_ADAPTER_CLS = partial(DeadlineAdapter, deadline=deadline, evidence=evidence)

        def get_session(self):  # noqa: ANN201
            """Refuse a request once the Deadline is spent, before exchangelib builds or logs it.

            Every EWS request takes its session here, ahead of ``post_ratelimited``, once per request (not per HTTP
            call: an NTLM handshake step is guarded by ``DeadlineAdapter``). That function logs any exception
            raised while posting at ERROR together with the whole SOAP request (recipients, subject, body), so the
            check must come first. It covers the requests exchangelib makes on its own as well (the version probe,
            the folder lookups behind ``account.sent``), not only the ones the client issues.
            """
            if deadline.expired():
                raise DeadlineExhaustedError
            return super().get_session()

    # ``type.__call__`` skips the ``CachingProtocol`` metaclass, which would cache and share this instance.
    return type.__call__(WriteProtocol, config=config)
