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

"""`bucket=ALL` says what `bucket=` only implied.

The Cleanup table defaults to the DELETE bucket, and "all buckets" was encoded
as an *empty* value. That made a meaningful selection indistinguishable from an
unset one: a URL reading `/cleanup?bucket=` looks like a serialization slip, and
it was mistaken for one during manual testing — while sitting next to a
parameter that genuinely was being dropped.

`ALL` is now the emitted spelling. The empty value keeps working, because URLs
are a shared surface: bookmarks, pasted links and the six Overview filter links
already in the wild all send it.
"""

from codemie.rest_api.routers.marketplace_management import _blank_means_no_filter


def test_all_is_translated_to_no_filter() -> None:
    """The sentinel the UI now emits."""
    assert _blank_means_no_filter("ALL") is None


def test_all_is_case_insensitive() -> None:
    """A hand-edited URL should not depend on shouting."""
    assert _blank_means_no_filter("all") is None


def test_empty_still_means_no_filter() -> None:
    """Old links must not start 422-ing.

    Every Overview filter link shipped so far sends `bucket=`, and any bookmark
    a reviewer saved does too.
    """
    assert _blank_means_no_filter("") is None


def test_a_real_bucket_passes_through_untouched() -> None:
    assert _blank_means_no_filter("DELETE") == "DELETE"


def test_an_unknown_value_is_left_for_the_enum_to_reject() -> None:
    """F-48: `?bucket=BOGUS` must 422 rather than quietly returning nothing.

    The translator must not swallow unknown values — it hands them to the enum,
    which rejects them.
    """
    assert _blank_means_no_filter("BOGUS") == "BOGUS"
