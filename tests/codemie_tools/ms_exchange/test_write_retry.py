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
import pytest

from codemie_tools.ms_exchange.errors import (
    CategoryConflictError,
    DeadlineExhaustedError,
    ExchangeError,
    NotAppliedError,
    SendAccessDeniedError,
    WriteConnectionLostUnknownError,
    WriteRateLimitedError,
    WriteUnknownError,
    InvalidWriteInputError,
    WriteTokenRejectedError,
)
from codemie_tools.ms_exchange.write_retry import Deadline, Retry, Stop, run_write


class FakeTime:
    """Fake clock whose sleep advances it, plus a recorded random source."""

    def __init__(self, start: float = 1000.0, rand_values: list[float] | None = None):
        self.now = start
        self.sleeps: list[float] = []
        self.rand_calls: list[tuple[float, float]] = []
        self._rand_values = list(rand_values or [])

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def rand(self, low: float, high: float) -> float:
        self.rand_calls.append((low, high))
        return self._rand_values.pop(0) if self._rand_values else high


class Boom(Exception):
    pass


def make(ft: FakeTime, seconds: float = 90.0) -> Deadline:
    return Deadline(seconds, clock=ft.clock)


def run(ft: FakeTime, submit, classify, deadline: Deadline | None = None):
    return run_write(submit, classify, deadline or make(ft), sleep=ft.sleep, rand=ft.rand)


class Submitter:
    """Fails with Boom until `succeed_on` (1-based); counts submissions."""

    def __init__(self, succeed_on: int | None = None, result: str = "sent"):
        self.calls = 0
        self.succeed_on = succeed_on
        self.result = result

    def __call__(self):
        self.calls += 1
        if self.succeed_on is not None and self.calls >= self.succeed_on:
            return self.result
        raise Boom


def always_retry(hint: float | None = None):
    return lambda exc: Retry(hint)


class TestDeadline:
    def test_default_budget_is_90_seconds(self):
        ft = FakeTime()
        assert Deadline(clock=ft.clock).remaining() == 90.0

    def test_remaining_shrinks_with_clock_and_expires(self):
        ft = FakeTime()
        deadline = make(ft, 10)
        ft.now += 4
        assert deadline.remaining() == pytest.approx(6)
        assert not deadline.expired()
        ft.now += 6
        assert deadline.remaining() == 0
        assert deadline.expired()

    def test_remaining_never_negative(self):
        ft = FakeTime()
        deadline = make(ft, 10)
        ft.now += 500
        assert deadline.remaining() == 0.0


class TestSubmissionCap:
    def test_success_on_first_try_returns_without_classify_or_sleep(self):
        ft = FakeTime()
        submit = Submitter(succeed_on=1)
        assert run(ft, submit, lambda exc: pytest.fail("classify called")) == "sent"
        assert submit.calls == 1
        assert ft.sleeps == []

    def test_retry_then_success_returns_result(self):
        ft = FakeTime()
        submit = Submitter(succeed_on=2)
        assert run(ft, submit, always_retry(2)) == "sent"
        assert submit.calls == 2
        assert ft.sleeps == [2]

    def test_attempt_cap_is_three_submissions(self):
        ft = FakeTime()
        submit = Submitter()
        with pytest.raises(WriteRateLimitedError):
            run(ft, submit, always_retry(1))
        assert submit.calls == 3
        assert len(ft.sleeps) == 2

    def test_retry_that_runs_out_of_submissions_raises_the_error_it_carries(self):
        ft = FakeTime()
        submit = Submitter()
        with pytest.raises(CategoryConflictError):
            run(ft, submit, lambda exc: Retry(0.0, on_exhausted=CategoryConflictError()))
        assert submit.calls == 3

    def test_retry_that_runs_out_of_time_raises_the_error_it_carries(self):
        ft = FakeTime()
        submit = Submitter()
        with pytest.raises(CategoryConflictError):
            run(ft, submit, lambda exc: Retry(60.0, on_exhausted=CategoryConflictError()), make(ft, seconds=30))
        assert submit.calls == 1
        assert ft.sleeps == []

    def test_cap_is_not_multiplied_by_repeated_calls_of_classify(self):
        ft = FakeTime()
        submit = Submitter()
        classified: list[BaseException] = []

        def classify(exc):
            classified.append(exc)
            return Retry(None)

        with pytest.raises(WriteRateLimitedError):
            run(ft, submit, classify)
        assert submit.calls == 3
        assert len(classified) == 3

    def test_success_after_deadline_passed_is_still_returned(self):
        ft = FakeTime()

        def submit():
            ft.now += 200
            return "sent"

        assert run(ft, submit, always_retry()) == "sent"


class TestServerHint:
    def test_hint_of_45_seconds_is_waited_in_full(self):
        ft = FakeTime()
        assert run(ft, Submitter(succeed_on=2), always_retry(45)) == "sent"
        assert ft.sleeps == [45]
        assert ft.rand_calls == []

    def test_hint_is_never_shortened_even_when_tiny(self):
        ft = FakeTime()
        run(ft, Submitter(succeed_on=2), always_retry(0.25))
        assert ft.sleeps == [0.25]

    def test_hint_over_remaining_time_stops_without_sleeping(self):
        ft = FakeTime()
        submit = Submitter()
        with pytest.raises(WriteRateLimitedError):
            run(ft, submit, always_retry(45), make(ft, 30))
        assert ft.sleeps == []
        assert submit.calls == 1

    def test_hint_equal_to_remaining_is_waited(self):
        ft = FakeTime()
        with pytest.raises(WriteRateLimitedError):
            run(ft, Submitter(), always_retry(30), make(ft, 30))
        assert ft.sleeps == [30]

    def test_second_hint_is_checked_against_time_left_after_first_wait(self):
        ft = FakeTime()
        hints = iter([40, 40])
        submit = Submitter()
        with pytest.raises(WriteRateLimitedError):
            run(ft, submit, lambda exc: Retry(next(hints)), make(ft, 70))
        assert ft.sleeps == [40]
        assert submit.calls == 2

    @pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf")])
    def test_invalid_hint_falls_back_to_local_jitter(self, bad):
        ft = FakeTime(rand_values=[0.4])
        run(ft, Submitter(succeed_on=2), always_retry(bad))
        assert ft.rand_calls == [(0, 1)]
        assert ft.sleeps == [0.4]


class TestLocalJitter:
    def test_jitter_bounds_per_retry(self):
        ft = FakeTime(rand_values=[0.3, 1.7])
        with pytest.raises(WriteRateLimitedError):
            run(ft, Submitter(), always_retry(None))
        assert ft.rand_calls == [(0, 1), (0, 2)]
        assert ft.sleeps == [0.3, 1.7]

    def test_local_delay_is_capped_at_30_seconds(self):
        ft = FakeTime(rand_values=[99.0])
        run(ft, Submitter(succeed_on=2), always_retry(None))
        assert ft.sleeps == [30]

    def test_cap_does_not_apply_to_server_hint(self):
        ft = FakeTime()
        run(ft, Submitter(succeed_on=2), always_retry(60))
        assert ft.sleeps == [60]

    def test_jitter_longer_than_remaining_stops_without_sleeping(self):
        ft = FakeTime(rand_values=[0.9])
        submit = Submitter()
        with pytest.raises(WriteRateLimitedError):
            run(ft, submit, always_retry(None), make(ft, 0.5))
        assert ft.sleeps == []
        assert submit.calls == 1


class TestNeverReplayed:
    @pytest.mark.parametrize(
        "error_cls",
        [
            WriteTokenRejectedError,
            SendAccessDeniedError,
            InvalidWriteInputError,
            WriteConnectionLostUnknownError,
            WriteUnknownError,
        ],
    )
    def test_stop_raises_the_error_after_one_submission(self, error_cls):
        ft = FakeTime()
        submit = Submitter()
        with pytest.raises(error_cls):
            run(ft, submit, lambda exc: Stop(error_cls()))
        assert submit.calls == 1
        assert ft.sleeps == []

    def test_stop_error_never_chains_the_raw_cause(self):
        """The raw provider exception never reaches the raised error's __cause__ (it can carry secrets)."""
        ft = FakeTime()
        with pytest.raises(WriteUnknownError) as info:
            run(ft, Submitter(), lambda exc: Stop(WriteUnknownError()))
        assert info.value.__cause__ is None
        assert info.value.__suppress_context__ is True

    def test_exchange_error_with_outcome_is_reraised_when_classify_fails(self):
        ft = FakeTime()
        original = WriteConnectionLostUnknownError()
        calls = 0

        def submit():
            nonlocal calls
            calls += 1
            raise original

        def classify(exc):
            raise RuntimeError("classifier bug")

        with pytest.raises(WriteConnectionLostUnknownError) as info:
            run(ft, submit, classify)
        assert info.value is original
        assert calls == 1


class TestNothingRaw:
    def test_classifier_exception_becomes_unknown(self):
        ft = FakeTime()
        submit = Submitter()

        def classify(exc):
            raise RuntimeError("classifier bug")

        with pytest.raises(WriteUnknownError) as info:
            run(ft, submit, classify)
        assert submit.calls == 1
        assert info.value.__cause__ is None
        assert info.value.__suppress_context__ is True

    def test_unrecognised_classification_becomes_unknown(self):
        ft = FakeTime()
        with pytest.raises(WriteUnknownError):
            run(ft, Submitter(), lambda exc: "retry please")

    def test_unexpected_exception_is_never_raised_raw(self):
        ft = FakeTime()
        with pytest.raises(ExchangeError) as info:
            run(ft, Submitter(), lambda exc: Stop(WriteUnknownError()))
        assert info.value.OUTCOME == "unknown"

    def test_keyboard_interrupt_is_not_swallowed(self):
        ft = FakeTime()

        def submit():
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            run(ft, submit, always_retry())


class TestDeadlineSharedWithReads:
    def test_no_submission_starts_when_time_is_already_spent(self):
        ft = FakeTime()
        deadline = make(ft, 90)
        ft.now += 95  # token work and reads used everything
        submit = Submitter(succeed_on=1)
        with pytest.raises(DeadlineExhaustedError) as info:
            run(ft, submit, always_retry(), deadline)
        assert submit.calls == 0
        assert isinstance(info.value, NotAppliedError)
        assert info.value.OUTCOME == "not_applied"

    def test_time_spent_on_reads_shrinks_the_wait_budget(self):
        ft = FakeTime()
        deadline = make(ft, 90)
        ft.now += 60  # 30 s left; the 45 s hint no longer fits
        submit = Submitter()
        with pytest.raises(WriteRateLimitedError):
            run(ft, submit, always_retry(45), deadline)
        assert ft.sleeps == []
        assert submit.calls == 1

    def test_deadline_is_rechecked_after_every_attempt(self):
        ft = FakeTime()
        deadline = make(ft, 10)
        calls = 0

        def submit():
            nonlocal calls
            calls += 1
            ft.now += 12  # the attempt itself overran the deadline
            raise Boom

        with pytest.raises(WriteRateLimitedError):
            run(ft, submit, always_retry(0), deadline)
        assert calls == 1
        assert ft.sleeps == []

    def test_wait_that_consumes_remaining_time_ends_without_another_submission(self):
        ft = FakeTime()
        submit = Submitter()
        with pytest.raises(WriteRateLimitedError):
            run(ft, submit, always_retry(30), make(ft, 30))
        assert submit.calls == 1


class TestDeadlineExhaustedError:
    def test_message_is_a_fixed_not_applied_sentence(self):
        assert str(DeadlineExhaustedError()).startswith("Not applied.")
        assert DeadlineExhaustedError.RETRYABLE is False
