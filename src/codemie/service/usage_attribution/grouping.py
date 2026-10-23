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

"""Repository groups (architecture.md §7.5): the probable project behind several repositories.

Two repositories are linked when at least ``groupMinSignals`` (2) of these agree: the same owner, the same ticket key
prefix, people in common, the same shared CodeMie project, or Git datasources of the same shared project / an owner
rule. Components are found with union-find; a component larger than ``maxGroupSize`` is split by owner.
"""

from __future__ import annotations

import fnmatch
import hashlib
from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Any

from codemie.service.usage_attribution.identity import AZURE, REMOTE

if TYPE_CHECKING:
    from codemie.service.usage_attribution.pipeline import Analysis

# Pairs counted per person for the people-in-common signal: a person with many repositories would otherwise add
# a quadratic number of pairs.
_PEOPLE_PAIR_KEYS = 30
# Ceiling on the repository pairs the shared signals may add (a month of usage adds far fewer). The smallest buckets
# are paired first; a bucket that no longer fits is skipped, so no setting can make grouping quadratic in memory.
_MAX_SIGNAL_PAIRS = 1_000_000


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def short_hash(text: str, n: int = 10) -> str:
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()[:n]


def owner_rule(an: Analysis, k: dict[str, Any]) -> str | None:
    """The shared project an owner rule (``ownerProjectRules``) maps the repository's owner to."""
    if not k.get("owner"):
        return None
    for rule in an.S.get("ownerProjectRules") or []:
        if fnmatch.fnmatchcase(k["owner"], rule["owner"].lower().split("/")[0]):
            return rule["project"]
    return None


def _add_owner_member(an: Analysis, k: dict[str, Any], members_by: dict[str, dict[str, list[str]]]) -> None:
    if k["kind"] == REMOTE and an.identity.groupable_owner(k["owner"]):
        members_by["owner"][k["owner"]].append(k["key"])


def _add_ticket_members(k: dict[str, Any], members_by: dict[str, dict[str, list[str]]]) -> None:
    for prefix in k["tickets"]:
        members_by["ticket"][prefix].append(k["key"])


def _add_project_members(
    an: Analysis, k: dict[str, Any], min_share: float, members_by: dict[str, dict[str, list[str]]]
) -> None:
    total = sum(k["projects"].values()) or 1.0
    for project, amount in k["projects"].items():
        if not an.is_personal_project(project) and amount / total >= min_share:
            members_by["project"][project].append(k["key"])


def _add_rule_member(an: Analysis, k: dict[str, Any], members_by: dict[str, dict[str, list[str]]]) -> None:
    rule_project = owner_rule(an, k)
    if rule_project:
        members_by["rule"][rule_project].append(k["key"])


def _add_datasource_members(an: Analysis, k: dict[str, Any], members_by: dict[str, dict[str, list[str]]]) -> None:
    if k["kind"] not in (REMOTE, AZURE):
        return
    for project in an.datasource_projects.get(k["key"], ()):
        if not broad_indexer(an, project):
            members_by["datasource"][project].append(k["key"])


def _signal_members(an: Analysis, keys: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    """Repositories that share each owner, ticket prefix, shared project, owner rule and datasource project."""
    min_share = an.S["minProjectShareForGrouping"]
    members_by: dict[str, dict[str, list[str]]] = {
        "owner": defaultdict(list),
        "ticket": defaultdict(list),
        "project": defaultdict(list),
        "rule": defaultdict(list),
        "datasource": defaultdict(list),
    }
    for k in keys:
        _add_owner_member(an, k, members_by)
        _add_ticket_members(k, members_by)
        _add_project_members(an, k, min_share, members_by)
        _add_rule_member(an, k, members_by)
        _add_datasource_members(an, k, members_by)
    return members_by


def _signal_buckets(an: Analysis, keys: list[dict[str, Any]]) -> list[tuple[int, str, str, list[str]]]:
    """(size, type, name, members) of every signal shared by 2 to its limit of repositories; larger ones are skipped."""
    settings = an.S
    limits = {
        "owner": settings["maxOwnerRepositoriesForGrouping"],
        "ticket": settings["maxTicketPrefixRepositories"],
        "project": settings["maxProjectRepositoriesForGrouping"],
        # An owner rule maps owners, so its bucket has the owner limit (a rule such as "a*" can match thousands).
        "rule": settings["maxOwnerRepositoriesForGrouping"],
        "datasource": settings["maxProjectRepositoriesForGrouping"],
    }
    buckets: list[tuple[int, str, str, list[str]]] = []
    for typ, by_name in _signal_members(an, keys).items():
        for name, members in by_name.items():
            unique = sorted(set(members))
            if len(unique) < 2:
                continue
            if len(unique) > limits[typ]:
                an.skipped_signal_buckets[typ] += 1
                continue
            buckets.append((len(unique), typ, name, unique))
    return buckets


def _people_in_common(keys: list[dict[str, Any]]) -> Counter:
    """Pairs of repositories and how many people worked on both (each person's first repositories only)."""
    person_keys: dict[str, list[str]] = defaultdict(list)
    for k in keys:
        for uid, amount in k["persons"].items():
            if amount > 0:
                person_keys[uid].append(k["key"])
    common: Counter = Counter()
    for ks in person_keys.values():
        unique = sorted(set(ks))[:_PEOPLE_PAIR_KEYS]
        for i, a in enumerate(unique):
            for b in unique[i + 1 :]:
                common[(a, b)] += 1
    return common


def _pair_signals(an: Analysis, keys: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """The signals each pair of repositories shares. Signals shared by too many repositories are skipped."""
    pair_sig: dict[tuple[str, str], dict[str, Any]] = defaultdict(dict)
    an.skipped_signal_buckets = Counter()
    pairs_left = _MAX_SIGNAL_PAIRS
    for n, typ, name, unique in sorted(_signal_buckets(an, keys), key=lambda b: b[:3]):
        cost = n * (n - 1) // 2
        if cost > pairs_left:
            an.skipped_signal_buckets[typ] += 1
            continue
        pairs_left -= cost
        for i, a in enumerate(unique):
            for b in unique[i + 1 :]:
                pair_sig[(a, b)].setdefault(typ, set()).add(name)
    for pair, n in _people_in_common(keys).items():
        if n >= 2:
            pair_sig[pair]["people"] = n
    return pair_sig


def _components(an: Analysis) -> list[tuple[list[str], bool]]:
    """Linked repositories, with components larger than ``maxGroupSize`` split by owner (flagged when still too big)."""
    max_size = an.S["maxGroupSize"]
    uf = UnionFind()
    for a, b in an.edges:
        uf.union(a, b)
    components: dict[str, list[str]] = defaultdict(list)
    for key in an.K:
        components[uf.find(key)].append(key)
    groups: list[tuple[list[str], bool]] = []
    for members in components.values():
        if len(members) <= max_size:
            groups.append((members, False))
            continue
        an.stats["components_split_by_owner"] += 1
        by_owner: dict[str, list[str]] = defaultdict(list)
        for m in members:
            by_owner[an.K[m]["owner"] or ("~" + m)].append(m)
        for sub in by_owner.values():
            groups.append((sub, len(sub) > max_size))
    return groups


def _new_group_shell(gid: str, members: list[str], anchor: str, oversized: bool) -> dict[str, Any]:
    return {
        "id": gid,
        "members": members,
        "anchor": anchor,
        "oversized": oversized,
        "persons": Counter(),
        "signals": {
            "owner": Counter(),
            "ticket": Counter(),
            "project": Counter(),
            "rule": Counter(),
            "datasource": Counter(),
        },
        "people_common": 0,
        "edges": 0,
    }


def _apply_member_edge(g: dict[str, Any], sig: dict[str, Any]) -> None:
    g["edges"] += 1
    for typ, names in sig.items():
        if typ == "people":
            g["people_common"] = max(g["people_common"], names)
        else:
            for name in names:
                g["signals"][typ][name] += 1


def _collect_group_edges(g: dict[str, Any], members: list[str], edges_by_member: dict) -> None:
    if len(members) <= 1:
        return
    member_set = set(members)
    for m in members:
        for (_a, b), sig in edges_by_member.get(m, ()):
            if b in member_set:
                _apply_member_edge(g, sig)


def _group_top_owner(an: Analysis, members: list[str]) -> str | None:
    owners = Counter(an.K[m]["owner"] for m in members if an.K[m]["owner"])
    top_owner = owners.most_common(1)[0] if owners else None
    return top_owner[0] if top_owner and top_owner[1] >= 2 else None


def _new_group(an: Analysis, members: list[str], oversized: bool, edges_by_member: dict) -> dict[str, Any]:
    members.sort(key=lambda m: (-an.K[m]["billed"], m))
    anchor = members[0]
    gid = "g-" + short_hash(anchor, 10)
    g = _new_group_shell(gid, members, anchor, oversized)
    for m in members:
        an.K[m]["group"] = gid
        g["persons"].update(an.K[m]["persons"])
    _collect_group_edges(g, members, edges_by_member)
    g["owner"] = _group_top_owner(an, members)
    g["people_n"] = sum(1 for a in g["persons"].values() if a > 0)
    return g


def build_groups(an: Analysis) -> None:
    keys = [k for k in an.K.values() if not k["excluded"] and k["billed"] > 0]
    min_signals = an.S["groupMinSignals"]
    an.edges = {pair: sig for pair, sig in _pair_signals(an, keys).items() if len(sig) >= min_signals}
    # Edges by member, so each group collects its own edges without scanning all of them.
    edges_by_member: dict[str, list[tuple[tuple[str, str], dict]]] = defaultdict(list)
    for pair, sig in an.edges.items():
        edges_by_member[pair[0]].append((pair, sig))
    an.groups = {}
    for members, oversized in _components(an):
        g = _new_group(an, members, oversized, edges_by_member)
        an.groups[g["id"]] = g


# A shared project whose members are more than this share of the period's CLI people is too broad for membership to
# say anything about a group: the platform team's own project, a project everyone is added to.
MEMBERSHIP_MAX_SHARE = 0.10


def broad_indexer(an: Analysis, project: str) -> bool:
    """A project that registers more repositories as Git datasources than one project groups
    (``maxProjectRepositoriesForGrouping``): it indexes repositories, a platform or knowledge project, it does not own
    them. Its datasources neither link repositories nor relate a group to it."""
    return an.datasource_count.get(project, 0) > an.S["maxProjectRepositoriesForGrouping"]


Related = tuple[str | None, str, str]


def _by_budget(an: Analysis, keys: list[dict[str, Any]], billed: float, enough: float) -> Related | None:
    """The shared project with a budget that most of the group's charged-back CLI money went to, when it is enough."""
    cb: Counter = Counter()
    for k in keys:
        for project, amount in k["projects_cb"].items():
            if amount > 0 and not an.is_personal_project(project):
                cb[project] += amount
    if not cb:
        return None
    project, amount = cb.most_common(1)[0]
    if billed <= 0 or amount < enough:
        return None
    return (
        project,
        "budgeted_reuse",
        f"A shared project with a budget, already used on these repositories (${amount:,.0f} charged back, "
        f"{amount / billed:.0%} of their CLI spend)",
    )


def _by_datasource(an: Analysis, keys: list[dict[str, Any]], billed: float, enough: float) -> Related | None:
    """The shared project, not a broad indexer, whose Git datasources hold most of the group's CLI money."""
    datasource: Counter = Counter()
    for k in keys:
        if k["kind"] in (REMOTE, AZURE):
            for project in an.datasource_projects.get(k["key"], ()):
                if not broad_indexer(an, project):
                    datasource[project] += k["billed"]
    if not datasource:
        return None
    project, amount = datasource.most_common(1)[0]
    if billed <= 0 or amount < enough:
        return None
    return project, "datasource", "Its repositories are registered as Git datasources of this shared project"


def _by_membership(an: Analysis, g: dict[str, Any] | None) -> Related | None:
    """The shared project at least half of the group's people (and at least 2) belong to, among projects at most
    ``MEMBERSHIP_MAX_SHARE`` of the period's CLI people belong to."""
    people = [u for u, a in (g["persons"] if g else Counter()).items() if a > 0]
    if not an.members or len(people) < 2:
        return None
    belongs = Counter(p for u in people for p in an.member_of.get(u, ()))
    cli_people = len(an.persons) or 1
    for project, n in belongs.most_common():
        if n < 2 or n * 2 < len(people):
            return None
        share = len(an.members[project] & an.persons.keys()) / cli_people
        if share <= MEMBERSHIP_MAX_SHARE:
            return (
                project,
                "membership",
                f"{n} of the group's {len(people)} people are members of this shared project "
                f"({share:.0%} of the period's CLI people are)",
            )
    return None


def related_project(an: Analysis, keys: list[dict[str, Any]], g: dict[str, Any] | None = None) -> Related:
    """architecture.md §7.5: budgeted reuse, then Git datasource, then membership, then owner rule.

    Each needs evidence specific to the group: money charged back to the project, or repositories it registers, of at
    least ``minProjectShareForGrouping`` of the group's CLI spend (a stray session is not the project's work); a
    datasource project that is not a broad indexer; and membership of a project at most ``MEMBERSHIP_MAX_SHARE`` of
    the period's CLI people belong to.

    Returns (project, reason id, explanation).
    """
    billed = sum(k["billed"] for k in keys)
    enough = an.S["minProjectShareForGrouping"] * billed
    found = _by_budget(an, keys, billed, enough) or _by_datasource(an, keys, billed, enough) or _by_membership(an, g)
    if found:
        return found
    for k in keys:
        rule = owner_rule(an, k)
        if rule:
            return rule, "owner_rule", f"Owner rule {k['owner']} → {rule}"
    return (
        None,
        "none",
        "No shared project with a budget uses these repositories, none has them as Git datasources, and their "
        "people share no project",
    )
