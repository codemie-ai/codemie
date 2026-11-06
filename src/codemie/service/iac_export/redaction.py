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

"""Redaction of credentials embedded in free-form MCP url / command / argument fields."""

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from codemie.service.iac_export.credential_ref import env_ref
from codemie.service.iac_export.document import ExportError

# `auth` alone would flag benign names such as --author, so it only matches authorization/auth-token style names.
_SECRET_NAME = re.compile(
    r"token|secret|passw(or)?d|pwd|api[-_]?key|apikey|private[-_]?key|access[-_]?key|auth(?!or(?!ization))"
    r"|bearer|credential|signature",
    re.I,
)
# Names too short or generic to match anywhere, secret only as the whole parameter or flag name.
_EXACT_SECRET_NAME = re.compile(r"(key|sig|code|pass|passphrase)", re.I)
# Names that contain a secret word but are sizes or counts (`--max-tokens 4096`). Split in two so each
# regex stays under the complexity gate; combined in `_strip_benign`.
_BENIGN_PREFIXED = re.compile(r"(?:max|min|num|total|prompt|completion|input|output|context)[-_]?tokens?", re.I)
_BENIGN_SUFFIXED = re.compile(r"tokens?[-_]?(?:limit|count|budget|size|max)", re.I)


def _strip_benign(text: str, repl: str) -> str:
    return _BENIGN_SUFFIXED.sub(repl, _BENIGN_PREFIXED.sub(repl, text))


# A quote may sit between the name and the separator (`"apiKey": "..."` in a JSON argument).
_KEY_WORD = re.compile(r"(?:^|[-_ ])key(?:$|[-_ ])", re.I)  # X-Key, SERVICE_KEY_ID
# Possessive quantifiers (`*+`/`++`) and the leading `\b` stop the engine from re-trying this match at every
# offset of a long run with no real separator (`"apikey" * 10000` took ~20s before this; CR 1f3a8151).
_SECRET_ARG = re.compile(rf"\b(?:{_SECRET_NAME.pattern})[\w-]*+[\"']?+(?:\s*+[=:]\s*+|\s++)\S++", re.I)
_EXACT_SECRET_ASSIGNMENT = re.compile(r"(?:^|[\s?&;-])(key|sig|pass|passphrase)\s*=\s*\S+", re.I)
# The first segment excludes ':' too, so the two quantifiers never compete for the same characters.
_URL_USERINFO = re.compile(r"://[^/\s@:]*:[^/\s@]*@|://[^/\s@:]+@")
# A long mixed letter-and-digit run is an opaque token even when no name or well-known prefix says so.
# Hyphenated words (repository names) and UUIDs are not tokens, so only an unbroken run counts.
_OPAQUE_RUN = re.compile(r"[A-Za-z0-9_=+]{24,}")
_HEX_RUN = re.compile(r"[0-9a-f]{32,}")
# Well-known literal credential shapes that carry no name at all (positional arguments). The `eyJ` branch's three
# `{5,}+` are possessive: without it, three adjacent unbounded repetitions separated by a literal `.` backtrack
# against each other on a long run with no (or misplaced) dots (CR 1f3a8151).
_TOKEN_LITERAL = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}"
    r"|eyJ[\w-]{5,}+\.[\w-]{5,}+\.[\w-]{5,}+)"
)
_BARE_FLAG = re.compile(r"^-{1,2}[\w-]+$")
_SECRET_SHORT_FLAGS = frozenset({"-p", "-k"})
# Defense in depth alongside the possessive fixes above: no regex here should be quadratic any more, but this bounds
# the worst case regardless, for any pattern added later too. Longer than any realistic field value (CR 1f3a8151).
_MAX_SCAN_LENGTH = 50_000


def _too_long_to_scan(text: str) -> bool:
    return len(text) > _MAX_SCAN_LENGTH


def is_secret_name(name: str) -> bool:
    """A parameter, header or flag name that implies its value is a credential."""
    plain = _strip_benign(name, "")
    return bool(_SECRET_NAME.search(plain) or _KEY_WORD.search(name) or _EXACT_SECRET_NAME.fullmatch(name.strip("-")))


def _is_opaque(segment: str) -> bool:
    """A mixed letter-and-digit token such as a webhook secret or an encoded config blob."""
    if _HEX_RUN.fullmatch(segment):
        return True
    return (
        bool(_OPAQUE_RUN.fullmatch(segment))
        and any(c.isdigit() for c in segment)
        and any(c.isupper() for c in segment)
        and any(c.islower() for c in segment)
    )


def url_has_credentials(url: str) -> bool:
    if _too_long_to_scan(url):
        return True  # cannot be shown free of credentials without an unbounded scan: fail closed
    try:
        parts = urlsplit(url)
        _ = parts.port  # raises ValueError for a malformed port
        query = parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:
        return True  # an unparsable url cannot be shown to be free of credentials: fail closed
    if parts.username is not None or parts.password is not None:
        return True
    if any(is_secret_name(k) or _is_opaque(v) for k, v in query):
        return True
    return any(_is_opaque(segment) for segment in parts.path.split("/"))


def text_has_credentials(text: str) -> bool:
    if _too_long_to_scan(text):
        return True  # fail closed rather than run an unbounded scan
    return bool(
        _SECRET_ARG.search(_strip_benign(text, "x"))
        or _EXACT_SECRET_ASSIGNMENT.search(text)
        or _URL_USERINFO.search(text)
        or _TOKEN_LITERAL.search(text)
    )


def has_literal_credential(text: str) -> bool:
    """Strong, name-independent signals only (token shapes, URL userinfo); safe to run on prose such as prompts.

    No length cap here (review finding, auto_epmd-edp_vcs): system prompts and skill content are legitimately
    long, and the "large content exports inline" criterion means this check must not fail closed on size alone.
    Unlike `url_has_credentials` / `text_has_credentials` above, it is safe to run unbounded: `_TOKEN_LITERAL` and
    `_URL_USERINFO` are its only patterns, and both are already linear (CR 1f3a8151 fixed the one that wasn't,
    the `eyJ` branch of `_TOKEN_LITERAL`).
    """
    return bool(_TOKEN_LITERAL.search(text) or _URL_USERINFO.search(text))


def redact_url(url: str | None, ident: str | None, key: str) -> str | None:
    """The url, or an env reference when it embeds userinfo, a secret query/path or a token literal."""
    if url and (url_has_credentials(url) or text_has_credentials(url)):
        return env_ref(ident, key)
    return url


def redact_link(link: str | None, ident: str | None, key: str) -> str | None:
    """A repository / document link, or an env reference when it carries userinfo, a secret query or a token."""
    if link and (url_has_credentials(link) or text_has_credentials(link)):
        return env_ref(ident, key)
    return link


def redact_args(text: str | None, ident: str | None, key: str) -> str | None:
    """The command/argument text, or an env reference when it carries a credential."""
    if text and text_has_credentials(text):
        return env_ref(ident, key)
    return text


def _is_secret_flag(token: str) -> bool:
    """A bare flag (no attached value) that names a credential, so its next argument is the secret."""
    if not _BARE_FLAG.match(token):
        return False
    return token in _SECRET_SHORT_FLAGS or is_secret_name(token)


def redact_arg_list(args: list[str] | None, ident: str | None, key: str) -> list[str] | None:
    """Each element is judged alone and as the value of a preceding secret-named bare flag (``--token VALUE``)."""
    if not args:
        return args
    out: list[str] = []
    for i, arg in enumerate(args):
        previous = args[i - 1] if i else ""
        value_of_secret_flag = _is_secret_flag(previous) and not arg.startswith("-")
        if previous == "-p" and arg.isdigit():
            value_of_secret_flag = False  # a port, not a password
        if text_has_credentials(arg) or value_of_secret_flag or _json_arg_has_secret(arg):
            out.append(env_ref(ident, f"{key}_{i}"))
        else:
            out.append(arg)
    return out


def _json_arg_has_secret(arg: str) -> bool:
    """An argument that is a JSON document (`--config '{"apiKey": ...}'`) is judged by its parsed content."""
    if not arg.lstrip().startswith(("{", "[")):
        return False
    try:
        parsed = json.loads(arg)
    except ValueError:
        return False
    return redact_free_form(parsed, "x", "x") != parsed


def redact_free_form(value: Any, ident: str | None, key: str) -> Any:
    """Secret-looking string values anywhere in a free-form structure become env references; shape is kept.

    A string is replaced when it sits under a secret-named key or carries a credential itself. Template
    placeholders (``{{ var }}`` / ``${VAR}``) and values that already are references are left alone.
    """
    if isinstance(value, dict):
        return {k: _redact_entry(k, v, ident, f"{key}_{k}") for k, v in value.items()}
    if isinstance(value, list):
        return [redact_free_form(v, ident, f"{key}_{i}") for i, v in enumerate(value)]
    if isinstance(value, str) and _literal_secret(value):
        return env_ref(ident, key)
    return value


_PLACEHOLDER = re.compile(r"\{\{[^{}]*\}\}|\$\{[^{}]*\}")
_ENV_REF = re.compile(r"env:\w+")
_AUTH_SCHEMES = frozenset({"", "bearer", "basic", "token"})


def _is_placeholder(value: str) -> bool:
    """A reference or a template with nothing literal but an auth scheme word around the placeholders."""
    if _ENV_REF.fullmatch(value):
        return True
    return _PLACEHOLDER.search(value) is not None and _PLACEHOLDER.sub("", value).strip().lower() in _AUTH_SCHEMES


def _literal_secret(value: str) -> bool:
    if not value or _is_placeholder(value):
        return False
    return text_has_credentials(value) or ("://" in value and url_has_credentials(value))


def _has_secret_value(value: Any) -> bool:
    """Any non-empty, non-flag value; a number, list or object under a secret name is a secret too."""
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, str):
        return bool(value) and not _is_placeholder(value)
    return bool(value) or value == 0


def _redact_entry(name: Any, value: Any, ident: str | None, key: str) -> Any:
    if is_secret_name(str(name)) and _has_secret_value(value):
        return env_ref(ident, key)
    return redact_free_form(value, ident, key)


# MCP `command` / `arguments` / `config` (its `command`, `url`, `args`, `headers`, `env`) and `mcp_connect_url` are
# slots the envelope's credential resolver walks on apply (codemie_iac `plan_apply/credentials.py` MCP_REF_FIELDS):
# an unresolved value there is never shown to the operator, so name/shape heuristics would leave real secrets in the
# document. Every literal value in these slots becomes a reference by position; only a pure template stays literal.
def redact_positional(value: str | None, ident: str | None, key: str) -> str | None:
    """A single-value positional slot: any literal becomes a reference, a placeholder or empty value stays as is."""
    return env_ref(ident, key) if _has_secret_value(value) else value


def redact_positional_list(args: list[str] | None, ident: str | None, key: str) -> list[str] | None:
    """Each element of a positional slot list is judged on its own; no name or shape heuristic is consulted."""
    if not args:
        return args
    return [env_ref(ident, f"{key}_{i}") if _has_secret_value(a) else a for i, a in enumerate(args)]


def redact_positional_map(value: dict[str, Any], ident: str | None, key: str) -> dict[str, Any]:
    """Every entry of a positional slot mapping (MCP `env` / `headers`) is a credential, whatever its name."""
    return {k: env_ref(ident, f"{key}_{k}") if _has_secret_value(v) else v for k, v in value.items()}


def require_server_name(name: object, owner: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ExportError(f"MCP server of '{owner}' has no name; cannot derive credential references")
    return name
