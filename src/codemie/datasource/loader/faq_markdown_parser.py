# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an “AS IS” BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Parser for FAQ markdown articles indexed by the Git FAQ datasource.

Each ``.md`` file in the repository is one FAQ article. Fields resolve through
a precedence chain (frontmatter -> markdown convention -> filename):

- ``title``: frontmatter ``title`` -> first ``#`` heading outside code fences -> humanized file name
- ``instructions``: frontmatter ``instructions`` -> text after the last standalone ``Prompt Instruction:`` line
- ``reference`` (routing anchor): frontmatter ``reference`` -> repo-relative path without extension
- ``references``: frontmatter list of links, passthrough metadata only

Corpus-derived hardening (validated against codemie-ai/docs/faq): ``---`` counts
as frontmatter only at byte 0 (mid-file rules are horizontal rules); the first
H1 wins on multi-heading files; ``#`` lines and markers inside fenced code blocks
are ignored; frontmatter scalar fields only accept strings — anything else falls
through to the next precedence level instead of dropping the file; invalid
frontmatter YAML raises :class:`FaqParseError` so the loader can skip the file.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

FRONTMATTER_DELIMITER = "---"
INSTRUCTIONS_MARKER = "Prompt Instruction:"
FENCE_PATTERN = re.compile(r"^(```|~~~)")


class FaqParseError(ValueError):
    """Raised when a FAQ markdown file cannot be parsed (e.g. invalid frontmatter YAML)."""


@dataclass
class FaqArticle:
    """One parsed FAQ article; metadata mirrors the Google Docs routing document shape."""

    title: str
    content: str
    instructions: str
    reference: str
    references: list[str] = field(default_factory=list)


def parse_faq_markdown(*, rel_path: str, raw: str) -> FaqArticle:
    """Parse one FAQ markdown file into a :class:`FaqArticle`.

    Args:
        rel_path: repo-relative file path with forward slashes.
        raw: full file content, LF or CRLF, with or without a UTF-8 BOM.

    Raises:
        FaqParseError: on unrecoverable structure (invalid frontmatter YAML).
    """
    raw = raw.replace("\r\n", "\n").lstrip("\ufeff")
    frontmatter, body = _split_frontmatter(raw)

    title = _frontmatter_str(frontmatter, "title") or _first_heading(body) or _humanize_filename(rel_path)
    instructions, content = _extract_instructions(_strip_first_heading(body), frontmatter)
    reference = _frontmatter_str(frontmatter, "reference") or _path_reference(rel_path)
    references = _frontmatter_references(frontmatter)

    return FaqArticle(
        title=title,
        content=_strip_blank_edges(content),
        instructions=instructions,
        reference=reference,
        references=references,
    )


def _is_block_scalar_continuation(line: str, indent: int) -> bool:
    return not line.strip() or len(line) - len(line.lstrip()) > indent


def _parse_frontmatter_yaml(block: str, body: str) -> tuple[dict[str, Any], str]:
    if not block.strip():
        return {}, body
    try:
        parsed = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        raise FaqParseError(f"Invalid frontmatter YAML: {exc}") from exc
    if parsed is None:
        return {}, body
    if not isinstance(parsed, dict):
        raise FaqParseError(f"Frontmatter must be a YAML mapping, got {type(parsed).__name__}")
    return parsed, body


def _split_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Split leading YAML frontmatter from the body; only a line-0 ``---`` opens frontmatter."""
    if not raw.startswith(f"{FRONTMATTER_DELIMITER}\n"):
        return {}, raw

    lines = raw.split("\n")
    block_scalar_indent = None
    for idx in range(1, len(lines)):
        line = lines[idx]
        if block_scalar_indent is not None:
            if _is_block_scalar_continuation(line, block_scalar_indent):
                continue
            block_scalar_indent = None
        if ":" in line and line.strip().endswith(("|", ">", "|-", ">-", "|+", ">+")):
            block_scalar_indent = len(line) - len(line.lstrip())
            continue
        if line.strip() == FRONTMATTER_DELIMITER:
            return _parse_frontmatter_yaml("\n".join(lines[1:idx]), "\n".join(lines[idx + 1 :]))

    return {}, raw


def _frontmatter_str(frontmatter: dict[str, Any], key: str) -> Optional[str]:
    """Return a frontmatter value only when it is a non-empty string scalar.

    Non-string scalars (numbers, booleans, dates, collections) fall through to the
    next precedence level instead of being stringified or dropping the file —
    YAML ``reference: 1.10`` must never silently become ``"1.1"``.
    """
    value = frontmatter.get(key)
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _frontmatter_references(frontmatter: dict[str, Any]) -> list[str]:
    value = frontmatter.get("references")
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in items if isinstance(item, str) and item.strip()]


def _iter_visible_lines(body: str):
    """Yield (is_fenced, line) tuples, tracking triple-backtick/tilde fences."""
    fenced = False
    for line in body.split("\n"):
        if FENCE_PATTERN.match(line):
            fenced = not fenced
        yield fenced, line


def _match_h1(line: str) -> Optional[str]:
    """Return heading text if line is an H1 (`# text`), else None."""
    if len(line) < 3 or line[0] != "#" or line[1] not in (" ", "\t"):
        return None
    return line[2:].strip() or None


def _first_heading(body: str) -> Optional[str]:
    for fenced, line in _iter_visible_lines(body):
        if not fenced:
            text = _match_h1(line)
            if text is not None:
                return text
    return None


def _strip_first_heading(body: str) -> str:
    result = []
    stripped = False
    for fenced, line in _iter_visible_lines(body):
        if not stripped and not fenced and _match_h1(line) is not None:
            stripped = True
            continue
        result.append(line)
    return "\n".join(result)


def _extract_instructions(body: str, frontmatter: dict[str, Any]) -> tuple[str, str]:
    instructions = _frontmatter_str(frontmatter, "instructions")
    if instructions is not None:
        return instructions, body

    # Split at the FIRST marker line outside code fences (marker may stand alone
    # or lead a line); everything after it is instructions. Later markers and
    # fenced/inline copies stay where they are.
    split_line = None
    lines = list(_iter_visible_lines(body))
    for idx, (fenced, line) in enumerate(lines):
        if not fenced and line.lstrip().startswith(INSTRUCTIONS_MARKER):
            split_line = idx
            break
    if split_line is not None:
        content = "\n".join(line for _, line in lines[:split_line])
        first_rest = lines[split_line][1].lstrip()[len(INSTRUCTIONS_MARKER) :].strip()
        tail = "\n".join(line for _, line in lines[split_line + 1 :]).strip()
        instructions = "\n".join(part for part in (first_rest, tail) if part)
        return instructions, content
    return "", body


def _path_reference(rel_path: str) -> str:
    reference = rel_path.replace("\\", "/")
    if reference.endswith(".md"):
        reference = reference[: -len(".md")]
    return reference


def _humanize_filename(rel_path: str) -> str:
    stem = rel_path.replace("\\", "/").rsplit("/", 1)[-1]
    if stem.endswith(".md"):
        stem = stem[: -len(".md")]
    words = re.split(r"[-_]+", stem)
    # Preserve inner casing (API, ES): uppercase the first letter only.
    return " ".join(word[:1].upper() + word[1:] for word in words if word)


def _strip_blank_edges(content: str) -> str:
    """Remove leading/trailing blank lines without touching indentation of real content."""
    return content.strip("\n")
