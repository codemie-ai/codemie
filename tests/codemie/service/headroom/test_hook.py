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

"""Tests for the Headroom fail-open pre-model hook."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from codemie.service.headroom import hook
from codemie.service.headroom.compressors.base import CompressOutcome, HeadroomCompressionError
from codemie.service.headroom.hook import build_headroom_pre_model_hook
from codemie.service.headroom.models import HeadroomStrategy, ResolvedHeadroomConfig

LOGGER = "codemie.service.headroom.hook.logger"
GET_COMPRESSOR = "codemie.service.headroom.hook.get_compressor"
GET_MODE = "codemie.service.headroom.hook.get_compressor_mode"
CALC_TOKENS = "codemie.service.headroom.hook.calculate_tokens"
MODEL = "gpt-4o"
SECRET = "confidential message body"
SHORT = "short"


def _cfg(user: bool = True, tools: bool = True) -> ResolvedHeadroomConfig:
    return ResolvedHeadroomConfig(
        compress_user_messages=user, compress_tool_outputs=tools, strategy=HeadroomStrategy.BALANCED
    )


class FakeCompressor:
    """Replaces every message content with SHORT unless `contents` is given."""

    mode = "sdk"

    def __init__(
        self,
        contents: list | None = None,
        count: int | None = None,
        error: Exception | None = None,
        tokens: tuple[int, int] = (100, 10),
    ):
        self.tokens = tokens
        self.contents = contents
        self.count = count
        self.error = error
        self.calls: list[list[dict]] = []

    def compress(self, messages, model, cfg):
        self.calls.append(messages)
        if self.error:
            raise self.error
        out = [dict(m, content=SHORT) for m in messages]
        if self.contents is not None:
            out = [dict(m, content=c) for m, c in zip(messages, self.contents, strict=True)]
        if self.count is not None:
            out = out[: self.count]
        return CompressOutcome(messages=out, tokens_before=self.tokens[0], tokens_after=self.tokens[1])


def _conversation() -> list:
    return [
        SystemMessage(content="system prompt", id="s1"),
        HumanMessage(content="user text " * 5, id="h1", name="alice", additional_kwargs={"k": "v"}),
        AIMessage(
            content="assistant text " * 5,
            id="a1",
            tool_calls=[{"name": "f", "args": {"x": 1}, "id": "call-1"}],
            additional_kwargs={"ak": 1},
        ),
        ToolMessage(content="tool output " * 5, tool_call_id="call-1", id="t1", name="f", artifact={"art": 1}),
    ]


def _run(
    fake: FakeCompressor,
    messages: list,
    cfg: ResolvedHeadroomConfig | None = None,
    min_tokens: int = 0,
    request_id: str | None = None,
):
    state = {"messages": messages}
    with (
        patch(GET_COMPRESSOR, return_value=fake),
        patch(GET_MODE, return_value="sdk"),
        patch("codemie.service.headroom.hook.config") as mock_config,
        patch(CALC_TOKENS, side_effect=lambda text, llm_model=None: len(str(text))),
    ):
        mock_config.HEADROOM_MIN_TOKENS = min_tokens
        hook_fn = build_headroom_pre_model_hook(cfg or _cfg(), MODEL, request_id)
        return hook_fn(state)


def test_none_config_returns_none():
    assert build_headroom_pre_model_hook(None, MODEL) is None


def test_below_min_tokens_skips_compressor():
    fake = FakeCompressor()

    result = _run(fake, _conversation(), min_tokens=10_000)

    assert result == {}
    assert fake.calls == []


def test_token_count_ignores_image_blocks_and_passes_model():
    fake = FakeCompressor()
    messages = [
        HumanMessage(
            content=[
                {"type": "text", "text": "abc"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ]
        ),
        HumanMessage(content="de"),
    ]
    with (
        patch(GET_COMPRESSOR, return_value=fake),
        patch(GET_MODE, return_value="sdk"),
        patch("codemie.service.headroom.hook.config") as mock_config,
        patch(CALC_TOKENS, return_value=1) as mock_calc,
    ):
        mock_config.HEADROOM_MIN_TOKENS = 10
        build_headroom_pre_model_hook(_cfg(), MODEL)({"messages": messages})

    counted = [c.args[0] for c in mock_calc.call_args_list]
    assert "".join(counted) == "abcde"
    assert all(c.kwargs.get("llm_model") == MODEL for c in mock_calc.call_args_list)
    assert fake.calls == []


def test_compresses_and_returns_full_list_preserving_system():
    fake = FakeCompressor()
    messages = _conversation()

    result = _run(fake, messages)

    out = result["llm_input_messages"]
    assert [m.type for m in out] == ["system", "human", "ai", "tool"]
    assert out[0] is messages[0]
    assert out[0].content == "system prompt"
    assert out[2] is messages[2]
    assert [m.content for m in out[1:]] == [SHORT, "assistant text " * 5, SHORT]


def test_compressor_called_with_model_and_openai_dicts():
    fake = FakeCompressor()
    seen = {}
    original_compress = fake.compress

    def spy(messages, model, cfg):
        seen.update(model=model, cfg=cfg)
        return original_compress(messages, model, cfg)

    fake.compress = spy

    _run(fake, _conversation())

    assert [m["role"] for m in fake.calls[0]] == ["system", "user", "assistant", "tool"]
    assert seen == {"model": MODEL, "cfg": _cfg()}


@pytest.mark.parametrize(
    ("user", "tools", "expected"),
    [
        (False, True, ["user text " * 5, "assistant text " * 5, SHORT]),
        (True, False, [SHORT, "assistant text " * 5, "tool output " * 5]),
        (False, False, None),
    ],
)
def test_toggles_are_respected(user, tools, expected):
    result = _run(FakeCompressor(), _conversation(), cfg=_cfg(user, tools))

    if expected is None:
        assert result == {}
        return
    assert [m.content for m in result["llm_input_messages"][1:]] == expected


@pytest.mark.parametrize(("user", "tools"), [(True, True), (False, True), (True, False)])
def test_assistant_replies_never_replaced_even_if_compressed(user, tools):
    messages = _conversation()

    out = _run(FakeCompressor(), messages, cfg=_cfg(user, tools))["llm_input_messages"]

    assert out[2] is messages[2]
    assert out[2].content == "assistant text " * 5


def test_system_message_never_replaced_even_if_compressed():
    fake = FakeCompressor()
    result = _run(fake, [SystemMessage(content="sys"), HumanMessage(content="hello world")])

    assert result["llm_input_messages"][0].content == "sys"
    assert result["llm_input_messages"][1].content == SHORT


def test_changed_messages_preserve_metadata():
    messages = _conversation()

    out = _run(FakeCompressor(), messages)["llm_input_messages"]

    assert out[1].id == "h1" and out[1].name == "alice" and out[1].additional_kwargs == {"k": "v"}
    assert out[3].tool_call_id == "call-1" and out[3].id == "t1" and out[3].name == "f"
    assert out[3].artifact == {"art": 1}


def test_list_content_and_image_messages_keep_original_and_request_is_text_only():
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    list_msg = HumanMessage(content=[{"type": "text", "text": "list text"}, image], id="h-list")
    image_only = HumanMessage(content=[image], id="h-img")
    str_msg = HumanMessage(content="plain string", id="h-str")
    fake = FakeCompressor()

    result = _run(fake, [list_msg, image_only, str_msg])

    out = result["llm_input_messages"]
    assert out[0] is list_msg and out[0].content == [{"type": "text", "text": "list text"}, image]
    assert out[1] is image_only and out[1].content == [image]
    assert out[2].content == SHORT
    request = fake.calls[0]
    assert len(request) == 3
    assert request[0]["content"] == "list text"
    assert request[1]["content"] in ("", [])
    assert "image" not in str(request)


def test_unchanged_outcome_returns_empty():
    messages = _conversation()
    contents = [m.content for m in messages]

    result = _run(FakeCompressor(contents=contents), messages)

    assert result == {}


@pytest.mark.parametrize("tokens", [(100, 120), (100, 100)])
def test_inflated_or_equal_token_count_keeps_original_messages(tokens):
    result = _run(FakeCompressor(tokens=tokens), _conversation())

    assert result == {}


def test_inflation_guard_is_recorded_as_unchanged():
    result, rec, log = _observe(FakeCompressor(tokens=(100, 120)), _secret_conversation())

    attrs = _attrs(rec)
    assert result == {}
    assert attrs[PREFIX + "outcome"] == "unchanged"
    assert attrs[PREFIX + "tokens_after"] == 120
    _assert_info_line(log, attrs)


def _list_conversation(tool_blocks, human_blocks=None):
    return [
        SystemMessage(content="system prompt"),
        HumanMessage(content=human_blocks or "get the data"),
        AIMessage(content="", tool_calls=[{"name": "f", "args": {}, "id": "c1"}]),
        ToolMessage(content=tool_blocks, tool_call_id="c1"),
    ]


def test_text_only_block_list_is_compressed():
    messages = _list_conversation([{"type": "text", "text": "tool output " * 20}])

    result = _run(FakeCompressor(), messages)

    assert result["llm_input_messages"][3].content == SHORT


def test_block_list_with_extra_keys_or_images_keeps_original():
    cached = [{"type": "text", "text": "tool output " * 20, "cache_control": {"type": "ephemeral"}}]
    image = [{"type": "text", "text": "tool output " * 20}, {"type": "image_url", "image_url": {"url": "u"}}]

    for blocks in (cached, image):
        messages = _list_conversation(blocks)
        result = _run(FakeCompressor(), messages)
        assert result["llm_input_messages"][3] is messages[3]


def test_only_changed_messages_are_new_objects():
    messages = _conversation()
    contents = [m.content for m in messages]
    contents[1] = SHORT

    out = _run(FakeCompressor(contents=contents), messages)["llm_input_messages"]

    assert out[0] is messages[0]
    assert out[1] is not messages[1] and out[1].content == SHORT
    assert out[2] is messages[2] and out[3] is messages[3]


def test_non_string_compressed_content_is_ignored():
    messages = _conversation()
    contents = [m.content for m in messages]
    contents[1] = [{"type": "text", "text": "x"}]

    assert _run(FakeCompressor(contents=contents), messages) == {}


def test_count_mismatch_fails_open():
    assert _run(FakeCompressor(count=2), _conversation()) == {}


@pytest.mark.parametrize("error", [HeadroomCompressionError("boom"), RuntimeError("kaput"), ValueError("bad")])
def test_exceptions_fail_open(error):
    assert _run(FakeCompressor(error=error), _conversation()) == {}


def test_input_state_and_messages_not_mutated():
    messages = _conversation()
    snapshot = [m.model_copy(deep=True) for m in messages]
    original_objects = list(messages)

    _run(FakeCompressor(), messages)

    assert messages == snapshot
    assert all(a is b for a, b in zip(messages, original_objects, strict=True))


def test_warning_logs_exception_type_only():
    error = HeadroomCompressionError(f"leaked {SECRET}")
    messages = [HumanMessage(content=SECRET), SystemMessage(content="s")]

    with patch(LOGGER) as mock_logger:
        result = _run(FakeCompressor(error=error), messages)

    assert result == {}
    mock_logger.warning.assert_called_once()
    logged = mock_logger.warning.call_args.args[0]
    assert "HeadroomCompressionError" in logged
    assert SECRET not in logged
    assert "leaked" not in logged
    assert hook.logger is not None


# --- observability: headroom.compress span and structured log ---------------------------------------------------

SPAN = "codemie.service.headroom.hook.span"
SET_ATTR = "codemie.service.headroom.hook.set_span_attribute"
PREFIX = "codemie.headroom."
ATTR_KEYS = {PREFIX + k for k in ("mode", "strategy", "tokens_before", "tokens_after", "outcome")}


class _SpanRecorder:
    """Stands in for `span(name)` and `set_span_attribute`: records names and the attributes set on each span."""

    def __init__(self, fail_on_set: bool = False):
        self.names: list[str] = []
        self.spans: list[dict] = []
        self.fail_on_set = fail_on_set

    def __call__(self, name, *args, **kwargs):
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            self.names.append(name)
            self.spans.append({})
            yield None

        return _cm()

    def set_attribute(self, key, value):
        if self.fail_on_set:
            raise RuntimeError("telemetry down")
        self.spans[-1][key] = value


def _observe(fake, messages, cfg=None, min_tokens=0, recorder=None, get_compressor_error=None, mode="sdk"):
    recorder = recorder or _SpanRecorder()
    get_patch = (
        patch(GET_COMPRESSOR, side_effect=get_compressor_error)
        if get_compressor_error
        else patch(GET_COMPRESSOR, return_value=fake)
    )
    with (
        get_patch,
        patch(GET_MODE, return_value=mode),
        patch(SPAN, recorder),
        patch(SET_ATTR, recorder.set_attribute),
        patch(LOGGER) as mock_logger,
        patch("codemie.service.headroom.hook.config") as mock_config,
        patch(CALC_TOKENS, side_effect=lambda text, llm_model=None: len(str(text))),
    ):
        mock_config.HEADROOM_MIN_TOKENS = min_tokens
        result = build_headroom_pre_model_hook(cfg or _cfg(), MODEL)({"messages": messages})
    return result, recorder, mock_logger


def _secret_conversation() -> list:
    return [
        SystemMessage(content="system prompt"),
        HumanMessage(content=SECRET),
        ToolMessage(content=SECRET, tool_call_id="c"),
    ]


def _attrs(recorder) -> dict:
    assert recorder.names == ["headroom.compress"]
    attrs = recorder.spans[0]
    assert set(attrs) == ATTR_KEYS
    return attrs


def _assert_info_line(mock_logger, attrs):
    mock_logger.info.assert_called_once()
    line = mock_logger.info.call_args.args[0]
    for key in ("mode", "strategy", "tokens_before", "tokens_after", "outcome"):
        assert f"{key}={attrs[PREFIX + key]}" in line
    assert SECRET not in line


def _assert_no_content_leak(attrs, mock_logger):
    blob = " ".join(str(v) for v in attrs.values()) + " ".join(str(c) for c in mock_logger.mock_calls)
    assert SECRET not in blob


def test_span_compressed_outcome():
    result, rec, log = _observe(FakeCompressor(), _secret_conversation())

    attrs = _attrs(rec)
    assert "llm_input_messages" in result
    assert attrs[PREFIX + "outcome"] == "compressed"
    assert attrs[PREFIX + "mode"] == "sdk"
    assert attrs[PREFIX + "strategy"] == "balanced"
    assert type(attrs[PREFIX + "strategy"]) is str
    assert attrs[PREFIX + "tokens_before"] == 100
    assert attrs[PREFIX + "tokens_after"] == 10
    _assert_info_line(log, attrs)
    _assert_no_content_leak(attrs, log)


def test_span_mode_is_the_configured_mode():
    _, rec, log = _observe(FakeCompressor(), _secret_conversation(), mode="sidecar")

    attrs = _attrs(rec)
    assert attrs[PREFIX + "mode"] == "sidecar"
    _assert_info_line(log, attrs)


def test_span_not_applied_outcome_when_saving_is_reported_but_nothing_changes():
    messages = _secret_conversation()

    result, rec, log = _observe(FakeCompressor(contents=[m.content for m in messages]), messages)

    attrs = _attrs(rec)
    assert result == {}
    assert attrs[PREFIX + "outcome"] == "not_applied"
    assert attrs[PREFIX + "mode"] == "sdk"
    assert attrs[PREFIX + "tokens_before"] == 100
    assert attrs[PREFIX + "tokens_after"] == 10
    _assert_info_line(log, attrs)
    _assert_no_content_leak(attrs, log)


def test_span_skipped_small_outcome_does_not_touch_compressor():
    fake = FakeCompressor()
    messages = _secret_conversation()
    expected_tokens = sum(len(m.content) for m in messages)

    result, rec, log = _observe(
        fake, messages, min_tokens=10_000, get_compressor_error=AssertionError("must not be obtained")
    )

    attrs = _attrs(rec)
    assert result == {}
    assert fake.calls == []
    assert attrs[PREFIX + "outcome"] == "skipped_small"
    assert attrs[PREFIX + "mode"] == "sdk"
    assert attrs[PREFIX + "strategy"] == "balanced"
    assert attrs[PREFIX + "tokens_before"] == expected_tokens
    assert attrs[PREFIX + "tokens_after"] == expected_tokens
    _assert_info_line(log, attrs)
    _assert_no_content_leak(attrs, log)


def test_span_failed_open_on_compression_error():
    error = HeadroomCompressionError(f"leaked {SECRET}")

    result, rec, log = _observe(FakeCompressor(error=error), _secret_conversation())

    attrs = _attrs(rec)
    assert result == {}
    assert attrs[PREFIX + "outcome"] == "failed_open"
    assert attrs[PREFIX + "mode"] == "sdk"
    assert attrs[PREFIX + "tokens_before"] == 0
    assert attrs[PREFIX + "tokens_after"] == 0
    _assert_info_line(log, attrs)
    _assert_no_content_leak(attrs, log)


def test_span_failed_open_on_count_mismatch():
    result, rec, log = _observe(FakeCompressor(count=1), _secret_conversation())

    attrs = _attrs(rec)
    assert result == {}
    assert attrs[PREFIX + "outcome"] == "failed_open"
    assert attrs[PREFIX + "mode"] == "sdk"
    assert attrs[PREFIX + "tokens_before"] == 0
    assert attrs[PREFIX + "tokens_after"] == 0
    _assert_info_line(log, attrs)
    _assert_no_content_leak(attrs, log)


def test_span_failed_open_on_unexpected_exception():
    result, rec, log = _observe(FakeCompressor(error=RuntimeError(f"kaput {SECRET}")), _secret_conversation())

    attrs = _attrs(rec)
    assert result == {}
    assert attrs[PREFIX + "outcome"] == "failed_open"
    _assert_info_line(log, attrs)
    _assert_no_content_leak(attrs, log)


def test_span_failed_open_keeps_configured_mode_when_compressor_unavailable():
    result, rec, log = _observe(
        None, _secret_conversation(), get_compressor_error=RuntimeError(f"no compressor {SECRET}"), mode="sidecar"
    )

    attrs = _attrs(rec)
    assert result == {}
    assert attrs[PREFIX + "outcome"] == "failed_open"
    assert attrs[PREFIX + "mode"] == "sidecar"
    assert attrs[PREFIX + "tokens_before"] == 0
    _assert_info_line(log, attrs)
    _assert_no_content_leak(attrs, log)


def test_one_span_and_one_info_line_per_invocation():
    fake = FakeCompressor()
    recorder = _SpanRecorder()

    for _ in range(2):
        _, _, log = _observe(fake, _secret_conversation(), recorder=recorder)
        log.info.assert_called_once()

    assert recorder.names == ["headroom.compress", "headroom.compress"]


def test_telemetry_failure_never_breaks_compression():
    result, rec, _ = _observe(FakeCompressor(), _secret_conversation(), recorder=_SpanRecorder(fail_on_set=True))

    assert [m.content for m in result["llm_input_messages"][1:]] == [SHORT, SHORT]
    assert rec.names == ["headroom.compress"]


@pytest.mark.parametrize("mode", ["sidecar", "sdk"])
@pytest.mark.parametrize("min_tokens", [0, 10_000])
def test_span_mode_is_only_sidecar_or_sdk(mode, min_tokens):
    _, rec, _ = _observe(FakeCompressor(), _secret_conversation(), mode=mode, min_tokens=min_tokens)

    assert _attrs(rec)[PREFIX + "mode"] == mode


# --- savings reported to the request summary --------------------------------------------------------------------

ADD_HEADROOM = "codemie.service.headroom.hook.RequestSummaryManager"


def test_compressed_run_records_one_savings_event_with_recounted_tokens():
    with patch(ADD_HEADROOM) as manager:
        result = _run(FakeCompressor(tokens=(999, 1)), _conversation(), request_id="req-1")

    assert "llm_input_messages" in result
    add = manager.return_value.add_headroom
    add.assert_called_once()
    request_id, savings = add.call_args.args
    assert request_id == "req-1"
    # recounted locally (len of text), not the compressor's 999/1: 13 + 50 + 75 + 60 before, 13 + 5 + 75 + 5 after
    # (the assistant reply is kept verbatim)
    assert (savings.tokens_original, savings.tokens_compacted, savings.tokens_saved) == (198, 98, 100)
    assert savings.mode == "sdk"
    assert savings.strategy == "balanced"
    assert savings.calls == 1


def test_no_request_id_records_nothing():
    with patch(ADD_HEADROOM) as manager:
        _run(FakeCompressor(), _conversation())

    manager.return_value.add_headroom.assert_not_called()


@pytest.mark.parametrize("scenario", ["unchanged", "inflated", "skipped_small", "not_applied", "failed"])
def test_non_compressed_outcomes_record_nothing(scenario):
    messages = _conversation()
    kwargs = {}
    fake = FakeCompressor(contents=[m.content for m in messages])  # unchanged
    if scenario == "inflated":
        fake = FakeCompressor(tokens=(100, 120))
    elif scenario == "skipped_small":
        fake, kwargs = FakeCompressor(), {"min_tokens": 10_000}
    elif scenario == "not_applied":
        fake = FakeCompressor()
        messages = [SystemMessage(content="only system " * 5)]
    elif scenario == "failed":
        fake = FakeCompressor(error=HeadroomCompressionError("boom"))

    with patch(ADD_HEADROOM) as manager:
        _run(fake, messages, request_id="req-1", **kwargs)

    manager.return_value.add_headroom.assert_not_called()


def test_recount_that_shows_no_saving_records_nothing():
    # The compressor claims a saving but the merged text is not smaller than the original.
    messages = [HumanMessage(content="ab")]
    fake = FakeCompressor(contents=["abcd"], tokens=(100, 10))

    with patch(ADD_HEADROOM) as manager:
        result = _run(fake, messages, request_id="req-1")

    assert "llm_input_messages" in result
    manager.return_value.add_headroom.assert_not_called()


def test_recording_failure_is_fail_open_and_keeps_the_compression():
    with patch(ADD_HEADROOM) as manager, patch(LOGGER) as log:
        manager.return_value.add_headroom.side_effect = RuntimeError(SECRET)
        result = _run(FakeCompressor(), _conversation(), request_id="req-1")

    assert "llm_input_messages" in result
    assert SECRET not in str(log.mock_calls)
