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
"""Graph write plumbing, send, forward, move, delete and tag (SDK fully faked, fixture-only credentials).

The fake mailbox records every effect a request would have had, so each test can say both what
the caller saw and what the mailbox ended up holding.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx
import pytest
from azure.core.exceptions import ClientAuthenticationError, ServiceRequestError
from azure.identity import ClientSecretCredential
from msgraph.generated.models.body_type import BodyType

from codemie_tools.ms_exchange import graph_client as gc
from codemie_tools.ms_exchange.errors import (
    CategoryConflictError,
    CredentialExpiredError,
    DeadlineExhaustedError,
    FolderNotFoundError,
    InvalidLocalInputError,
    NotAppliedError,
    PreMutationReadFailedError,
    SendAccessDeniedError,
    UnknownOutcomeError,
    WriteAccessDeniedError,
    WriteConnectionLostUnknownError,
    WriteMessageNotFoundError,
    WriteMessageNotFoundUnknownError,
    WriteRateLimitedError,
    WriteServerErrorUnknownError,
    WriteTimeoutUnknownError,
    WriteUnknownError,
)
from codemie_tools.ms_exchange.models import ExchangeAuthType, ExchangeToolConfig

TO = ["a@example.test", "b@example.test"]


class FakeGraphError(Exception):
    def __init__(self, status, code=None, headers=None, text="secret tenant-id https://login.example"):
        super().__init__(text)
        self.response_status_code = status
        self.response_headers = headers or {}
        self.error = SimpleNamespace(code=code)


class FakeMailbox:
    """What the mailbox would hold after the requests the fake graph accepted."""

    def __init__(self):
        self.sent = []
        self.forwarded = []


class Commit:
    """The request is processed (effect recorded) but the caller sees ``error`` instead of a reply."""

    def __init__(self, error):
        self.error = error


class CommitThenHang:
    """The request is processed, then the reply never arrives (the real wait_for fires)."""


class ScriptedPost:
    """A fake ``.post`` that replays outcomes in order; the default outcome is success."""

    def __init__(self, effects, *outcomes):
        self.effects = effects
        self.outcomes = list(outcomes)
        self.bodies = []

    async def __call__(self, body, request_configuration=None):
        self.bodies.append(body)
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Commit):
            self.effects.append(body)
            raise outcome.error
        if isinstance(outcome, CommitThenHang):
            self.effects.append(body)
            await asyncio.sleep(30)
        if isinstance(outcome, BaseException):
            raise outcome
        self.effects.append(body)


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _config():
    return ExchangeToolConfig(
        auth_type=ExchangeAuthType.OAUTH_AZURE,
        mailbox="shared@example.test",
        oauth_tenant_id="tenant-fixture",
        oauth_client_id="client-fixture",
        oauth_client_secret="secret-fixture",
    )


@pytest.fixture
def env():
    mailbox = FakeMailbox()
    clock = FakeClock()
    credential = MagicMock()
    graph = MagicMock()
    user = graph.users.by_user_id.return_value
    send_post = ScriptedPost(mailbox.sent)
    forward_post = ScriptedPost(mailbox.forwarded)
    user.send_mail.post = send_post
    user.messages.by_message_id.return_value.forward.post = forward_post
    jitter = []

    def rand(low, high):
        jitter.append((low, high))
        return high / 2

    with (
        patch.object(gc, "build_credential", return_value=credential),
        patch.object(gc, "build_graph_client", return_value=graph) as build_client,
        patch.object(gc, "call_with_retry") as read_retry,
    ):
        client = gc.GraphExchangeClient(_config(), clock=clock, sleep=clock.sleep, rand=rand)
        yield SimpleNamespace(
            client=client,
            mailbox=mailbox,
            clock=clock,
            credential=credential,
            graph=graph,
            user=user,
            send_post=send_post,
            forward_post=forward_post,
            build_client=build_client,
            read_retry=read_retry,
            jitter=jitter,
        )
    # The write path never goes through the read retry policy.
    read_retry.assert_not_called()


# ---------------------------------------------------------------- send: request shape


def test_send_posts_sendmail_as_text_and_saves_to_sent_items(env):
    result = env.client.send(TO, "Subj", "Hello", "text")

    assert len(env.mailbox.sent) == 1
    body = env.send_post.bodies[0]
    assert body.save_to_sent_items is True
    assert body.message.subject == "Subj"
    assert body.message.body.content == "Hello"
    assert body.message.body.content_type == BodyType.Text
    assert [r.email_address.address for r in body.message.to_recipients] == TO
    assert result["status"] == "submitted"
    assert result["to"] == TO


def test_send_html_passes_the_body_as_is_with_html_content_type(env):
    env.client.send(TO[:1], "S", "<b>x</b><script>1</script>", "html")

    body = env.send_post.bodies[0]
    assert body.message.body.content_type == BodyType.Html
    assert body.message.body.content == "<b>x</b><script>1</script>"


def test_send_uses_the_configured_mailbox(env):
    env.client.send(TO[:1], "S", "B", "text")

    env.graph.users.by_user_id.assert_called_with("shared@example.test")


# ---------------------------------------------------------------- validation before any request


@pytest.mark.parametrize(
    "args",
    [
        (TO, "S", "B", "markdown"),
        (TO, "S", "B", ""),
        ([], "S", "B", "text"),
        (["not-an-address"], "S", "B", "text"),
        (None, "S", "B", "text"),
        (TO, None, "B", "text"),
    ],
)
def test_send_input_errors_are_not_applied_and_touch_neither_token_nor_graph(env, args):
    with pytest.raises(InvalidLocalInputError) as info:
        env.client.send(*args)

    assert isinstance(info.value, NotAppliedError)
    env.credential.get_token.assert_not_called()
    env.build_client.assert_not_called()
    assert env.mailbox.sent == []


@pytest.mark.parametrize("to", [[], ["bad"], None])
def test_forward_recipient_errors_touch_nothing(env, to):
    with pytest.raises(InvalidLocalInputError):
        env.client.forward("m1", to)

    env.credential.get_token.assert_not_called()
    env.build_client.assert_not_called()


@pytest.mark.parametrize("message_id", ["", "  ", None])
def test_forward_blank_message_id_is_an_input_error(env, message_id):
    with pytest.raises(InvalidLocalInputError):
        env.client.forward(message_id, TO)

    env.build_client.assert_not_called()


# ---------------------------------------------------------------- outcomes


def test_403_before_mutation_is_not_applied_and_the_mailbox_is_unchanged(env):
    env.send_post.outcomes = [FakeGraphError(403, "ErrorAccessDenied")]

    with pytest.raises(SendAccessDeniedError) as info:
        env.client.send(TO, "S", "B", "text")

    assert isinstance(info.value, NotAppliedError)
    assert "Mail.Send" in str(info.value)
    assert env.mailbox.sent == []
    assert len(env.send_post.bodies) == 1
    assert env.clock.sleeps == []


def test_committed_then_timeout_is_one_effect_unknown_and_never_resubmitted(env):
    env.send_post.outcomes = [CommitThenHang()]

    with patch.object(gc, "REQUEST_TIMEOUT_SECONDS", 0.05):
        with pytest.raises(WriteTimeoutUnknownError) as info:
            env.client.send(TO, "S", "B", "text")

    assert isinstance(info.value, UnknownOutcomeError)
    assert len(env.mailbox.sent) == 1
    assert len(env.send_post.bodies) == 1
    assert env.clock.sleeps == []


def test_committed_then_connection_loss_is_unknown_with_one_effect(env):
    env.send_post.outcomes = [Commit(httpx.ReadError("reset"))]

    with pytest.raises(UnknownOutcomeError):
        env.client.send(TO, "S", "B", "text")

    assert len(env.mailbox.sent) == 1
    assert len(env.send_post.bodies) == 1


def test_5xx_is_unknown_and_not_retried(env):
    env.send_post.outcomes = [Commit(FakeGraphError(503, "ServiceUnavailable"))]

    with pytest.raises(WriteServerErrorUnknownError):
        env.client.send(TO, "S", "B", "text")

    assert len(env.send_post.bodies) == 1
    assert env.clock.sleeps == []


def test_unexpected_exception_in_a_submission_is_wrapped_as_unknown(env):
    env.send_post.outcomes = [Commit(RuntimeError("secret internals"))]

    with pytest.raises(WriteUnknownError) as info:
        env.client.send(TO, "S", "B", "text")

    assert "secret" not in str(info.value)
    assert len(env.send_post.bodies) == 1


def test_429_with_retry_after_is_retried_after_the_exact_server_wait(env):
    env.send_post.outcomes = [FakeGraphError(429, headers={"Retry-After": "7"})]

    env.client.send(TO, "S", "B", "text")

    assert env.clock.sleeps == [7.0]
    assert len(env.send_post.bodies) == 2
    assert len(env.mailbox.sent) == 1  # the rejected submission changed nothing


def test_429_is_attempted_at_most_three_times_then_rate_limited_not_applied(env):
    rejection = lambda: FakeGraphError(429, headers={"Retry-After": "2"})  # noqa: E731
    env.send_post.outcomes = [rejection(), rejection(), rejection(), rejection()]

    with pytest.raises(WriteRateLimitedError) as info:
        env.client.send(TO, "S", "B", "text")

    assert isinstance(info.value, NotAppliedError)
    assert len(env.send_post.bodies) == 3
    assert env.clock.sleeps == [2.0, 2.0]
    assert env.mailbox.sent == []


def test_429_without_a_usable_retry_after_uses_full_jitter(env):
    env.send_post.outcomes = [FakeGraphError(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})]

    env.client.send(TO, "S", "B", "text")

    assert env.jitter == [(0, 1.0)]
    assert env.clock.sleeps == [0.5]


def test_retry_after_longer_than_the_remaining_time_stops_without_sleeping(env):
    env.send_post.outcomes = [FakeGraphError(429, headers={"Retry-After": "120"})]

    with pytest.raises(WriteRateLimitedError):
        env.client.send(TO, "S", "B", "text")

    assert env.clock.sleeps == []
    assert len(env.send_post.bodies) == 1


# ---------------------------------------------------------------- Retry-After header shapes


def test_429_with_a_lowercase_retry_after_header_key_is_still_honoured(env):
    env.send_post.outcomes = [FakeGraphError(429, headers={"retry-after": "5"})]

    env.client.send(TO, "S", "B", "text")

    assert env.clock.sleeps == [5.0]
    assert len(env.send_post.bodies) == 2


def test_429_with_a_list_valued_retry_after_header_uses_its_first_value(env):
    env.send_post.outcomes = [FakeGraphError(429, headers={"Retry-After": ["7"]})]

    env.client.send(TO, "S", "B", "text")

    assert env.clock.sleeps == [7.0]
    assert len(env.send_post.bodies) == 2


def test_429_with_a_non_numeric_retry_after_header_uses_full_jitter(env):
    env.send_post.outcomes = [FakeGraphError(429, headers={"Retry-After": "soon"})]

    env.client.send(TO, "S", "B", "text")

    assert env.jitter == [(0, 1.0)]
    assert env.clock.sleeps == [0.5]


def test_429_with_no_retry_after_header_at_all_uses_full_jitter(env):
    env.send_post.outcomes = [FakeGraphError(429)]  # headers defaults to {}

    env.client.send(TO, "S", "B", "text")

    assert env.jitter == [(0, 1.0)]
    assert env.clock.sleeps == [0.5]


# ---------------------------------------------------------------- token step


def test_expired_secret_at_the_token_step_sends_no_graph_request(env):
    env.credential.get_token.side_effect = ClientAuthenticationError(message="AADSTS7000222: client secret expired")

    with pytest.raises(CredentialExpiredError) as info:
        env.client.send(TO, "S", "B", "text")

    assert isinstance(info.value, NotAppliedError)
    env.build_client.assert_not_called()
    assert env.mailbox.sent == []


def test_token_transport_failure_is_not_applied_never_unknown(env):
    env.credential.get_token.side_effect = ServiceRequestError("secret connection details")

    with pytest.raises(PreMutationReadFailedError) as info:
        env.client.send(TO, "S", "B", "text")

    assert "secret" not in str(info.value)
    env.build_client.assert_not_called()


def test_the_token_is_acquired_with_the_cae_flag_the_kiota_provider_uses(env):
    env.client.send(TO, "S", "B", "text")

    env.credential.get_token.assert_called_once_with(*gc.GRAPH_SCOPES, enable_cae=True)


def test_token_time_counts_against_the_deadline_and_shrinks_the_attempt_timeout(env):
    """Behaviour, not a mock of ``run_sync``: the token step (50 s) shrinks what ``_attempt_timeout``
    computes for the deadline it shares with the submission, directly on the production method."""
    env.credential.get_token.side_effect = lambda *a, **k: setattr(env.clock, "now", env.clock.now + 50)

    deadline = env.client._start_write("send")

    assert env.client._attempt_timeout(deadline) == pytest.approx(40.0)


def test_attempt_timeout_is_capped_at_the_per_request_limit(env):
    deadline = env.client._start_write("send")

    assert env.client._attempt_timeout(deadline) == gc.REQUEST_TIMEOUT_SECONDS == 60


def test_no_submission_starts_when_the_token_step_used_the_whole_deadline(env):
    env.credential.get_token.side_effect = lambda *a, **k: setattr(env.clock, "now", env.clock.now + 95)

    with pytest.raises(DeadlineExhaustedError) as info:
        env.client.send(TO, "S", "B", "text")

    assert isinstance(info.value, NotAppliedError)
    env.build_client.assert_not_called()
    assert env.mailbox.sent == []


# ---------------------------------------------------------------- the token is acquired once


class _RecordingTransport:
    """A synchronous azure-core transport that never touches the network."""

    def __init__(self):
        self.token_requests = 0

    def send(self, request, **kwargs):
        if request.method == "POST" and "/token" in request.url:
            self.token_requests += 1
            payload = {"access_token": "token-fixture", "token_type": "Bearer", "expires_in": 3600}
        else:
            authority = "https://login.microsoftonline.com/tenant-fixture"
            payload = {
                "authorization_endpoint": f"{authority}/oauth2/v2.0/authorize",
                "token_endpoint": f"{authority}/oauth2/v2.0/token",
                "device_authorization_endpoint": f"{authority}/oauth2/v2.0/devicecode",
                "issuer": f"{authority}/v2.0",
            }
        body = json.dumps(payload)
        response = Mock(status_code=200, headers={"content-type": "application/json"})
        response.content_type = "application/json"
        response.text = lambda encoding=None: body
        response.body = lambda: body.encode("utf-8")
        return response

    def open(self): ...

    def close(self): ...

    def sleep(self, duration): ...

    def __enter__(self):
        return self

    def __exit__(self, *args): ...


def test_pre_warm_and_write_share_one_token_request_through_the_real_kiota_stack():
    transport = _RecordingTransport()
    credential = ClientSecretCredential(
        tenant_id="tenant-fixture",
        client_id="client-fixture",
        client_secret="secret-fixture",
        transport=transport,
        disable_instance_discovery=True,
        connection_timeout=10,
        read_timeout=10,
        retry_total=1,
    )
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(202)

    real_create = gc.GraphClientFactory.create_with_default_middleware

    def create(**kwargs):
        return real_create(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), **kwargs)

    with (
        patch.object(gc, "build_credential", return_value=credential),
        patch.object(gc.GraphClientFactory, "create_with_default_middleware", side_effect=create),
    ):
        client = gc.GraphExchangeClient(_config())
        client.send(TO[:1], "S", "B", "text")

    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert seen[0].url.path.endswith("/users/shared@example.test/sendMail")
    assert seen[0].headers["Authorization"] == "Bearer token-fixture"
    assert transport.token_requests == 1


# ---------------------------------------------------------------- forward


def test_forward_posts_to_the_message_forward_endpoint_with_recipients_only(env):
    result = env.client.forward("m1", TO)

    env.user.messages.by_message_id.assert_called_with("m1")
    body = env.forward_post.bodies[0]
    assert [r.email_address.address for r in body.to_recipients] == TO
    assert body.comment is None
    assert body.message is None
    assert len(env.mailbox.forwarded) == 1
    assert result == {"status": "submitted", "message_id": "m1", "to": TO}


def test_forward_of_a_missing_message_is_not_applied(env):
    env.forward_post.outcomes = [FakeGraphError(404, "ErrorItemNotFound")]

    with pytest.raises(WriteMessageNotFoundError) as info:
        env.client.forward("gone", TO)

    assert isinstance(info.value, NotAppliedError)
    assert env.mailbox.forwarded == []
    assert len(env.forward_post.bodies) == 1


def test_forward_429_is_retried_and_forward_403_names_mail_send(env):
    env.forward_post.outcomes = [FakeGraphError(429, headers={"Retry-After": "1"})]
    env.client.forward("m1", TO)
    assert len(env.mailbox.forwarded) == 1

    env.forward_post.outcomes = [FakeGraphError(403, "ErrorAccessDenied")]
    with pytest.raises(SendAccessDeniedError):
        env.client.forward("m1", TO)
    assert len(env.mailbox.forwarded) == 1


# ---------------------------------------------------------------- reusable helpers for T6+


def test_start_write_returns_a_deadline_after_a_token_pre_warm(env):
    deadline = env.client._start_write("move")

    assert deadline.remaining() == pytest.approx(90.0)
    env.credential.get_token.assert_called_once_with(*gc.GRAPH_SCOPES, enable_cae=True)


def test_inner_read_is_a_single_attempt_bounded_by_min_of_limit_and_remaining(env):
    """The timeout bound is asserted straight from the production method (``_attempt_timeout``), not
    by spying on how ``_inner_read`` happens to pass it to ``run_sync``."""
    deadline = env.client._start_write("move")
    env.clock.now += 70
    read = AsyncMock(return_value="folder")

    assert env.client._attempt_timeout(deadline) == pytest.approx(20.0)
    result = env.client._inner_read("move", deadline, read)

    assert result == "folder"
    read.assert_awaited_once()


def test_inner_read_429_before_mutation_is_not_applied_rate_limited_and_not_retried(env):
    deadline = env.client._start_write("move")
    read = AsyncMock(side_effect=FakeGraphError(429, headers={"Retry-After": "1"}))

    with pytest.raises(WriteRateLimitedError):
        env.client._inner_read("move", deadline, read)

    read.assert_awaited_once()
    assert env.clock.sleeps == []


def test_inner_read_failure_before_mutation_is_not_applied(env):
    deadline = env.client._start_write("move")
    read = AsyncMock(side_effect=FakeGraphError(503))

    with pytest.raises(PreMutationReadFailedError):
        env.client._inner_read("move", deadline, read)


def test_failed_reconciliation_read_stays_unknown(env):
    deadline = env.client._start_write("move")
    read = AsyncMock(side_effect=FakeGraphError(503))

    with pytest.raises(WriteServerErrorUnknownError):
        env.client._inner_read("move", deadline, read, before_mutation=False)


def test_inner_read_on_a_spent_deadline_sends_nothing(env):
    deadline = env.client._start_write("move")
    env.clock.now += 91
    read = AsyncMock()

    with pytest.raises(DeadlineExhaustedError):
        env.client._inner_read("move", deadline, read)
    with pytest.raises(UnknownOutcomeError):
        env.client._inner_read("move", deadline, read, before_mutation=False)

    read.assert_not_called()


def test_submit_write_runs_a_general_attempt_under_the_shared_owner(env):
    deadline = env.client._start_write("move")
    attempt = AsyncMock(side_effect=[FakeGraphError(429, headers={"Retry-After": "3"}), "moved-id"])

    result = env.client._submit_write("move", deadline, attempt)

    assert result == "moved-id"
    assert attempt.await_count == 2
    assert env.clock.sleeps == [3.0]


# ---------------------------------------------------------------- run_sync


def test_run_sync_enforces_an_explicit_timeout():
    async def slow():
        await asyncio.sleep(5)

    with pytest.raises(TimeoutError):
        gc.run_sync(slow, timeout=0.05)


def test_run_sync_enforces_an_explicit_timeout_inside_a_running_loop():
    async def slow():
        await asyncio.sleep(5)

    async def caller():
        return gc.run_sync(slow, timeout=0.05)

    with pytest.raises(TimeoutError):
        asyncio.run(caller())


def test_run_sync_default_timeout_is_unchanged_for_reads():
    async def quick():
        return 7

    assert gc.run_sync(quick) == 7


# ---------------------------------------------------------------- move, delete and tag fakes


class FakeStore:
    """A mailbox behind the Graph request builders: messages, folders, scripted failures, recorded effects.

    ``If-Match`` is enforced here (a stale etag answers 412) only so the client's 412 handling can be
    exercised; whether the real Graph enforces it on messages is unverified.
    """

    def __init__(self, user):
        self.messages = {}
        self.folders = [
            SimpleNamespace(
                id="id-finance", display_name="Finance", child_folder_count=0, total_item_count=0, unread_item_count=0
            )
        ]
        self.calls = []
        self.scripts = {}
        self.moves = []
        self.permanent_deletes = []
        self.patches = []
        self.keep_id_on_move = False
        self.send_etag = True
        self.before_patch = None
        user.messages.by_message_id = lambda mid: _FakeItem(self, mid)
        user.mail_folders.by_mail_folder_id = lambda name: SimpleNamespace(get=_FolderGet(self, name))
        user.mail_folders.get = self._folders_get

    def add(self, mid, parent="id-inbox", categories=()):
        self.messages[mid] = {"parent": parent, "categories": list(categories), "version": 1}

    def script(self, kind, *outcomes):
        self.scripts[kind] = list(outcomes)

    def outcome(self, kind):
        queue = self.scripts.get(kind) or []
        return queue.pop(0) if queue else None

    def kinds(self):
        return [c[0] for c in self.calls]

    async def _folders_get(self, request_configuration=None):
        self.calls.append(("folders",))
        return SimpleNamespace(value=list(self.folders), odata_next_link=None)


class _FolderGet:
    def __init__(self, store, name):
        self.store, self.name = store, name

    async def __call__(self, request_configuration=None):
        self.store.calls.append(("folder", self.name))
        outcome = self.store.outcome("folder")
        if isinstance(outcome, BaseException):
            raise outcome
        return SimpleNamespace(id=f"id-{self.name}")


class _FakeItem:
    def __init__(self, store, mid):
        self.store, self.mid = store, mid
        self.move = SimpleNamespace(post=self._move)
        self.permanent_delete = SimpleNamespace(post=self._permanent_delete)

    def _row(self):
        row = self.store.messages.get(self.mid)
        if row is None:
            raise FakeGraphError(404, "ErrorItemNotFound")
        return row

    async def _apply(self, kind, effect):
        """Run scripted outcomes around ``effect`` (recorded when the request is processed)."""
        outcome = self.store.outcome(kind)
        if isinstance(outcome, BaseException):
            raise outcome
        result = effect()
        if isinstance(outcome, Commit):
            raise outcome.error
        if isinstance(outcome, CommitThenHang):
            await asyncio.sleep(30)
        return result

    async def get(self, request_configuration=None):
        self.store.calls.append(("get", self.mid))
        outcome = self.store.outcome("get")
        if isinstance(outcome, BaseException):
            raise outcome
        row = self._row()
        extra = {"@odata.etag": f'W/"{row["version"]}"'} if self.store.send_etag else {}
        return SimpleNamespace(
            id=self.mid, parent_folder_id=row["parent"], categories=list(row["categories"]), additional_data=extra
        )

    async def _move(self, body, request_configuration=None):
        self.store.calls.append(("move", self.mid))

        def effect():
            row = self._row()
            self.store.moves.append((self.mid, body.destination_id))
            new_id = self.mid if self.store.keep_id_on_move else f"{self.mid}-moved"
            del self.store.messages[self.mid]
            self.store.messages[new_id] = dict(row, parent=body.destination_id)
            return SimpleNamespace(id=new_id)

        return await self._apply("move", effect)

    async def _permanent_delete(self, request_configuration=None):
        self.store.calls.append(("permanent_delete", self.mid))

        def effect():
            self._row()
            self.store.permanent_deletes.append(self.mid)
            del self.store.messages[self.mid]

        return await self._apply("permanent_delete", effect)

    async def patch(self, body, request_configuration=None):
        if_match = (request_configuration.headers.try_get("If-Match") if request_configuration else None) or set()
        self.store.calls.append(("patch", self.mid))
        if self.store.before_patch:
            self.store.before_patch(self.store, self.mid)
        self.store.patches.append({"id": self.mid, "if_match": set(if_match), "categories": list(body.categories)})

        def effect():
            row = self._row()
            if if_match and if_match != {f'W/"{row["version"]}"'}:
                raise FakeGraphError(412, "ErrorIrresolvableConflict")
            row["categories"] = list(body.categories)
            row["version"] += 1
            return SimpleNamespace(id=self.mid)

        return await self._apply("patch", effect)


@pytest.fixture
def store(env):
    return FakeStore(env.user)


def _concurrent_change(category="Other"):
    """A hook that has someone else add ``category`` to the message just before each PATCH lands."""

    def hook(store, mid):
        row = store.messages[mid]
        row["categories"].append(category)
        row["version"] += 1

    return hook


# ---------------------------------------------------------------- move


def test_move_to_a_well_known_folder_returns_the_new_message_id(env, store):
    store.add("m1")

    result = env.client.move("m1", "archive")

    assert result == {"status": "moved", "message_id": "m1-moved", "target_folder": "archive"}
    assert store.moves == [("m1", "id-archive")]
    assert store.messages["m1-moved"]["parent"] == "id-archive"


def test_move_resolves_a_folder_name_through_the_folder_list(env, store):
    store.add("m1")

    result = env.client.move("m1", "finance")

    assert result["message_id"] == "m1-moved"
    assert store.moves == [("m1", "id-finance")]


def test_move_to_the_folder_the_message_is_already_in_writes_nothing(env, store):
    store.add("m1", parent="id-archive")

    result = env.client.move("m1", "Archive")

    assert result == {"status": "moved", "already_done": True, "message_id": "m1", "target_folder": "Archive"}
    assert "move" not in store.kinds()
    assert store.moves == []


def test_move_to_an_unknown_folder_is_not_applied_and_writes_nothing(env, store):
    store.add("m1")

    with pytest.raises(FolderNotFoundError) as info:
        env.client.move("m1", "No Such Folder")

    assert isinstance(info.value, NotAppliedError)
    assert store.moves == []
    assert "move" not in store.kinds()


def test_move_to_a_well_known_folder_the_server_does_not_know_is_a_folder_error(env, store):
    store.add("m1")
    store.script("folder", FakeGraphError(404, "ErrorItemNotFound"))

    with pytest.raises(FolderNotFoundError) as info:
        env.client.move("m1", "archive")

    assert isinstance(info.value, NotAppliedError)
    assert store.moves == []


def test_move_pre_read_failure_is_not_applied_and_writes_nothing(env, store):
    store.add("m1")
    store.script("get", FakeGraphError(503, "ServiceUnavailable"))

    with pytest.raises(PreMutationReadFailedError) as info:
        env.client.move("m1", "archive")

    assert isinstance(info.value, NotAppliedError)
    assert store.moves == []
    assert env.clock.sleeps == []  # the pre-read is a single attempt


def test_move_of_a_missing_message_is_unknown_at_the_pre_read(env, store):
    # An earlier move that ended Unknown may have changed the id already.
    with pytest.raises(WriteMessageNotFoundUnknownError):
        env.client.move("gone", "archive")

    assert "move" not in store.kinds()


def test_move_403_names_mail_readwrite_and_leaves_the_message_in_place(env, store):
    store.add("m1")
    store.script("move", FakeGraphError(403, "ErrorAccessDenied"))

    with pytest.raises(WriteAccessDeniedError) as info:
        env.client.move("m1", "archive")

    assert isinstance(info.value, NotAppliedError)
    assert "Mail.ReadWrite" in str(info.value)
    assert store.messages["m1"]["parent"] == "id-inbox"


def test_move_429_is_retried_after_the_server_wait_and_moves_once(env, store):
    store.add("m1")
    store.script("move", FakeGraphError(429, headers={"Retry-After": "4"}))

    result = env.client.move("m1", "archive")

    assert result["message_id"] == "m1-moved"
    assert env.clock.sleeps == [4.0]
    assert store.kinds().count("move") == 2
    assert len(store.moves) == 1


def test_move_lost_reply_is_unknown_when_the_old_id_is_gone_and_never_resubmitted(env, store):
    store.add("m1")
    store.script("move", Commit(httpx.ReadError("reset")))

    with pytest.raises(WriteConnectionLostUnknownError) as info:
        env.client.move("m1", "archive")

    assert isinstance(info.value, UnknownOutcomeError)
    assert store.kinds().count("move") == 1
    assert len(store.moves) == 1  # the change did land
    assert store.calls[-1] == ("get", "m1")  # reconciliation re-read the same id


def test_move_lost_reply_is_success_when_the_same_id_is_found_in_the_target(env, store):
    store.add("m1")
    store.keep_id_on_move = True
    store.script("move", Commit(httpx.ReadError("reset")))

    result = env.client.move("m1", "archive")

    assert result == {"status": "moved", "message_id": "m1", "target_folder": "archive"}
    assert store.kinds().count("move") == 1


def test_move_5xx_with_the_message_still_in_the_source_stays_unknown(env, store):
    store.add("m1")
    store.script("move", FakeGraphError(503, "ServiceUnavailable"))

    with pytest.raises(WriteServerErrorUnknownError):
        env.client.move("m1", "archive")

    assert store.kinds().count("move") == 1
    assert store.messages["m1"]["parent"] == "id-inbox"


def test_move_failed_reconciliation_read_keeps_the_original_unknown(env, store):
    store.add("m1")
    store.script("move", Commit(httpx.ReadError("reset")))
    store.script("get", None, FakeGraphError(503, "ServiceUnavailable"))

    with pytest.raises(WriteConnectionLostUnknownError):
        env.client.move("m1", "archive")


def test_move_submission_gets_only_the_deadline_left_after_a_slow_pre_read(env, store):
    """The pre-read (folder lookup + parent lookup) and the submission share one 90 s Deadline:
    time the pre-read spends shrinks what ``_attempt_timeout`` hands the submission, exactly as
    ``_inner_read`` is bounded for a plain read (see ``test_inner_read_is_a_single_attempt_...``)."""
    store.add("m1")

    async def slow_folder_get(request_configuration=None):
        env.clock.now += 70  # the pre-read consumes most of the 90 s deadline
        return SimpleNamespace(id="id-archive")

    env.user.mail_folders.by_mail_folder_id = lambda name: SimpleNamespace(get=slow_folder_get)

    with patch.object(gc, "run_sync", wraps=gc.run_sync) as spy:
        result = env.client.move("m1", "archive")

    assert result["message_id"] == "m1-moved"
    assert len(spy.call_args_list) == 2  # one pre-read, one submission
    pre_read_timeout = spy.call_args_list[0].kwargs["timeout"]
    submission_timeout = spy.call_args_list[-1].kwargs["timeout"]
    assert pre_read_timeout == pytest.approx(60.0)  # full per-request budget: nothing spent yet
    assert submission_timeout == pytest.approx(20.0)  # 90 s deadline - 70 s already spent


def test_move_timeout_after_commit_is_unknown_with_one_effect(env, store):
    store.add("m1")
    store.script("move", CommitThenHang())

    with patch.object(gc, "REQUEST_TIMEOUT_SECONDS", 0.05):
        with pytest.raises(WriteTimeoutUnknownError):
            env.client.move("m1", "archive")

    assert len(store.moves) == 1
    assert store.kinds().count("move") == 1


@pytest.mark.parametrize("args", [("", "archive"), ("  ", "archive"), (None, "archive"), ("m1", ""), ("m1", None)])
def test_move_input_errors_touch_neither_token_nor_graph(env, store, args):
    with pytest.raises(InvalidLocalInputError):
        env.client.move(*args)

    env.credential.get_token.assert_not_called()
    env.build_client.assert_not_called()


# ---------------------------------------------------------------- delete


def test_soft_delete_is_the_default_and_moves_to_deleted_items(env, store):
    store.add("m1")

    result = env.client.delete("m1")

    assert result == {"status": "deleted", "soft": True, "message_id": "m1-moved"}
    assert store.moves == [("m1", "id-deleteditems")]
    assert store.permanent_deletes == []


def test_default_delete_of_an_item_already_in_deleted_items_issues_no_write(env, store):
    store.add("m1", parent="id-deleteditems")

    result = env.client.delete("m1")

    assert result == {"status": "deleted", "soft": True, "already_done": True, "message_id": "m1"}
    assert set(store.kinds()) <= {"folder", "get"}
    assert store.moves == [] and store.permanent_deletes == []
    assert "m1" in store.messages


def test_soft_delete_unknown_outcome_never_escalates_to_a_hard_delete(env, store):
    store.add("m1")
    store.script("move", FakeGraphError(503, "ServiceUnavailable"))

    with pytest.raises(UnknownOutcomeError):
        env.client.delete("m1")

    assert "permanent_delete" not in store.kinds()
    assert store.permanent_deletes == []


def test_soft_delete_lost_reply_is_success_when_found_in_deleted_items(env, store):
    store.add("m1")
    store.keep_id_on_move = True
    store.script("move", Commit(httpx.ReadError("reset")))

    result = env.client.delete("m1", soft=True)

    assert result == {"status": "deleted", "soft": True, "message_id": "m1"}


def test_soft_delete_pre_read_failure_is_not_applied(env, store):
    store.add("m1")
    store.script("get", FakeGraphError(503, "ServiceUnavailable"))

    with pytest.raises(PreMutationReadFailedError):
        env.client.delete("m1")

    assert store.moves == []


def test_hard_delete_posts_permanent_delete_after_a_minimal_pre_read(env, store):
    store.add("m1")

    result = env.client.delete("m1", soft=False)

    assert result == {"status": "deleted", "soft": False, "message_id": "m1"}
    assert store.permanent_deletes == ["m1"]
    assert store.kinds() == ["get", "permanent_delete"]


def test_hard_delete_lost_reply_stays_unknown_and_is_not_resubmitted_or_reconciled(env, store):
    store.add("m1")
    store.script("permanent_delete", Commit(httpx.ReadError("reset")))

    with pytest.raises(WriteConnectionLostUnknownError):
        env.client.delete("m1", soft=False)

    assert store.kinds() == ["get", "permanent_delete"]  # the pre-read only: no reconciliation
    assert store.permanent_deletes == ["m1"]


def test_hard_delete_of_an_id_that_is_gone_is_unknown_and_sends_no_delete(env, store):
    # An earlier delete that ended Unknown may have removed it already.
    with pytest.raises(WriteMessageNotFoundUnknownError) as info:
        env.client.delete("m1", soft=False)

    assert info.value.OUTCOME == "unknown"
    assert store.permanent_deletes == []


def test_hard_delete_of_a_malformed_id_is_not_applied_and_sends_no_delete(env, store):
    store.script("get", FakeGraphError(400, "ErrorInvalidIdMalformed"))

    with pytest.raises(WriteMessageNotFoundError):
        env.client.delete("not-an-id", soft=False)

    assert store.permanent_deletes == []


def test_hard_delete_not_found_on_the_delete_itself_stays_unknown(env, store):
    store.add("m1")
    store.script("permanent_delete", Commit(FakeGraphError(404, "ErrorItemNotFound")))

    with pytest.raises(WriteMessageNotFoundUnknownError):
        env.client.delete("m1", soft=False)


def test_hard_delete_403_is_not_applied_and_the_message_stays(env, store):
    store.add("m1")
    store.script("permanent_delete", FakeGraphError(403, "ErrorAccessDenied"))

    with pytest.raises(WriteAccessDeniedError):
        env.client.delete("m1", soft=False)

    assert "m1" in store.messages


def test_hard_delete_429_is_retried_and_deletes_once(env, store):
    store.add("m1")
    store.script("permanent_delete", FakeGraphError(429, headers={"Retry-After": "2"}))

    env.client.delete("m1", soft=False)

    assert env.clock.sleeps == [2.0]
    assert store.permanent_deletes == ["m1"]


@pytest.mark.parametrize(
    "args, kwargs", [(("",), {}), ((None,), {}), (("m1",), {"soft": "false"}), (("m1",), {"soft": None})]
)
def test_delete_input_errors_touch_neither_token_nor_graph(env, store, args, kwargs):
    with pytest.raises(InvalidLocalInputError):
        env.client.delete(*args, **kwargs)

    env.credential.get_token.assert_not_called()
    env.build_client.assert_not_called()


# ---------------------------------------------------------------- tag


def test_tag_adds_the_category_and_keeps_the_existing_ones(env, store):
    store.add("m1", categories=["Finance"])

    result = env.client.tag("m1", "Processed")

    assert result == {"status": "tagged", "message_id": "m1", "categories": ["Finance", "Processed"]}
    assert store.messages["m1"]["categories"] == ["Finance", "Processed"]
    assert len(store.patches) == 1


def test_tag_sends_the_message_etag_as_if_match(env, store):
    store.add("m1", categories=["Finance"])

    env.client.tag("m1", "Processed")

    assert store.patches[0]["if_match"] == {'W/"1"'}


def test_tag_without_an_etag_still_writes_the_union_without_if_match(env, store):
    store.add("m1")
    store.send_etag = False

    env.client.tag("m1", "Processed")

    assert store.patches[0]["if_match"] == set()
    assert store.messages["m1"]["categories"] == ["Processed"]


def test_tagging_twice_issues_no_second_patch(env, store):
    store.add("m1", categories=["Finance"])
    env.client.tag("m1", "Processed")

    result = env.client.tag("m1", "Processed")

    assert result == {
        "status": "tagged",
        "already_done": True,
        "message_id": "m1",
        "categories": ["Finance", "Processed"],
    }
    assert len(store.patches) == 1


def test_tag_presence_check_ignores_case(env, store):
    store.add("m1", categories=["Finance"])

    result = env.client.tag("m1", "finance")

    assert result["already_done"] is True
    assert store.patches == []


def test_tag_writes_the_category_as_received(env, store):
    store.add("m1")

    env.client.tag("m1", "Processed")

    assert store.messages["m1"]["categories"] == ["Processed"]


def test_concurrent_change_triggers_one_recompute_that_keeps_the_other_category(env, store):
    store.add("m1", categories=["Finance"])
    changes = iter([_concurrent_change("Other")])
    store.before_patch = lambda s, mid: next(changes, lambda *_: None)(s, mid)

    result = env.client.tag("m1", "Processed")

    assert result["categories"] == ["Finance", "Other", "Processed"]
    assert store.messages["m1"]["categories"] == ["Finance", "Other", "Processed"]
    assert len(store.patches) == 2
    assert store.kinds().count("get") == 2  # one reread


def test_concurrent_change_that_already_added_the_category_needs_no_second_patch(env, store):
    store.add("m1")
    changes = iter([_concurrent_change("Processed")])
    store.before_patch = lambda s, mid: next(changes, lambda *_: None)(s, mid)

    result = env.client.tag("m1", "Processed")

    assert result["already_done"] is True
    assert result["categories"] == ["Processed"]
    assert len(store.patches) == 1


def test_a_second_conflict_is_a_category_conflict_not_applied_after_one_recompute(env, store):
    store.add("m1", categories=["Finance"])
    store.before_patch = _concurrent_change("Other")

    with pytest.raises(CategoryConflictError) as info:
        env.client.tag("m1", "Processed")

    assert isinstance(info.value, NotAppliedError)
    assert len(store.patches) == 2
    assert store.kinds().count("get") == 2
    assert "Processed" not in store.messages["m1"]["categories"]


def test_tag_never_exceeds_three_submissions_across_a_rate_limit_and_conflicts(env, store):
    store.add("m1")
    store.script("patch", FakeGraphError(429, headers={"Retry-After": "1"}))
    store.before_patch = _concurrent_change("Other")

    with pytest.raises(CategoryConflictError):
        env.client.tag("m1", "Processed")

    assert len(store.patches) == 3
    assert env.clock.sleeps == [1.0, 0.0]  # the server wait, then the zero-length conflict retry


def test_tag_conflict_on_the_last_submission_is_a_category_conflict_not_rate_limiting(env, store):
    store.add("m1")
    throttled = FakeGraphError(429, headers={"Retry-After": "1"})
    store.script("get", throttled, throttled)
    store.before_patch = _concurrent_change("Other")

    with pytest.raises(CategoryConflictError) as info:
        env.client.tag("m1", "Processed")

    assert isinstance(info.value, NotAppliedError)
    assert len(store.patches) == 1  # two throttled reads used two submissions; the first conflict had none left
    assert env.clock.sleeps == [1.0, 1.0]


def test_tag_conflict_retry_waits_for_nothing_and_uses_no_jitter(env, store):
    store.add("m1")
    changes = iter([_concurrent_change("Other")])
    store.before_patch = lambda s, mid: next(changes, lambda *_: None)(s, mid)

    env.client.tag("m1", "Processed")

    assert sum(env.clock.sleeps) == 0
    assert env.jitter == []


def test_tag_read_failure_is_not_applied_and_writes_nothing(env, store):
    store.add("m1")
    store.script("get", FakeGraphError(503, "ServiceUnavailable"))

    with pytest.raises(PreMutationReadFailedError) as info:
        env.client.tag("m1", "Processed")

    assert isinstance(info.value, NotAppliedError)
    assert store.patches == []
    assert store.kinds().count("get") == 1


def test_tag_read_timeout_is_not_applied(env, store):
    store.add("m1")
    store.script("get", TimeoutError())

    with pytest.raises(PreMutationReadFailedError):
        env.client.tag("m1", "Processed")

    assert store.patches == []


def test_tag_of_a_missing_message_is_not_applied(env, store):
    with pytest.raises(WriteMessageNotFoundError) as info:
        env.client.tag("gone", "Processed")

    assert isinstance(info.value, NotAppliedError)
    assert store.patches == []


def test_tag_read_429_is_retried_as_a_rate_limit(env, store):
    store.add("m1")
    store.script("get", FakeGraphError(429, headers={"Retry-After": "2"}))

    result = env.client.tag("m1", "Processed")

    assert result["categories"] == ["Processed"]
    assert env.clock.sleeps == [2.0]
    assert len(store.patches) == 1


def test_tag_patch_403_names_mail_readwrite_and_changes_nothing(env, store):
    store.add("m1")
    store.script("patch", FakeGraphError(403, "ErrorAccessDenied"))

    with pytest.raises(WriteAccessDeniedError) as info:
        env.client.tag("m1", "Processed")

    assert "Mail.ReadWrite" in str(info.value)
    assert store.messages["m1"]["categories"] == []


def test_tag_patch_5xx_is_unknown_and_not_resubmitted(env, store):
    store.add("m1")
    store.script("patch", Commit(FakeGraphError(503, "ServiceUnavailable")))

    with pytest.raises(WriteServerErrorUnknownError):
        env.client.tag("m1", "Processed")

    assert len(store.patches) == 1
    assert store.messages["m1"]["categories"] == ["Processed"]  # the change did land


def test_tag_patch_timeout_after_commit_is_unknown(env, store):
    store.add("m1")
    store.script("patch", CommitThenHang())

    with patch.object(gc, "REQUEST_TIMEOUT_SECONDS", 0.05):
        with pytest.raises(WriteTimeoutUnknownError):
            env.client.tag("m1", "Processed")

    assert len(store.patches) == 1


@pytest.mark.parametrize(
    "args", [("", "C"), (None, "C"), ("m1", ""), ("m1", "  "), ("m1", None), ("m1", "a\nb"), ("m1", "x" * 300)]
)
def test_tag_input_errors_touch_neither_token_nor_graph(env, store, args):
    with pytest.raises(InvalidLocalInputError):
        env.client.tag(*args)

    env.credential.get_token.assert_not_called()
    env.build_client.assert_not_called()
