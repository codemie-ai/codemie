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

"""The navigation hook goes into the document before any page script, so it exists while the page boots."""

import itertools

import pytest

from codemie.hosted_apps_gateway import navigation
from codemie.hosted_apps_gateway.navigation import injection_offset, representation_range

META = b'<meta charset="utf-8">'


def at(document: bytes, offset: int) -> bytes:
    return document[:offset] + b"|" + document[offset:]


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        # Right after <meta charset>, which must stay within the first 1024 bytes.
        (
            b"<!doctype html><html><head>" + META + b"<title>x</title><script src=a.js></script>",
            b"<!doctype html><html><head>" + META + b"|<title>x</title><script src=a.js></script>",
        ),
        (
            b'<html><head><META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=utf-8"><script>',
            b'<html><head><META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=utf-8">|<script>',
        ),
        # Else the start of <head>, with attributes and in any case.
        (
            b'<!DOCTYPE html><HTML><HEAD lang="en" data-x=">"><SCRIPT>',
            b'<!DOCTYPE html><HTML><HEAD lang="en" data-x=">">|<SCRIPT>',
        ),
        (b"<html><head><title>t</title><script>", b"<html><head>|<title>t</title><script>"),
        (b"<html><header><head ><script>", b"<html><header><head >|<script>"),
        # A <meta charset> past the first 1024 bytes is not the declaration; <head> decides.
        (
            b"<html><head><!--" + b"x" * 1100 + b"-->" + META + b"<script>",
            b"<html><head>|<!--" + b"x" * 1100 + b"-->" + META + b"<script>",
        ),
        # Commented-out markup does not count.
        (
            b"<!doctype html><!-- <head><meta charset=utf-8> --><html><head><script>",
            b"<!doctype html><!-- <head><meta charset=utf-8> --><html><head>|<script>",
        ),
        # A page script before <head> or before <meta charset>: earlier, after the doctype or at the start.
        (
            b"<!doctype html><script>boot()</script><html><head>" + META,
            b"<!doctype html>|<script>boot()</script><html><head>" + META,
        ),
        (b"<html><head><script>x()</script>" + META, b"<html><head>|<script>x()</script>" + META),
        (b"<script>boot()</script><html><head>", b"|<script>boot()</script><html><head>"),
        # No <head>: after the doctype, else at the very start (after a byte order mark).
        (b"<!doctype html><html><body><script>", b"<!doctype html>|<html><body><script>"),
        (b"<html><body>Version content</body></html>", b"|<html><body>Version content</body></html>"),
        (b"\xef\xbb\xbf<p>hi<script>", b"\xef\xbb\xbf|<p>hi<script>"),
        (b"", b"|"),
    ],
)
def test_the_hook_goes_before_the_first_page_script(document, expected):
    assert at(document, injection_offset(document, len(document))) == expected


@pytest.mark.parametrize("bom", [b"\xff\xfe", b"\xfe\xff"])
def test_a_utf16_document_keeps_the_hook_at_its_end(bom):
    document = bom + "<html><head><script>".encode("utf-16-le")
    assert injection_offset(document, len(document)) == len(document)


def test_only_the_scanned_prefix_is_searched():
    document = b"<!doctype html>" + b" " * 20000 + b"<head><script>"
    prefix = document[:16384]
    assert injection_offset(prefix, len(document)) == len(b"<!doctype html>")


class Storage:
    def __init__(self, data):
        self.data = data

    async def read_range(self, key, start, end):
        assert 0 <= start <= end < len(self.data)
        yield self.data[start : end + 1]


@pytest.mark.asyncio
async def test_every_byte_range_of_the_representation_is_the_asset_with_the_hook_inside():
    asset, hook = b"0123456789", b"<script>h</script>"
    for offset in (0, 4, 10):
        whole = asset[:offset] + hook + asset[offset:]
        for start, end in itertools.combinations_with_replacement(range(len(whole)), 2):
            chunks = [
                chunk async for chunk in representation_range(Storage(asset), "k", len(asset), hook, start, end, offset)
            ]
            assert b"".join(chunks) == whole[start : end + 1], (offset, start, end)


@pytest.mark.asyncio
async def test_without_an_offset_the_hook_is_still_appended():
    chunks = [chunk async for chunk in representation_range(Storage(b"abc"), "k", 3, b"!", 0, 3)]
    assert b"".join(chunks) == b"abc!"


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        # "--->" closes the comment at its last "-->", like the first "-->" a browser finds.
        (b"<!-- a ---><html><head><script>", b"<!-- a ---><html><head>|<script>"),
        (b"<!-- <head> a ---><html><head><script>", b"<!-- <head> a ---><html><head>|<script>"),
        (b"<!-- a - b -- c --><head><script>", b"<!-- a - b -- c --><head>|<script>"),
        # A commented-out script before <head> does not move the hook in front of <head>.
        (b"<!-- <script> --><html><head><script>", b"<!-- <script> --><html><head>|<script>"),
        # An unclosed comment hides everything after it.
        (b"<html><!-- <head><meta charset=utf-8><script>", b"|<html><!-- <head><meta charset=utf-8><script>"),
    ],
)
def test_comments_end_at_their_first_closing_marker(document, expected):
    assert at(document, injection_offset(document, len(document))) == expected


@pytest.mark.parametrize(
    ("prefix", "span"),
    [
        (b"x<!-- a --->y", (1, 12)),
        (b"x<!---->y", (1, 8)),
        (b"x<!-- a -->b-->y", (1, 11)),
        (b"x<!-- line\none -->y", (1, 18)),
        (b"x<!-- unclosed\n-", (1, 16)),
        (b"x<!-- unclosed --", (1, 17)),
    ],
)
def test_a_comment_spans_to_its_first_closing_marker_or_the_end_of_the_prefix(prefix, span):
    assert navigation._COMMENT.search(prefix).span() == span


def test_comment_matching_stays_linear_on_long_dash_runs():
    prefix = b"<!--" + b"-" * 200_000 + b"x" * 200_000
    assert navigation._COMMENT.search(prefix).span() == (0, len(prefix))
