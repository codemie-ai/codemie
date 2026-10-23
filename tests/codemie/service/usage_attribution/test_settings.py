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

"""Settings of the Usage Attribution analysis: defaults, override validation, hashes, tiers and roles."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

from codemie.service.usage_attribution.settings import (
    SCHEMA,
    SCHEMA_BY_KEY,
    SCHEMA_VERSION,
    TIER_ANALYSIS,
    TIER_FETCH,
    TIER_VIEW,
    SettingsConfigError,
    SettingsError,
    build_defaults,
    build_effective,
    decode_overrides,
    encode_overrides,
    normalize_rules,
    schema_payload,
    user_can_tune,
    validate_overrides,
)


@pytest.fixture
def defaults():
    return build_defaults({})


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


class TestDefaults:
    def test_code_defaults_are_valid_and_complete(self, defaults):
        assert defaults.values["minimumConfidence"] == 50
        assert defaults.values["contradictedProductionMinConfidence"] == 60
        assert defaults.values["likelyProductionMinConfidence"] == 40
        assert defaults.values["personHistory"] == {
            "enabled": True,
            "minProductionShare": 0.7,
            "minClassifiedSpend": 20,
        }
        assert defaults.values["allocateByProject"] == {
            "enabled": True,
            "minClassifiedShare": 0.3,
            "minClassifiedSpend": 50,
        }
        assert defaults.values["confidenceConstant"] == 6
        assert defaults.values["allocateByPerson"] == {
            "enabled": True,
            "minClassifiedShare": 0.1,
            "minClassifiedSpend": 5,
        }
        assert defaults.values["tuning"]["roles"] == ["admin", "maintainer"]
        assert len(SCHEMA) == 38
        assert len(defaults.defaults_hash) == 32
        assert defaults.values["workerMemoryLimitMb"] == 3072
        assert defaults.values["tuning"]["maxComputationsPerHour"] == 30

    def test_configuration_overrides_code_defaults(self):
        d = build_defaults(
            {"enabled": True, "name": "x", "minimumConfidence": 70, "thresholds": {"activeDaysProduction": 7}}
        )
        assert d.values["minimumConfidence"] == 70
        assert d.values["thresholds"]["activeDaysProduction"] == 7
        # Nested dicts merge: the other thresholds keep their defaults.
        assert d.values["thresholds"]["minToolSessions"] == 3
        # Component metadata never becomes an analysis setting.
        assert "enabled" not in d.values and "name" not in d.values

    def test_configuration_change_changes_defaults_hash(self, defaults):
        assert build_defaults({"minimumConfidence": 70}).defaults_hash != defaults.defaults_hash

    @pytest.mark.parametrize(
        "configured",
        [
            {"minimumConfidence": 10},  # below 30
            {"repositoriesPerPerson": 100},  # above 25
            {"sustainedWork": {"enabled": "yes"}},
            {"fallbackFieldSince": "yesterday"},
            {"fallbackFieldSince": "2026-10-09"},
            {"fallbackFieldSince": "2026-13-09T12:00:00Z"},
            {"workerMemoryLimitMb": 100},
            {"tuning": {"maxComputationsPerHour": 0}},
            {"weights": {"where.datasource": ["unknown", 8]}},
            {"weights": {"elsewhere.x": ["production", 8]}},
            {"tuning": {"roles": ["everyone"]}},
            {"maxRangeDays": 0},
            {"namePatterns": {"learning": "(unclosed"}},
        ],
    )
    def test_invalid_configuration_fails_at_load(self, configured):
        with pytest.raises(SettingsConfigError):
            build_defaults(configured)

    def test_owner_rules_as_mapping_are_accepted(self):
        d = build_defaults({"ownerProjectRules": {"payments/*": "northwind-payments"}})
        assert d.values["ownerProjectRules"] == [{"owner": "payments", "project": "northwind-payments"}]


class TestDecodeOverrides:
    def test_empty(self):
        assert decode_overrides(None, 4096) == {}
        assert decode_overrides("", 4096) == {}

    def test_round_trip(self):
        raw = encode_overrides({"minimumConfidence": 50, "allocateByPerson.enabled": False})
        assert decode_overrides(raw, 4096) == {"allocateByPerson.enabled": False, "minimumConfidence": 50}

    def test_padding_is_optional(self):
        raw = base64.urlsafe_b64encode(b'{"namesCap": 5}').decode()
        assert decode_overrides(raw, 4096) == {"namesCap": 5}
        assert decode_overrides(raw.rstrip("="), 4096) == {"namesCap": 5}

    def test_larger_than_the_limit_is_rejected(self):
        raw = _b64({"localFolderOwners": ["x" * 90] * 50})
        assert len(raw) > 4096
        with pytest.raises(SettingsError, match="larger than 4 KB"):
            decode_overrides(raw, 4096)

    @pytest.mark.parametrize("raw", ["not base64!", "%%%", _b64([1, 2]), _b64("text"), "eyJh"])
    def test_malformed_payloads_are_rejected(self, raw):
        with pytest.raises(SettingsError):
            decode_overrides(raw, 4096)


class TestValidateOverrides:
    def test_unknown_key_names_the_setting(self, defaults):
        with pytest.raises(SettingsError) as exc:
            validate_overrides({"weights.how.code_changes": ["production", 99]}, defaults)
        assert exc.value.key == "weights.how.code_changes"

    def test_signal_points_are_not_tunable(self, defaults):
        with pytest.raises(SettingsError):
            validate_overrides({"weights": {}}, defaults)

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("minimumConfidence", 29),
            ("minimumConfidence", 96),
            ("minimumConfidence", 60.5),
            ("minimumConfidence", "60"),
            ("minimumConfidence", True),
            ("confidenceConstant", 21),
            ("repositoriesPerPerson", 26),
            ("elasticsearchConcurrency", 13),
            ("partitionBucketShare", 0.9),
            ("thresholds.readOnlyShareLearning", 0.4),
            ("sharedProjectThresholdPer30Days", -1),
            ("sharedProjectThresholdPer30Days", float("inf")),
            ("allocateByPerson.enabled", "true"),
            ("localFolderOwners", "projects"),
            ("localFolderOwners", ["ok", ""]),
            ("localFolderOwners", ["x"] * 51),
            ("localFolderOwners", ["x" * 101]),
            ("localFolderOwners", [1]),
            ("ownerProjectRules", [["payments", "northwind"]]),
            ("ownerProjectRules", [{"owner": "a/b", "project": "p"}]),
            ("ownerProjectRules", [{"owner": "a", "project": ""}]),
            ("ownerProjectRules", [{"owner": "a", "project": "p", "x": 1}]),
            # An owner of wildcards only (or one letter) would pair thousands of repositories.
            ("ownerProjectRules", [{"owner": "*", "project": "p"}]),
            ("ownerProjectRules", [{"owner": "?*", "project": "p"}]),
            ("ownerProjectRules", [{"owner": "a*", "project": "p"}]),
            # Informational, configuration only: the queries tell the records with the field by the field.
            ("fallbackFieldSince", "2026-10-01T08:30:00Z"),
        ],
    )
    def test_invalid_values_name_the_setting(self, defaults, key, value):
        with pytest.raises(SettingsError) as exc:
            validate_overrides({key: value}, defaults)
        assert exc.value.key == key

    def test_bounds_are_inclusive(self, defaults):
        cleaned = validate_overrides(
            {"minimumConfidence": 30, "repositoriesPerPerson": 25, "partitionBucketShare": 0.2}, defaults
        )
        assert cleaned == {"minimumConfidence": 30, "partitionBucketShare": 0.2, "repositoriesPerPerson": 25}

    def test_whole_float_is_an_int(self, defaults):
        assert validate_overrides({"minimumConfidence": 55.0}, defaults) == {"minimumConfidence": 55}

    def test_values_equal_to_the_default_are_dropped(self, defaults):
        cleaned = validate_overrides(
            {"minimumConfidence": 50, "localFolderOwners": list(defaults.values["localFolderOwners"]), "namesCap": 5},
            defaults,
        )
        assert cleaned == {"namesCap": 5}

    def test_lists_are_trimmed_and_deduplicated(self, defaults):
        cleaned = validate_overrides({"learningSources": [" a/* ", "a/*", "b/*"]}, defaults)
        assert cleaned == {"learningSources": ["a/*", "b/*"]}

    def test_rules_are_normalized(self):
        assert normalize_rules([{"owner": " Payments/* ", "project": "northwind "}]) == [
            {"owner": "payments", "project": "northwind"}
        ]

    def test_rule_owners_with_two_letters_besides_wildcards(self):
        assert normalize_rules([{"owner": "ab*", "project": "p"}]) == [{"owner": "ab*", "project": "p"}]

    def test_configured_datetime_without_seconds_is_completed(self):
        values = build_defaults({"fallbackFieldSince": "2026-10-01T08:30Z"}).values
        assert values["fallbackFieldSince"] == "2026-10-01T08:30:00Z"


class TestEffectiveSettings:
    def test_defaults_have_no_tier(self, defaults):
        eff = build_effective(defaults, {})
        assert eff.tier is None
        assert eff.is_custom is False
        assert eff.info() == {
            "hash": eff.settings_hash,
            "defaults_hash": defaults.defaults_hash,
            "schema_version": SCHEMA_VERSION,
            "overridden": [],
            "tier": None,
        }

    def test_display_overrides_change_neither_cache_key(self, defaults):
        base = build_effective(defaults, {})
        eff = build_effective(defaults, {"sharedProjectThresholdPer30Days": 250, "experimentsHighlightPer30Days": 100})
        assert eff.tier == TIER_VIEW
        assert eff.is_custom is False
        assert (eff.fetch_hash, eff.analysis_hash) == (base.fetch_hash, base.analysis_hash)
        assert eff.settings_hash != base.settings_hash
        assert eff.get("sharedProjectThresholdPer30Days") == 250

    def test_classification_overrides_change_only_the_analysis_key(self, defaults):
        base = build_effective(defaults, {})
        eff = build_effective(defaults, {"minimumConfidence": 55})
        assert eff.tier == TIER_ANALYSIS
        assert eff.is_custom is True
        assert eff.fetch_hash == base.fetch_hash
        assert eff.analysis_hash != base.analysis_hash

    def test_retrieval_overrides_change_the_inputs_key(self, defaults):
        base = build_effective(defaults, {})
        eff = build_effective(defaults, {"repositoriesPerPerson": 15, "minimumConfidence": 50})
        assert eff.tier == TIER_FETCH
        assert eff.fetch_hash != base.fetch_hash
        assert sorted(eff.overridden if hasattr(eff, "overridden") else eff.overrides) == [
            "minimumConfidence",
            "repositoriesPerPerson",
        ]

    def test_nested_overrides_apply(self, defaults):
        eff = build_effective(defaults, {"thresholds.activeDaysProduction": 9, "allocateByPerson.enabled": False})
        assert eff.values["thresholds"]["activeDaysProduction"] == 9
        assert eff.values["thresholds"]["minToolSessions"] == 3
        assert eff.values["allocateByPerson"]["enabled"] is False
        # The defaults are never modified.
        assert defaults.values["thresholds"]["activeDaysProduction"] == 5

    def test_extra_fetch_inputs_change_the_inputs_key(self, defaults):
        a = build_effective(defaults, {}, {"framework": "(a:).*"})
        b = build_effective(defaults, {}, {"framework": "(b:).*"})
        assert a.fetch_hash != b.fetch_hash
        assert a.analysis_hash == b.analysis_hash

    def test_configuration_changes_change_the_analysis_key(self):
        a = build_effective(build_defaults({}), {})
        b = build_effective(build_defaults({"weights": {"how.framework": ["production", 9]}}), {})
        assert a.analysis_hash != b.analysis_hash


class TestRoles:
    @pytest.mark.parametrize(
        ("flags", "expected"),
        [
            ({"is_admin": True}, True),
            ({"is_admin": True, "is_maintainer": True}, True),
            ({"is_maintainer": True}, True),
            ({"is_auditor": True}, False),
            ({}, False),
        ],
    )
    def test_default_roles(self, defaults, flags, expected):
        user = SimpleNamespace(**{"is_admin": False, "is_maintainer": False, "is_auditor": False, **flags})
        assert user_can_tune(user, defaults) is expected

    def test_auditors_can_be_allowed(self):
        d = build_defaults({"tuning": {"roles": ["admin", "maintainer", "auditor"]}})
        assert user_can_tune(SimpleNamespace(is_admin=False, is_maintainer=False, is_auditor=True), d) is True

    def test_tuning_switched_off(self):
        d = build_defaults({"tuning": {"enabled": False}})
        assert user_can_tune(SimpleNamespace(is_admin=True, is_maintainer=True, is_auditor=False), d) is False


class TestSchemaPayload:
    def test_payload(self, defaults):
        payload = schema_payload(defaults, can_tune=False)
        assert payload["can_tune"] is False
        assert payload["schema_version"] == SCHEMA_VERSION
        assert payload["defaults_hash"] == defaults.defaults_hash
        assert [g["id"] for g in payload["groups"]] == ["view", "classify", "alloc", "identity", "fetch"]
        keys = {s["key"]: s for s in payload["settings"]}
        assert keys["minimumConfidence"]["default"] == 50
        assert keys["minimumConfidence"]["min"] == 30 and keys["minimumConfidence"]["max"] == 95
        assert keys["minimumConfidence"]["tier"] == TIER_ANALYSIS
        assert keys["repositoriesPerPerson"]["tier"] == TIER_FETCH
        assert keys["localFolderOwners"]["max_items"] == 50
        # Signal points are shown as fixed values, never as a tunable setting.
        assert not any(k.startswith("weights") for k in keys)
        assert payload["fixed"]["signal_points"]["who.team"] == ["production", 10]
        assert payload["tuning"]["max_override_bytes"] == 4096


class TestChargebackCandidateSetting:
    def test_candidate_production_days_is_a_view_setting_with_bounds(self, defaults):
        spec = SCHEMA_BY_KEY["candidateProductionDaysPer30Days"]
        assert (spec.group, spec.type, spec.minimum, spec.maximum) == ("view", "int", 1, 30)
        assert defaults.values["candidateProductionDaysPer30Days"] == 5
        for bad in (0, 31, 4.5):
            with pytest.raises(SettingsError):
                validate_overrides({"candidateProductionDaysPer30Days": bad}, defaults)
        assert validate_overrides({"candidateProductionDaysPer30Days": 10}, defaults) == {
            "candidateProductionDaysPer30Days": 10
        }

    def test_labels_and_descriptions_say_chargeback_potential(self):
        texts = " ".join(f"{s.label} {s.description}" for s in SCHEMA)
        assert "Not Charged Back" not in texts
        assert "Chargeback Potential" in texts


def test_only_the_candidate_setting_speaks_of_chargeback_candidates():
    """People's "chargeback candidate" is a person; other help texts must not reuse the term."""
    others = [s.key for s in SCHEMA if "chargeback candidate" in s.description.lower()]
    assert others == ["candidateProductionDaysPer30Days"]
    assert "Chargeback Potential" in SCHEMA_BY_KEY["notChargedBack.excludeSharedExperiments"].description
