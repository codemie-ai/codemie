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

"""Postgres framework sessions: repository normalization as the CLI reports it, aggregation and the ES merge."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from codemie.service.usage_attribution.framework_sessions import (
    aggregate_pg_rows,
    merge_framework,
    normalize_repository,
)


@pytest.mark.parametrize(
    "remote, expected",
    [
        ("https://github.com/owner/repo", "owner/repo"),
        ("https://github.com/owner/repo.git", "owner/repo"),
        ("https://github.com/owner/repo.GIT", "owner/repo"),
        ("http://gitlab.local:8080/owner/repo.git", "owner/repo"),
        ("git@github.com:owner/repo.git", "owner/repo"),
        ("git@github.com:owner/repo", "owner/repo"),
        ("ssh://git@github.com/owner/repo.git", "owner/repo"),
        ("ssh://git@host.example:2222/owner/repo.git", "owner/repo"),
        ("github-work:org/repo.git", "org/repo"),
        ("https://gitlab.com/a/b/c/repo.git", "a/b/c/repo"),
        ("https://gitlab.com/Group/Sub/Repo", "group/sub/repo"),
        ("https://github.com/owner/repo/", "owner/repo"),
        ("https://github.com/Owner/Repo.git", "owner/repo"),
        ("https://user:token@github.com/o/r.git", "o/r"),
        ("https://github.com/owner/repo.git?ref=x#frag", "owner/repo"),
        ("https://github.com/owner/my%20repo.git", "owner/my repo"),
        ("https://github.com/owner/r%2Fx.git", "owner/r%2fx"),
        ("https://github.com/owner/bad%ZZ.git", "owner/bad%zz"),
        ("https://github.com/owner/ctl%0D%0A.git", "owner/ctl%0d%0a"),
        # Azure DevOps
        ("https://dev.azure.com/Org/Project/_git/Repo", "org/project/repo"),
        ("https://user@dev.azure.com/Org/Project/_git/Repo", "org/project/repo"),
        ("git@ssh.dev.azure.com:v3/Org/Project/Repo", "org/project/repo"),
        ("https://org.visualstudio.com/Project/_git/Repo", "org/project/repo"),
        ("https://Org.visualstudio.com/DefaultCollection/Project/_git/Repo", "org/project/repo"),
        ("org@vs-ssh.visualstudio.com:v3/Org/Project/Repo", "org/project/repo"),
        # Bitbucket
        ("https://bitbucket.example.com/scm/PROJ/repo.git", "proj/repo"),
        ("https://bitbucket.example.com/bitbucket/scm/PROJ/repo.git", "proj/repo"),
        ("https://bitbucket.example.com/scm/repo.git", "scm/repo"),
        ("https://bitbucket.org/~User/repo.git", "user/repo"),
        ("ssh://git@bitbucket.example.com:7999/~user/repo.git", "user/repo"),
        ("file:///srv/git/repo.git", "git/repo"),
    ],
)
def test_normalize_repository_from_remote(remote, expected):
    assert normalize_repository(remote, "/ignored/cwd") == expected


def _repository_vectors():
    path = Path(__file__).parent / "fixtures" / "repository_normalization_vectors.json"
    for vector in json.loads(path.read_text(encoding="utf-8"))["vectors"]:
        marks = [pytest.mark.xfail(strict=True, reason=vector["xfail"])] if "xfail" in vector else []
        yield pytest.param(vector["input"], vector["expected"], marks=marks, id=vector["input"] or "<empty>")


@pytest.mark.parametrize("remote, expected", _repository_vectors())
def test_normalize_repository_matches_cli_golden_vectors(remote, expected):
    assert normalize_repository(remote, "") == expected


@pytest.mark.parametrize(
    "remote",
    [
        "https://user:token@host",  # credential-bearing scheme URL with no repository path
        "user:token@host",  # bare userinfo, no scheme and no path
        "https://github.com/~",  # a lone "~" is no repository
        "https://org.visualstudio.com/DefaultCollection/_git",  # nothing left after stripping
    ],
)
def test_unparseable_remote_falls_back_to_cwd(remote):
    assert normalize_repository(remote, "/Users/x/projects/app") == "projects/app"


def test_unparseable_remote_without_cwd_is_unknown():
    assert normalize_repository("https://user:token@host", "") == "unknown"


@pytest.mark.parametrize(
    "parsed, expected",
    [
        ("owner/repo", "owner/repo"),
        ("Owner/Repo", "owner/repo"),
        ("  owner/repo\n", "owner/repo"),
        ("epm-cdme/platform/backend/codemie", "epm-cdme/platform/backend/codemie"),
        ("Group/Sub/Sub2/Repo", "group/sub/sub2/repo"),
        ("org/project/_git/repo", "org/project/_git/repo"),
        ("single", "single"),
    ],
)
def test_parsed_id_passes_through_with_all_segments(parsed, expected):
    assert normalize_repository(parsed, "/ignored/cwd") == expected


def test_parsed_id_with_credentials_is_not_kept():
    assert normalize_repository("token@github.com", "/Users/x/projects/app") == "projects/app"


@pytest.mark.parametrize(
    "cwd, expected",
    [
        ("/Users/x/projects/app", "projects/app"),
        ("/Users/x/Projects/App/", "projects/app"),
        ("C:\\Users\\x\\proj\\app", "proj/app"),
        ("/app", "app"),
        ("solo", "solo"),
        ("/", "unknown"),
        ("C:\\", "unknown"),
        (
            "/Users/x/Library/Application Support/Claude-3p/local-agent-mode-sessions/s/u/"
            "local_2d5f3a0f-6a50-4778-ac55-9ffbca0446da/outputs",
            "cowork",
        ),
        ("/tmp/local_2D5F3A0F-6A50-4778-AC55-9FFBCA0446DA", "cowork"),
        ("/home/u/local_2d5f3a0f/work", "local_2d5f3a0f/work"),
    ],
)
def test_normalize_repository_from_cwd(cwd, expected):
    assert normalize_repository("", cwd) == expected


def test_normalize_repository_empty_is_unknown():
    assert normalize_repository("", "") == "unknown"
    assert normalize_repository("  ", "  ") == "unknown"


def test_normalize_repository_trims_remote():
    assert normalize_repository("  git@github.com:o/r.git\n", "") == "o/r"


def test_aggregate_pg_rows_sums_per_person_and_repository():
    rows = [
        ("Owner@X.com", "git@gitlab.example:Owner/Repo.git", "/home/u/work/Repo", 1),
        ("owner@x.com", "https://gitlab.example/owner/repo", "/other/cwd", 3),
        ("owner@x.com", "", "/Users/x/projects/app", 2),
        ("", "git@gitlab.example:a/b.git", "", 5),
        ("dropped@x.com", "", "", 7),
    ]
    assert aggregate_pg_rows(rows) == [
        ["owner@x.com", "owner/repo", 4],
        ["owner@x.com", "projects/app", 2],
        ["dropped@x.com", "unknown", 7],
    ]


def _es(uid, repo, sessions, framework, mcp_any=0, mcp_work=0):
    return [uid, repo, sessions, mcp_any, mcp_work, framework]


def test_merge_framework_pg_fills_zero_es():
    es = [_es("u1", "o/r", 3, 0, 1, 2)]
    pg = [["a@x.com", "o/r", 1]]
    assert merge_framework(es, pg, {"u1": "a@x.com"}) == [["u1", "o/r", 3, 1, 2, 1]]


def test_merge_framework_clamps_to_sessions():
    es = [_es("u1", "o/r", 2, 0)]
    pg = [["a@x.com", "o/r", 7]]
    assert merge_framework(es, pg, {"u1": "a@x.com"})[0][5] == 2


def test_merge_framework_keeps_larger_es():
    es = [_es("u1", "o/r", 5, 4)]
    pg = [["a@x.com", "o/r", 1]]
    assert merge_framework(es, pg, {"u1": "a@x.com"})[0][5] == 4


def test_merge_framework_unmatched_rows_unchanged():
    es = [_es("u1", "o/r", 3, 0), _es("u2", "o/r", 3, 0), _es("u3", "o/other", 3, 0)]
    pg = [["a@x.com", "o/r", 2]]
    out = merge_framework(es, pg, {"u1": "b@x.com", "u3": "a@x.com"})
    assert out == es  # u1 other person, u2 no email, u3 other repository


def test_merge_framework_case_insensitive_and_does_not_mutate():
    es = [_es("u1", "Owner/Repo", 3, 0)]
    pg = [["a@x.com", "owner/repo", 2]]
    out = merge_framework(es, pg, {"u1": "A@X.com"})
    assert out == [["u1", "Owner/Repo", 3, 0, 0, 2]]
    assert es == [_es("u1", "Owner/Repo", 3, 0)]
    assert out[0] is not es[0]


def test_merge_framework_keeps_float_type_of_es():
    es = [_es("u1", "o/r", 3.0, 0.0)]
    pg = [["a@x.com", "o/r", 1]]
    fw = merge_framework(es, pg, {"u1": "a@x.com"})[0][5]
    assert fw == 1.0
    assert isinstance(fw, float)


def test_merge_framework_preserves_order_and_length():
    es = [_es("u2", "o/r", 1, 0), _es("u1", "o/r", 1, 0)]
    pg = [["a@x.com", "o/r", 1]]
    out = merge_framework(es, pg, {"u1": "a@x.com", "u2": "z@x.com"})
    assert [r[0] for r in out] == ["u2", "u1"]
    assert [r[5] for r in out] == [0, 1]
    assert all(len(r) == 6 for r in out)


def test_acceptance_postgres_framework_session_without_es_skills():
    """ES has no installed skills (framework 0); the Postgres session classified as a framework session wins."""
    pg_rows = aggregate_pg_rows([("Owner@X.com", "git@gitlab.example:Owner/Repo.git", "/home/u/work/Repo", 1)])
    es = [["uid-1", "owner/repo", 2, 0, 0, 0]]
    merged = merge_framework(es, pg_rows, {"uid-1": "owner@x.com"})
    assert merged[0][5] > 0
    assert merged == [["uid-1", "owner/repo", 2, 0, 0, 1]]


def test_merge_framework_pg_count_is_shared_by_rows_of_one_key():
    """Two ES rows of one person and repository: the Postgres count of 3 is split, not applied to both."""
    es = [_es("u1", "o/r", 4, 0), _es("u2", "O/R", 4, 0)]
    pg = [["a@x.com", "o/r", 3]]
    out = merge_framework(es, pg, {"u1": "a@x.com", "u2": "A@x.com"})
    assert [r[5] for r in out] == [3, 0]


def test_merge_framework_split_follows_remaining_room_and_own_es_count():
    es = [_es("u1", "o/r", 2, 1), _es("u2", "o/r", 5, 0)]
    pg = [["a@x.com", "o/r", 4]]
    out = merge_framework(es, pg, {"u1": "a@x.com", "u2": "a@x.com"})
    assert [r[5] for r in out] == [2, 2]  # u1 keeps its 1 and takes 1 more (room 1), u2 takes the remaining 2


def test_merge_framework_split_never_exceeds_the_sessions_of_the_key():
    es = [_es("u1", "o/r", 2, 0), _es("u2", "o/r", 1, 0)]
    pg = [["a@x.com", "o/r", 9]]
    out = merge_framework(es, pg, {"u1": "a@x.com", "u2": "a@x.com"})
    assert [r[5] for r in out] == [2, 1]


def test_merge_framework_split_keeps_larger_es_total():
    es = [_es("u1", "o/r", 3, 3), _es("u2", "o/r", 3, 1)]
    pg = [["a@x.com", "o/r", 2]]
    out = merge_framework(es, pg, {"u1": "a@x.com", "u2": "a@x.com"})
    assert [r[5] for r in out] == [3, 1]


def test_merge_framework_other_keys_are_not_drawn_from_the_budget():
    es = [_es("u1", "o/r", 3, 0), _es("u2", "o/r", 3, 0), _es("u3", "o/other", 3, 0)]
    pg = [["a@x.com", "o/r", 4], ["a@x.com", "o/other", 1]]
    out = merge_framework(es, pg, {"u1": "a@x.com", "u2": "a@x.com", "u3": "a@x.com"})
    assert [r[5] for r in out] == [3, 1, 1]


def test_merge_framework_matches_a_repository_by_its_canonical_form_on_both_sides():
    pg = [["a@x.com", "acme/api", 2]]
    assert merge_framework([_es("u1", "Acme//Api/", 3, 0)], pg, {"u1": "a@x.com"})[0][5] == 2
    assert merge_framework([_es("u1", "acme/api", 3, 0)], [["a@x.com", "Acme//Api/", 2]], {"u1": "a@x.com"})[0][5] == 2


def test_merge_framework_ignores_a_trailing_slash_and_keeps_a_folder_distinct():
    pg = [["a@x.com", "acme/api", 2]]
    assert merge_framework([_es("u1", "acme/api/", 3, 0)], pg, {"u1": "a@x.com"})[0][5] == 2
    assert merge_framework([_es("u1", "acme/", 3, 0)], pg, {"u1": "a@x.com"})[0][5] == 0  # a folder, not acme/api


def test_merge_framework_trims_the_email():
    assert merge_framework([_es("u1", "o/r", 3, 0)], [["a@x.com", "o/r", 1]], {"u1": " A@X.com "})[0][5] == 1


def test_aggregate_pg_rows_sums_repositories_that_differ_only_by_slashes():
    rows = [("a@x.com", "Acme//Api/", "", 1), ("a@x.com", "acme/api", "", 2)]
    assert aggregate_pg_rows(rows) == [["a@x.com", "acme/api", 3]]


def _previous_merge_framework(sess_rows, pg_rows, email_by_uid):
    """The implementation before the redundant steps were removed: re-summed the Postgres rows per key, copied every
    row, and compared the repository lower-cased only."""
    pg_framework = {}
    for email, repo, framework in pg_rows:
        key = (email, repo)
        pg_framework[key] = pg_framework.get(key, 0) + framework
    merged = [list(row) for row in sess_rows]
    rows_by_key = {}
    for index, row in enumerate(merged):
        email = email_by_uid.get(row[0])
        key = (email.lower(), str(row[1]).lower()) if email else None
        if key is not None and key in pg_framework:
            rows_by_key.setdefault(key, []).append(index)
    for key, indexes in rows_by_key.items():
        rows = [merged[i] for i in indexes]
        total = min(sum(r[2] for r in rows), max(sum(r[5] for r in rows), pg_framework[key]))
        rest = total - sum(r[5] for r in rows)
        for row in rows:
            if rest <= 0:
                break
            add = min(rest, row[2] - row[5])
            if add > 0:
                row[5] = type(row[5])(row[5] + add)
                rest -= add
    return merged


@pytest.mark.parametrize("seed", range(40))
def test_merge_framework_equals_the_previous_implementation_on_random_input(seed):
    rnd = random.Random(seed)
    repos = ["o/r", "o/other", "Acme/Api", "projects/app", "unknown"]
    emails = ["a@x.com", "b@x.com", "C@x.com"]
    uids = [f"u{i}" for i in range(6)]
    email_by_uid = {u: rnd.choice(emails + [" "]) for u in uids if rnd.random() < 0.85}
    email_by_uid = {u: e for u, e in email_by_uid.items() if e.strip()}
    sess_rows = []
    for _ in range(rnd.randint(0, 9)):
        sessions = rnd.randint(0, 6)
        framework = rnd.randint(0, 8)  # may exceed the sessions: the Elasticsearch count is approximate
        number = float if rnd.random() < 0.3 else int
        sess_rows.append([rnd.choice(uids), rnd.choice(repos), number(sessions), 0, 0, number(framework)])
    pg_rows = aggregate_pg_rows(
        (rnd.choice(emails), "", rnd.choice(repos), rnd.randint(1, 9)) for _ in range(rnd.randint(0, 8))
    )
    snapshot = [list(r) for r in sess_rows]
    assert merge_framework(sess_rows, pg_rows, email_by_uid) == _previous_merge_framework(
        sess_rows, pg_rows, email_by_uid
    )
    assert sess_rows == snapshot  # the input rows are not mutated
