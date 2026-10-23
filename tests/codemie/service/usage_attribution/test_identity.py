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

"""Repository identity rules 1-7, folder-like owners, own names and Git datasource links."""

from __future__ import annotations

import pytest

from codemie.service.usage_attribution.identity import (
    IdentityRules,
    is_own_owner,
    normalize_link,
    own_name_info,
)
from tests.codemie.service.usage_attribution.builders import person, settings_with


@pytest.fixture
def rules():
    return IdentityRules(settings_with())


def resolve(rules: IdentityRules, uid: str, name: str, values: list[str], value: str) -> tuple:
    ctx = rules.person_context(name, values)
    key, kind, rule, _args = rules.resolve(uid, value, ctx)
    return key, kind, rule


class TestIgnored:
    @pytest.mark.parametrize(
        "value",
        ["", "  ", "n/a", "N/A", "unknown", "null", "-", "/", "//", "Cowork", "cowork-2026", "work/temp", "a/tmp/b", "x/scratchpad",
         "repo/tmp.abc", "Temp", "team/cowork", "Projects/Cowork/notes"],
    )  # fmt: skip
    def test_no_repository(self, rules, value):
        assert resolve(rules, "u1", "a@x.com", [value], value)[0] is None


class TestRules:
    def test_rule_7_remote_in_any_letter_case(self, rules):
        assert resolve(rules, "u1", "a@x.com", ["Acme/Core"], "Acme/Core") == ("acme/core", "remote", "remote")

    def test_rule_1_azure_named_and_generic(self, rules):
        assert resolve(rules, "u1", "a@x.com", ["_git/storefront"], "_git/storefront") == (
            "_git/storefront",
            "azure",
            "azure",
        )
        # A generic Azure name stays per person.
        assert resolve(rules, "u1", "a@x.com", ["_git/api"], "_git/api") == ("azure:u1:api", "azure", "azure_generic")

    def test_rule_4_common_folder_and_drive_letter(self, rules):
        assert resolve(rules, "u1", "a@x.com", ["projects/app"], "projects/app") == (
            "folder:u1:projects/app",
            "folder",
            "local_folder",
        )
        assert resolve(rules, "u1", "a@x.com", ["C:/ebsco"], "C:/ebsco") == (
            "folder:u1:c:/ebsco",
            "folder",
            "local_folder",
        )

    def test_single_folder_name_is_a_local_folder(self, rules):
        assert resolve(rules, "u1", "a@x.com", ["myapp"], "myapp") == ("folder:u1:myapp", "folder", "no_slash")

    def test_rule_5_own_name(self, rules):
        assert resolve(rules, "u1", "john_doe@x.com", ["john-doe/dotfiles"], "john-doe/dotfiles") == (
            "personal:u1:john-doe/dotfiles",
            "personal",
            "own_name",
        )

    def test_rule_3_subfolder_of_a_remote(self, rules):
        values = ["acme/core", "core/src"]
        assert resolve(rules, "u1", "a@x.com", values, "core/src") == ("acme/core", "remote", "subfolder")

    def test_rule_6_folder_named_like_a_remote(self, rules):
        values = ["acme/core", "projects/core"]
        assert resolve(rules, "u1", "a@x.com", values, "projects/core") == ("acme/core", "remote", "folder_like_remote")

    def test_rule_6_own_name_named_like_a_remote(self, rules):
        values = ["acme/core", "john-doe/core"]
        assert resolve(rules, "u1", "john_doe@x.com", values, "john-doe/core") == (
            "acme/core",
            "remote",
            "personal_like_remote",
        )

    def test_folder_named_like_an_azure_repository(self, rules):
        values = ["_git/storefront", "repos/storefront"]
        assert resolve(rules, "u1", "a@x.com", values, "repos/storefront") == (
            "_git/storefront",
            "azure",
            "folder_like_remote",
        )

    def test_same_folder_name_of_different_people_is_not_merged(self, rules):
        a = resolve(rules, "u1", "a@x.com", ["projects/app"], "projects/app")[0]
        b = resolve(rules, "u2", "b@x.com", ["projects/app"], "projects/app")[0]
        assert a != b

    def test_same_remote_of_different_people_is_one_repository(self, rules):
        a = resolve(rules, "u1", "a@x.com", ["acme/core"], "acme/core")[0]
        b = resolve(rules, "u2", "b@x.com", ["ACME/core"], "ACME/core")[0]
        assert a == b == "acme/core"


class TestFolderLikeOwners:
    def test_detected_from_the_data(self, rules):
        # "box" is used by 5 people with 10 different folders, none shared and never charged back.
        persons = {f"u{i}": person(f"p{i}@x.com", {f"box/f{i}a": (1,), f"box/f{i}b": (1,)}) for i in range(5)}
        rules.detect_folder_like(persons)
        assert "box" in rules.folder_like
        assert resolve(rules, "u1", "p1@x.com", ["box/f1a"], "box/f1a") == (
            "folder:u1:box/f1a",
            "folder",
            "folder_like_owner",
        )

    def test_not_detected_when_charged_back(self, rules):
        persons = {f"u{i}": person(f"p{i}@x.com", {f"box/f{i}a": (1, 1), f"box/f{i}b": (1,)}) for i in range(5)}
        rules.detect_folder_like(persons)
        assert "box" not in rules.folder_like

    def test_not_detected_when_shared(self, rules):
        # Every folder used by two people: a real owner, not a folder name.
        persons = {f"u{i}": person(f"p{i}@x.com", {f"box/r{j}": (1,) for j in range(10)}) for i in range(5)}
        rules.detect_folder_like(persons)
        assert "box" not in rules.folder_like


class TestOwnName:
    @pytest.mark.parametrize(
        "owner", ["john-smith", "johnsmith", "john_smith", "jsmith", "smithjohn", "johns", "JohnSmith"]
    )
    def test_variants(self, owner):
        assert is_own_owner(owner, own_name_info("john_smith@example.com")) is True

    @pytest.mark.parametrize("owner", ["acme", "john", "smith", "jo", ""])
    def test_not_own(self, owner):
        assert is_own_owner(owner, own_name_info("john_smith@example.com")) is False

    def test_short_surnames_need_the_full_name(self):
        """Surnames under four letters match only the whole name, so 'jdoe' is not taken for john_doe."""
        info = own_name_info("john_doe@example.com")
        assert is_own_owner("john-doe", info) is True
        assert is_own_owner("johndoe", info) is True
        assert is_own_owner("jdoe", info) is False


class TestNormalizeLink:
    @pytest.mark.parametrize(
        ("link", "expected"),
        [
            ("https://gitlab.example.com/Group/Sub/Repo.git", "sub/repo"),
            ("https://user:token@github.com/Acme/Core", "acme/core"),
            ("git@github.com:Acme/Core.git", "acme/core"),
            ("ssh://git@gitlab.example.com:2222/team/app.git", "team/app"),
            ("https://dev.azure.com/org/project/_git/Storefront", "_git/storefront"),
            ("https://org@dev.azure.com/org/project/_git/storefront?path=/", "_git/storefront"),
            ("https://bitbucket.example.com/projects/KEY/repos/name/browse", "key/name"),
            ("https://gitlab.example.com/team/app/-/tree/main/src", "team/app"),
            ("https://github.com/acme/core/blob/main/README.md", "acme/core"),
            ("https://github.com/acme", None),
            ("", None),
            (None, None),
        ],
    )
    def test_cases(self, link, expected):
        assert normalize_link(link) == expected
