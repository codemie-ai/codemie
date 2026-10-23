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

"""Repository identity (architecture.md §7.4, classification-model.md §6).

The CLI reports the repository as ``owner/repo`` parsed from the git remote without the host, ``_git/<repo>`` for
Azure DevOps HTTPS remotes, or the last two folder names when there is no remote (``C:/folder`` on Windows). Rules
apply per person, in order:

1. ``_git/<repo>`` is one repository per name; generic names stay per person.
2. Empty, ``n/a``, Cowork, temporary and scratch values are no repository.
3. ``b/c`` where ``b`` names a repository the same person uses is that repository (a subfolder).
4. A common folder name, a drive letter or a folder-like owner detected from the data makes a person's local folder.
5. An owner that is the person's own name makes a personal repository.
6. A folder or own-name value named like a remote the person uses is that remote.
7. Any other ``owner/repo`` is one repository, in any letter case.
"""

from __future__ import annotations

import fnmatch
import re
from collections import defaultdict
from typing import Any

FOLDER = "folder"
PERSONAL_KEY = "personal"
REMOTE = "remote"
AZURE = "azure"

AZURE_PREFIX = "_git/"

_DRIVE_RE = re.compile(r"[a-z]:")


def own_name_info(user_name: str | None) -> dict[str, Any]:
    local = (user_name or "").split("@")[0].lower()
    return {
        "local": local,
        "norm": re.sub(r"[^a-z0-9]", "", local),
        "tokens": [t for t in re.split(r"[^a-z0-9]+", local) if t],
    }


def is_own_owner(owner: str, info: dict[str, Any]) -> bool:
    """The owner looks like the person's own name: john_doe -> john-doe, johndoe, jdoe, doejohn, johnd."""
    o = owner.lower()
    on = re.sub(r"[^a-z0-9]", "", o)
    if not on or not info["norm"]:
        return False
    if o == info["local"] or on == info["norm"]:
        return True
    tokens = info["tokens"]
    if len(tokens) >= 2:
        first, last = tokens[0], tokens[-1]
        if len(last) >= 4 and last in on and (on.startswith(first[:1]) or first in on):
            return True
        if len(first) >= 4 and on.startswith(first) and on.endswith(last[:1]) and len(on) <= len(first) + 2:
            return True
    return False


def canonical_value(value: str) -> str:
    """A repository value trimmed and in lower case, without empty path segments: ``acme//api/`` and ``acme/api``
    are one repository, and ``acme/`` is a folder. A leading slash (an absolute path) is kept."""
    lv = value.strip().lower()
    if lv == AZURE_PREFIX or ("//" not in lv and not (lv.endswith("/") and len(lv) > 1)):
        return lv
    body = "/".join(part for part in lv.split("/") if part)
    if not body:
        return lv
    return ("/" if lv.startswith("/") else "") + body


def glob_any(value: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(value, p.lower()) for p in patterns)


class IdentityRules:
    """Resolves a person's repository values to repository keys."""

    def __init__(self, settings: dict[str, Any]):
        self.local_owners = {x.lower() for x in settings["localFolderOwners"]}
        self.generic_azure = {x.lower() for x in settings["genericAzureNames"]}
        self.ignored_values = {x.lower() for x in settings["ignoredValues"]}
        self.ignored_prefixes = [x.lower() for x in settings["ignoredValuePrefixes"] if x]
        self.ignored_segments = {x.lower() for x in settings["ignoredSegments"]}
        self.ignored_segment_prefixes = [x.lower() for x in settings["ignoredSegmentPrefixes"] if x]
        self.min_people = settings["folderOwnerMinPeople"]
        self.min_repos = settings["folderOwnerMinRepos"]
        self.max_shared_share = settings["folderOwnerMaxSharedShare"]
        self.folder_like: dict[str, dict[str, int]] = {}

    # ------------------------------------------------------------------ simple tests
    def is_local_owner(self, owner: str | None) -> bool:
        """A common folder name, or a Windows drive letter: on Windows the CLI reports ``C:/folder``."""
        return bool(owner) and (owner in self.local_owners or bool(_DRIVE_RE.fullmatch(owner)))

    def is_ignored(self, lv: str) -> bool:
        if lv in self.ignored_values or any(lv.startswith(p) for p in self.ignored_prefixes):
            return True
        segments = [s for s in lv.split("/") if s]
        if not segments:
            return True
        if any(s in self.ignored_segments for s in segments):
            return True
        return any(s.startswith(p) for s in segments for p in self.ignored_segment_prefixes)

    def groupable_owner(self, owner: str | None) -> bool:
        return bool(owner) and not self.is_local_owner(owner) and owner not in self.folder_like

    # ------------------------------------------------------------------ folder-like owners (rule 4)
    def detect_folder_like(self, persons: dict[str, dict]) -> None:
        """Owners that many people use as a folder name: 5+ people, 10+ folders, at most 10% shared, never charged
        back."""
        owner_values, owner_cb = self._collect_owner_usage(persons)
        self.folder_like = {}
        for owner, vals in owner_values.items():
            counts = self._folder_like_counts(vals)
            if self._is_folder_like(counts, owner_cb[owner]):
                self.folder_like[owner] = counts

    def _collect_owner_usage(self, persons: dict[str, dict]) -> tuple[dict[str, dict[str, set]], dict[str, float]]:
        """Per-owner repository values each person uses, and the owner's total charge-back money, for non-local,
        non-Azure, non-ignored owner/repo values."""
        owner_values: dict[str, dict[str, set]] = defaultdict(dict)
        owner_cb: dict[str, float] = defaultdict(float)
        for uid, p in persons.items():
            for value, pair in p["pairs"].items():
                owner, lv = self._groupable_owner_value(value)
                if owner is None:
                    continue
                owner_values[owner].setdefault(lv, set()).add(uid)
                owner_cb[owner] += pair[1]
        return owner_values, owner_cb

    def _groupable_owner_value(self, value: str) -> tuple[str | None, str]:
        """The owner and canonical value of a repository value eligible for folder-like detection, or (None, "")."""
        lv = canonical_value(value)
        if "/" not in lv or lv.startswith(AZURE_PREFIX) or self.is_ignored(lv):
            return None, ""
        owner = lv.split("/", 1)[0]
        if self.is_local_owner(owner):
            return None, ""
        return owner, lv

    @staticmethod
    def _folder_like_counts(vals: dict[str, set]) -> dict[str, int]:
        people = set().union(*vals.values())
        shared = sum(1 for users in vals.values() if len(users) >= 2)
        return {"values": len(vals), "people": len(people), "shared": shared}

    def _is_folder_like(self, counts: dict[str, int], charged_back: float) -> bool:
        return (
            counts["people"] >= self.min_people
            and counts["values"] >= self.min_repos
            and counts["shared"] / float(counts["values"]) <= self.max_shared_share
            and charged_back <= 0
        )

    # ------------------------------------------------------------------ per person
    def person_context(self, user_name: str | None, values: list[str]) -> dict[str, Any]:
        """The person's remote repositories, and their names, for rules 3 and 6 (values sorted by spend)."""
        info = own_name_info(user_name)
        candidates, azure_names = self._collect_candidates(values, info)
        remotes = self._filter_remotes(candidates, azure_names)
        by_name = self._build_by_name(remotes, azure_names)
        return {"info": info, "remotes": set(remotes), "by_name": by_name}

    def _collect_candidates(self, values: list[str], info: dict[str, Any]) -> tuple[list[str], list[str]]:
        """Remote owner/repo candidates and Azure repo names, in order, skipping ignored, local, folder-like and
        own-name owners."""
        candidates: list[str] = []
        azure_names: list[str] = []
        for value in values:
            lv = canonical_value(value)
            if self.is_ignored(lv):
                continue
            if lv.startswith(AZURE_PREFIX):
                self._add_azure_name(lv, azure_names)
                continue
            if "/" not in lv:
                continue
            owner = lv.split("/", 1)[0]
            if self._skip_candidate_owner(owner, info):
                continue
            if lv not in candidates:
                candidates.append(lv)
        return candidates, azure_names

    def _add_azure_name(self, lv: str, azure_names: list[str]) -> None:
        name = lv[len(AZURE_PREFIX) :]
        if name and name not in self.generic_azure:
            azure_names.append(name)

    def _skip_candidate_owner(self, owner: str, info: dict[str, Any]) -> bool:
        return self.is_local_owner(owner) or owner in self.folder_like or is_own_owner(owner, info)

    @staticmethod
    def _is_subfolder(lv: str, owner: str, names: set[str], candidates: list[str]) -> bool:
        """The candidate's owner is the name of another candidate repository the person uses (rule 3)."""
        return owner in names and any(x != lv and x.split("/", 1)[1] == owner for x in candidates)

    def _filter_remotes(self, candidates: list[str], azure_names: list[str]) -> list[str]:
        """Candidates that are remotes in their own right: not an Azure repo name, and not a subfolder of another
        candidate (rule 3)."""
        names = {x.split("/", 1)[1] for x in candidates}
        remotes = []
        for lv in candidates:
            owner = lv.split("/", 1)[0]
            if owner in azure_names or self._is_subfolder(lv, owner, names, candidates):
                continue
            remotes.append(lv)
        return remotes

    @staticmethod
    def _build_by_name(remotes: list[str], azure_names: list[str]) -> dict[str, str]:
        by_name: dict[str, str] = {}
        for lv in remotes:  # the first remote with a name wins: values come sorted by spend
            by_name.setdefault(lv.split("/", 1)[1], lv)
        for name in azure_names:
            by_name.setdefault(name, AZURE_PREFIX + name)
        return by_name

    def resolve(self, uid: str, value: str, ctx: dict[str, Any]) -> tuple[str | None, str, str, dict[str, Any]]:
        """(key, kind, rule, args) for one repository value of one person; key is None for no repository."""
        lv = canonical_value(value)
        if self.is_ignored(lv):
            return None, "ignored", "ignored", {}
        if lv.startswith(AZURE_PREFIX):
            return self._resolve_azure(uid, lv)
        if "/" not in lv:
            return f"folder:{uid}:{lv}", FOLDER, "no_slash", {}
        return self._resolve_owned(uid, lv, ctx)

    def _resolve_azure(self, uid: str, lv: str) -> tuple[str | None, str, str, dict[str, Any]]:
        name = lv[len(AZURE_PREFIX) :] or "(empty)"
        if name in self.generic_azure:
            return f"azure:{uid}:{name}", AZURE, "azure_generic", {}
        return AZURE_PREFIX + name, AZURE, "azure", {}

    def _resolve_owned(self, uid: str, lv: str, ctx: dict[str, Any]) -> tuple[str | None, str, str, dict[str, Any]]:
        owner, name = lv.split("/", 1)
        by_name = ctx["by_name"]
        if lv in ctx["remotes"]:
            return lv, REMOTE, "remote", {}
        if owner in by_name and by_name[owner] != lv:
            return self._merged(by_name[owner], "subfolder", part=owner)
        if self.is_local_owner(owner) or owner in self.folder_like:
            return self._resolve_folder_like(uid, lv, owner, name, by_name)
        if is_own_owner(owner, ctx["info"]):
            return self._resolve_own_name(uid, lv, owner, name, by_name)
        return lv, REMOTE, "remote", {}

    def _resolve_folder_like(
        self, uid: str, lv: str, owner: str, name: str, by_name: dict[str, str]
    ) -> tuple[str | None, str, str, dict[str, Any]]:
        if name in by_name:
            return self._merged(by_name[name], "folder_like_remote", owner=owner)
        rule = "local_folder" if self.is_local_owner(owner) else "folder_like_owner"
        return f"folder:{uid}:{lv}", FOLDER, rule, {"owner": owner}

    @staticmethod
    def _resolve_own_name(
        uid: str, lv: str, owner: str, name: str, by_name: dict[str, str]
    ) -> tuple[str | None, str, str, dict[str, Any]]:
        if name in by_name:
            return IdentityRules._merged(by_name[name], "personal_like_remote", owner=owner)
        return f"personal:{uid}:{lv}", PERSONAL_KEY, "own_name", {"owner": owner}

    @staticmethod
    def _merged(target: str, rule: str, **args: Any) -> tuple[str, str, str, dict[str, Any]]:
        return target, (AZURE if target.startswith(AZURE_PREFIX) else REMOTE), rule, dict(args, target=target)


def normalize_link(link: str | None) -> str | None:
    """A Git datasource URL as the CLI reports the repository: owner/repo in lowercase, or _git/<repo> for Azure.

    Credentials, host and port are dropped; ``/-/``, ``/tree/``, ``/blob/`` and ``.git`` are stripped; GitLab subgroups
    keep the last two segments, as the CLI does; Bitbucket Server ``projects/KEY/repos/name`` keeps ``KEY/name``.
    """
    s = (link or "").strip().lower()
    if not s:
        return None
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s)  # scheme
    s = re.sub(r"^[^@/]*@", "", s)  # credentials, or the ssh user
    s = s.split("?", 1)[0].split("#", 1)[0]
    head, _, rest = s.partition("/")
    if ":" in head:
        _, _, after = head.partition(":")
        if after and not after.isdigit():  # scp-like ssh: host:owner/repo
            rest = after + ("/" + rest if rest else "")
    rest = re.split(r"/-/|/tree/|/blob/|/src/|/browse|/commits?/", "/" + rest)[0].strip("/")
    rest = re.sub(r"\.git$", "", rest)
    parts = [p for p in rest.split("/") if p]
    if "_git" in parts:
        i = parts.index("_git")
        return AZURE_PREFIX + parts[i + 1] if i + 1 < len(parts) else None
    if "repos" in parts and 1 <= parts.index("repos") < len(parts) - 1:  # Bitbucket Server
        i = parts.index("repos")
        return parts[i - 1] + "/" + parts[i + 1]
    if len(parts) < 2:
        return None
    return parts[-2] + "/" + parts[-1]
