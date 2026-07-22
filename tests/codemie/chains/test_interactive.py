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

import json

import pytest
from pydantic import ValidationError

from codemie.core.interactive import (
    InteractiveFeaturesConfig,
    InteractiveRequest,
    InteractiveResponse,
    build_surface_args_schema,
    enabled_element_types,
    validate_response_values,
    validate_surface,
)

CFG_CHOICE_ONLY = InteractiveFeaturesConfig(action_buttons=False, choice=True, short_forms=False)
CFG_ALL = InteractiveFeaturesConfig(action_buttons=True, choice=True, short_forms=True)
CFG_FORMS_ONLY = InteractiveFeaturesConfig(action_buttons=False, choice=False, short_forms=True)


class TestEnabledElementTypes:
    def test_choice_only_has_layout_and_choice_no_button(self):
        types = enabled_element_types(CFG_CHOICE_ONLY)
        assert types == {"text", "column", "row", "multiple_choice", "dropdown"}

    def test_button_enabled_by_short_forms(self):
        assert "button" in enabled_element_types(CFG_FORMS_ONLY)

    def test_all_disabled_is_empty(self):
        cfg = InteractiveFeaturesConfig()
        assert enabled_element_types(cfg) == set()


class TestElementRegistryAndCatalog:
    def test_default_catalog_derived_from_registry(self):
        from codemie.core.interactive import default_element_catalog

        catalog = default_element_catalog()
        assert set(catalog["layout"]) == {"text", "column", "row"}
        # choice enables both multiple_choice and dropdown per registry FEATURES
        assert set(catalog["features"]["choice"]) == {"multiple_choice", "dropdown"}
        assert "date_picker" in catalog["features"]["short_forms"]

    def test_catalog_override_gates_types(self):
        # A deployment can drop dropdown from the 'choice' feature via config without code.
        catalog = {"layout": ["text", "column", "row"], "features": {"choice": ["multiple_choice"]}}
        types = enabled_element_types(CFG_CHOICE_ONLY, catalog)
        assert "multiple_choice" in types
        assert "dropdown" not in types

    def test_catalog_ignores_unknown_element_types(self):
        # A stale/typo catalog entry cannot smuggle an unknown type into the allowed set.
        catalog = {"layout": ["text"], "features": {"choice": ["multiple_choice", "made_up_widget"]}}
        types = enabled_element_types(CFG_CHOICE_ONLY, catalog)
        assert "made_up_widget" not in types
        assert "multiple_choice" in types

    def test_malformed_catalog_shape_does_not_raise(self):
        # The catalog is raw customer config; a bad SHAPE must degrade, never 500.
        # Non-dict catalog -> registry defaults.
        assert "multiple_choice" in enabled_element_types(CFG_CHOICE_ONLY, "onboarding")
        # Dict catalog with malformed sub-shapes -> those parts empty (fail-closed), no raise.
        for bad in (
            {"features": "onboarding"},
            {"features": {"choice": 5}},
            {"layout": 7, "features": {"choice": ["dropdown"]}},
        ):
            types = enabled_element_types(CFG_CHOICE_ONLY, bad)
            assert isinstance(types, set)

    def test_registry_is_single_source_for_union_and_map(self):
        from codemie.core.interactive import ELEMENT_REGISTRY, _ELEMENT_BY_TYPE

        # Every registered model is reachable by its wire discriminator.
        assert len(_ELEMENT_BY_TYPE) == len(ELEMENT_REGISTRY)
        assert _ELEMENT_BY_TYPE["dropdown"].__name__ == "DropdownElement"


class TestValidateSurface:
    def test_rejects_disabled_element(self):
        with pytest.raises(ValueError, match="button"):
            validate_surface([{"type": "button", "id": "b1", "label": "OK"}], CFG_CHOICE_ONLY)

    def test_rejects_disabled_nested_in_column(self):
        surface = [{"type": "column", "children": [{"type": "text_field", "id": "f1", "label": "Name"}]}]
        with pytest.raises(ValueError, match="text_field"):
            validate_surface(surface, CFG_CHOICE_ONLY)

    def test_accepts_enabled_tree(self):
        surface = [
            {
                "type": "column",
                "children": [
                    {"type": "text", "content": "Pick one"},
                    {
                        "type": "multiple_choice",
                        "id": "c1",
                        "options": [{"value": "a", "label": "A"}],
                        "max_allowed_selections": 1,
                    },
                ],
            }
        ]
        elements = validate_surface(surface, CFG_CHOICE_ONLY)
        assert elements[0].type == "column"


class TestArgsSchemaFactory:
    def test_schema_excludes_disabled_types(self):
        schema_cls = build_surface_args_schema(CFG_CHOICE_ONLY)
        json_schema = str(schema_cls.model_json_schema())
        assert "multiple_choice" in json_schema
        assert "text_field" not in json_schema
        assert "'button'" not in json_schema

    def test_schema_validates_payload(self):
        schema_cls = build_surface_args_schema(CFG_CHOICE_ONLY)
        with pytest.raises(ValidationError):
            schema_cls(surface=[{"type": "button", "id": "b", "label": "X"}])

    def test_no_features_enabled_raises(self):
        with pytest.raises(ValueError):
            build_surface_args_schema(InteractiveFeaturesConfig())


def _request_with_form():
    return InteractiveRequest(
        request_id="r1",
        surface=[
            {
                "type": "text_field",
                "id": "email",
                "label": "Email",
                "validation": {"required": True, "email": True},
            },
            {"type": "text_field", "id": "code", "label": "Code", "validation": {"regex": "^[0-9]{4}$"}},
            {"type": "button", "id": "submit", "label": "Submit"},
        ],
    )


class TestValidateResponseValues:
    def test_missing_required_raises(self):
        resp = InteractiveResponse(request_id="r1", kind="form", payload={"values": {"code": "1234"}})
        with pytest.raises(ValueError, match="email"):
            validate_response_values(resp, _request_with_form())

    def test_bad_email_raises(self):
        resp = InteractiveResponse(
            request_id="r1", kind="form", payload={"values": {"email": "not-an-email", "code": "1234"}}
        )
        with pytest.raises(ValueError, match="email"):
            validate_response_values(resp, _request_with_form())

    def test_regex_mismatch_raises(self):
        resp = InteractiveResponse(request_id="r1", kind="form", payload={"values": {"email": "a@b.co", "code": "12"}})
        with pytest.raises(ValueError, match="code"):
            validate_response_values(resp, _request_with_form())

    def test_valid_form_passes(self):
        resp = InteractiveResponse(
            request_id="r1", kind="form", payload={"values": {"email": "a@b.co", "code": "1234"}}
        )
        validate_response_values(resp, _request_with_form())

    def test_choice_over_max_selections_raises(self):
        req = InteractiveRequest(
            request_id="r2",
            surface=[
                {
                    "type": "multiple_choice",
                    "id": "c1",
                    "max_allowed_selections": 1,
                    "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}],
                }
            ],
        )
        resp = InteractiveResponse(request_id="r2", kind="choice", payload={"selected": ["a", "b"]})
        with pytest.raises(ValueError, match="max"):
            validate_response_values(resp, req)

    def test_choice_unknown_value_raises(self):
        req = InteractiveRequest(
            request_id="r2",
            surface=[
                {
                    "type": "multiple_choice",
                    "id": "c1",
                    "max_allowed_selections": 1,
                    "options": [{"value": "a", "label": "A"}],
                }
            ],
        )
        resp = InteractiveResponse(request_id="r2", kind="choice", payload={"selected": ["zzz"]})
        with pytest.raises(ValueError, match="[Uu]nknown"):
            validate_response_values(resp, req)

    def test_text_fallback_always_passes(self):
        resp = InteractiveResponse(request_id="r1", kind="text_fallback", payload={"text": "free text"})
        validate_response_values(resp, _request_with_form())

    def test_request_id_generated_by_default(self):
        req = InteractiveRequest(surface=[{"type": "text", "content": "hi"}])
        assert req.request_id


class TestWireFields:
    def test_streamed_result_carries_interactive_request(self):
        import json

        from codemie.chains.base import StreamedGenerationResult

        req = InteractiveRequest(surface=[{"type": "text", "content": "hi"}])
        chunk = json.loads(StreamedGenerationResult(interactive_request=req).model_dump_json())
        assert chunk["interactive_request"]["surface"][0]["type"] == "text"

    def test_streamed_result_default_has_null_interactive_request(self):
        import json

        from codemie.chains.base import StreamedGenerationResult

        chunk = json.loads(StreamedGenerationResult(generated="x").model_dump_json())
        assert chunk.get("interactive_request") is None

    def test_chat_request_accepts_interactive_response(self):
        from codemie.core.models import AssistantChatRequest

        request = AssistantChatRequest(
            text="✓ OK",
            interactive_response={"request_id": "r1", "kind": "action", "payload": {"action": "ok"}},
        )
        assert request.interactive_response.kind == "action"
        assert AssistantChatRequest(text="hi").interactive_response is None

    def test_generated_message_persists_interactive_fields(self):
        from codemie.rest_api.models.conversation import GeneratedMessage

        req = InteractiveRequest(request_id="r1", surface=[{"type": "text", "content": "hi"}])
        message = GeneratedMessage(role="Assistant", message="", interactive_request=req)
        assert message.interactive_request.request_id == "r1"
        resp = InteractiveResponse(request_id="r1", kind="action", payload={"action": "ok"})
        user_message = GeneratedMessage(role="User", message="✓ OK", interactive_response=resp)
        assert user_message.interactive_response.request_id == "r1"
        assert GeneratedMessage(role="User", message="x").interactive_request is None


class TestResponseHardening:
    def test_invalid_regex_raises_value_error_not_re_error(self):
        req = InteractiveRequest(
            request_id="r1",
            surface=[
                {"type": "text_field", "id": "f", "label": "F", "validation": {"regex": "("}},
                {"type": "button", "id": "s", "label": "S"},
            ],
        )
        resp = InteractiveResponse(request_id="r1", kind="form", payload={"values": {"f": "x"}})
        with pytest.raises(ValueError):
            validate_response_values(resp, req)

    def test_overlong_field_value_rejected(self):
        req = InteractiveRequest(
            request_id="r1",
            surface=[
                {"type": "text_field", "id": "f", "label": "F", "validation": {"regex": "^a+$"}},
                {"type": "button", "id": "s", "label": "S"},
            ],
        )
        resp = InteractiveResponse(request_id="r1", kind="form", payload={"values": {"f": "a" * 20000}})
        with pytest.raises(ValueError, match="too long"):
            validate_response_values(resp, req)

    def test_overlong_regex_pattern_rejected(self):
        req = InteractiveRequest(
            request_id="r1",
            surface=[
                {"type": "text_field", "id": "f", "label": "F", "validation": {"regex": "a" * 2000}},
                {"type": "button", "id": "s", "label": "S"},
            ],
        )
        resp = InteractiveResponse(request_id="r1", kind="form", payload={"values": {"f": "aaa"}})
        with pytest.raises(ValueError, match="pattern"):
            validate_response_values(resp, req)

    def test_selected_not_a_list_raises_value_error(self):
        req = InteractiveRequest(
            request_id="r1",
            surface=[
                {
                    "type": "multiple_choice",
                    "id": "c",
                    "max_allowed_selections": 1,
                    "options": [{"value": "a", "label": "A"}],
                }
            ],
        )
        resp = InteractiveResponse(request_id="r1", kind="choice", payload={"selected": 5})
        with pytest.raises(ValueError, match="list"):
            validate_response_values(resp, req)

    def test_values_not_a_dict_raises_value_error(self):
        req = InteractiveRequest(
            request_id="r1",
            surface=[
                {"type": "text_field", "id": "f", "label": "F"},
                {"type": "button", "id": "s", "label": "S"},
            ],
        )
        resp = InteractiveResponse(request_id="r1", kind="form", payload={"values": "oops"})
        with pytest.raises(ValueError, match="object"):
            validate_response_values(resp, req)


class TestRedosAndPayloadHardening:
    def test_catastrophic_regex_times_out_as_value_error(self):
        req = InteractiveRequest(
            request_id="r1",
            surface=[
                {"type": "text_field", "id": "f", "label": "F", "validation": {"regex": r"(a|a)*$"}},
                {"type": "button", "id": "s", "label": "S"},
            ],
        )
        resp = InteractiveResponse(request_id="r1", kind="form", payload={"values": {"f": "a" * 40 + "X"}})
        with pytest.raises(ValueError):
            validate_response_values(resp, req)

    def test_unhashable_selected_item_raises_value_error(self):
        req = InteractiveRequest(
            request_id="r1",
            surface=[
                {
                    "type": "multiple_choice",
                    "id": "c",
                    "max_allowed_selections": 2,
                    "options": [{"value": "a", "label": "A"}],
                }
            ],
        )
        resp = InteractiveResponse(request_id="r1", kind="choice", payload={"selected": [{"nested": 1}]})
        with pytest.raises(ValueError):
            validate_response_values(resp, req)

    def test_oversized_total_payload_rejected(self):
        req = InteractiveRequest(
            request_id="r1",
            surface=[{"type": "button", "id": "ok", "label": "OK"}],
        )
        resp = InteractiveResponse(request_id="r1", kind="action", payload={"action": "ok", "junk": "z" * 200000})
        with pytest.raises(ValueError, match="[Pp]ayload"):
            validate_response_values(resp, req)


class TestKindAndSurfaceValidation:
    def _surface_button(self):
        return InteractiveRequest(request_id="r1", surface=[{"type": "button", "id": "ok", "label": "OK"}])

    def test_action_unknown_id_rejected(self):
        resp = InteractiveResponse(request_id="r1", kind="action", payload={"action": "nope"})
        with pytest.raises(ValueError, match="[Aa]ction"):
            validate_response_values(resp, self._surface_button())

    def test_action_known_id_passes(self):
        resp = InteractiveResponse(request_id="r1", kind="action", payload={"action": "ok"})
        validate_response_values(resp, self._surface_button())

    def test_text_fallback_with_structured_payload_rejected(self):
        # A client must not smuggle structured data past validation via kind=text_fallback
        resp = InteractiveResponse(request_id="r1", kind="text_fallback", payload={"text": "hi", "values": {"x": "y"}})
        with pytest.raises(ValueError):
            validate_response_values(resp, self._surface_button())

    def test_text_fallback_plain_text_passes(self):
        resp = InteractiveResponse(request_id="r1", kind="text_fallback", payload={"text": "free"})
        validate_response_values(resp, self._surface_button())

    def test_form_unknown_field_key_rejected(self):
        req = InteractiveRequest(
            request_id="r1",
            surface=[
                {"type": "text_field", "id": "name", "label": "Name"},
                {"type": "button", "id": "s", "label": "S"},
            ],
        )
        resp = InteractiveResponse(request_id="r1", kind="form", payload={"values": {"name": "a", "sneaky": "z"}})
        with pytest.raises(ValueError, match="[Uu]nknown"):
            validate_response_values(resp, req)

    def test_required_not_satisfied_by_falsy_nonstring(self):
        req = InteractiveRequest(
            request_id="r1",
            surface=[
                {"type": "text_field", "id": "n", "label": "N", "validation": {"required": True}},
                {"type": "button", "id": "s", "label": "S"},
            ],
        )
        resp = InteractiveResponse(request_id="r1", kind="form", payload={"values": {"n": 0}})
        with pytest.raises(ValueError, match="required|string"):
            validate_response_values(resp, req)


class TestSurfaceDepthLimits:
    def test_deeply_nested_surface_rejected_as_value_error(self):
        node = {"type": "text", "content": "x"}
        for _ in range(30):
            node = {"type": "column", "children": [node]}
        with pytest.raises(ValueError, match="[Dd]epth|[Nn]est"):
            validate_surface([node], CFG_ALL)

    def test_too_many_elements_rejected(self):
        surface = [{"type": "button", "id": f"b{i}", "label": str(i)} for i in range(500)]
        with pytest.raises(ValueError, match="[Cc]ount|too many|max"):
            validate_surface(surface, CFG_ALL)

    def test_reasonable_surface_still_accepted(self):
        surface = [{"type": "column", "children": [{"type": "button", "id": "ok", "label": "OK"}]}]
        validate_surface(surface, CFG_ALL)


class TestDynamicSchemaSurfaceRevalidation:
    def test_column_surface_from_tool_args_schema_revalidates(self):
        # Reproduces the tool path: args parsed via build_surface_args_schema produce
        # DYNAMIC ColumnElement instances; validate_surface must accept them, not raise
        # a pydantic model_type mismatch against the module-level ColumnElement.
        from codemie.core.interactive import build_surface_args_schema

        schema_cls = build_surface_args_schema(CFG_ALL)
        parsed = schema_cls(
            surface=[
                {"type": "text", "content": "Plan:"},
                {
                    "type": "column",
                    "children": [
                        {"type": "text", "content": "step 1"},
                        {"type": "button", "id": "ok", "label": "Approve", "style": "secondary"},
                    ],
                },
            ]
        )
        # parsed.surface elements are dynamic-class instances (Text/Column)
        elements = validate_surface(parsed.surface, CFG_ALL)
        assert elements[1].type == "column"
        assert elements[1].children[1].id == "ok"


class TestCombinedSubmitResponse:
    def _multi_block_request(self):
        return InteractiveRequest(
            request_id="r1",
            surface=[
                {
                    "type": "multiple_choice",
                    "id": "db",
                    "max_allowed_selections": 1,
                    "options": [{"value": "postgresql", "label": "PostgreSQL"}, {"value": "mysql", "label": "MySQL"}],
                },
                {
                    "type": "multiple_choice",
                    "id": "feats",
                    "max_allowed_selections": 2,
                    "options": [{"value": "auth", "label": "Auth"}, {"value": "billing", "label": "Billing"}],
                },
                {
                    "type": "text_field",
                    "id": "email",
                    "label": "Email",
                    "validation": {"required": True, "email": True},
                },
                {"type": "button", "id": "approve", "label": "Approve"},
            ],
        )

    def test_combined_submit_validates_each_answer_against_its_element(self):
        # The key bug: a value valid for one choice must NOT be rejected because it is
        # unknown to another choice. Combined answers-by-id fix that.
        resp = InteractiveResponse(
            request_id="r1",
            kind="submit",
            payload={
                "action": "approve",
                "answers": {
                    "db": {"selected": ["postgresql"]},
                    "feats": {"selected": ["auth", "billing"]},
                    "email": {"value": "a@b.co"},
                },
            },
        )
        validate_response_values(resp, self._multi_block_request())

    def test_combined_choice_value_checked_against_correct_element(self):
        resp = InteractiveResponse(
            request_id="r1",
            kind="submit",
            payload={
                "answers": {"db": {"selected": ["auth"]}},  # 'auth' belongs to feats, not db
            },
        )
        with pytest.raises(ValueError, match="[Uu]nknown choice"):
            validate_response_values(resp, self._multi_block_request())

    def test_combined_over_cap_rejected(self):
        resp = (
            InteractiveResponse(
                request_id="r1",
                kind="submit",
                payload={
                    "answers": {
                        "feats": {"selected": ["auth", "billing"]},
                        "db": {"selected": ["postgresql", "mysql"]},
                    },
                    "email": None,
                },
            )
            if False
            else InteractiveResponse(
                request_id="r1",
                kind="submit",
                payload={
                    "answers": {"db": {"selected": ["postgresql", "mysql"]}},  # db cap=1
                },
            )
        )
        with pytest.raises(ValueError, match="max"):
            validate_response_values(resp, self._multi_block_request())

    def test_combined_required_field_missing_rejected(self):
        resp = InteractiveResponse(
            request_id="r1",
            kind="submit",
            payload={
                "answers": {"db": {"selected": ["postgresql"]}},  # email required but absent
            },
        )
        with pytest.raises(ValueError, match="email|required"):
            validate_response_values(resp, self._multi_block_request())

    def test_combined_unknown_answer_id_rejected(self):
        resp = InteractiveResponse(
            request_id="r1",
            kind="submit",
            payload={
                "answers": {"ghost": {"selected": ["x"]}, "email": {"value": "a@b.co"}},
            },
        )
        with pytest.raises(ValueError, match="[Uu]nknown answer"):
            validate_response_values(resp, self._multi_block_request())

    def test_combined_unknown_action_rejected(self):
        resp = InteractiveResponse(
            request_id="r1",
            kind="submit",
            payload={
                "action": "nope",
                "answers": {"email": {"value": "a@b.co"}},
            },
        )
        with pytest.raises(ValueError, match="[Aa]ction"):
            validate_response_values(resp, self._multi_block_request())

    def test_combined_submit_field_without_validation_does_not_crash(self):
        # Regression: a text_field / checkbox with NO validation block must not raise
        # AttributeError ('NoneType' has no attribute 'required') on combined submit.
        req = InteractiveRequest(
            request_id="r1",
            surface=[
                {
                    "type": "multiple_choice",
                    "id": "c",
                    "max_allowed_selections": 1,
                    "options": [{"value": "a", "label": "A"}],
                },
                {"type": "text_field", "id": "note", "label": "Note"},
                {"type": "checkbox", "id": "agree", "label": "Agree"},
                {"type": "button", "id": "send", "label": "Send"},
            ],
        )
        resp = InteractiveResponse(
            request_id="r1",
            kind="submit",
            payload={
                "action": "send",
                "answers": {"c": {"selected": ["a"]}, "note": {"value": "hi"}, "agree": {"value": True}},
            },
        )
        validate_response_values(resp, req)


class TestKindCoverageHardening:
    def _required_field_surface(self):
        return InteractiveRequest(
            request_id="r1",
            surface=[
                {
                    "type": "text_field",
                    "id": "email",
                    "label": "Email",
                    "validation": {"required": True, "email": True},
                },
                {"type": "button", "id": "send", "label": "Send"},
            ],
        )

    def test_action_kind_cannot_skip_required_field(self):
        # Attack: answer a form that has a required field via kind='action' (a valid
        # button id) to skip the required/email validation entirely.
        resp = InteractiveResponse(request_id="r1", kind="action", payload={"action": "send"})
        with pytest.raises(ValueError, match="cannot answer required"):
            validate_response_values(resp, self._required_field_surface())

    def test_form_kind_cannot_skip_required_dropdown(self):
        req = InteractiveRequest(
            request_id="r1",
            surface=[
                {
                    "type": "dropdown",
                    "id": "db",
                    "label": "DB",
                    "required": True,
                    "options": [{"value": "pg", "label": "PG"}],
                },
                {"type": "button", "id": "send", "label": "Send"},
            ],
        )
        resp = InteractiveResponse(request_id="r1", kind="form", payload={"values": {}})
        with pytest.raises(ValueError, match="cannot answer required"):
            validate_response_values(resp, req)

    def test_action_kind_ok_when_no_required_inputs(self):
        req = InteractiveRequest(request_id="r1", surface=[{"type": "button", "id": "ok", "label": "OK"}])
        resp = InteractiveResponse(request_id="r1", kind="action", payload={"action": "ok"})
        validate_response_values(resp, req)

    def test_text_fallback_exempt_from_required_coverage(self):
        resp = InteractiveResponse(request_id="r1", kind="text_fallback", payload={"text": "free"})
        validate_response_values(resp, self._required_field_surface())


class TestArgsSchemaDepthGuard:
    def test_args_schema_rejects_deeply_nested_surface(self):
        # The tool framework validates args against build_surface_args_schema BEFORE
        # execute()/validate_surface runs, so the depth cap must apply here too — a
        # clean ValidationError, never a RecursionError.
        schema_cls = build_surface_args_schema(CFG_ALL)
        node = {"type": "text", "content": "x"}
        for _ in range(30):
            node = {"type": "column", "children": [node]}
        with pytest.raises(ValidationError):
            schema_cls(surface=[node])

    def test_args_schema_rejects_too_many_elements(self):
        schema_cls = build_surface_args_schema(CFG_ALL)
        surface = [{"type": "button", "id": f"b{i}", "label": str(i)} for i in range(500)]
        with pytest.raises(ValidationError):
            schema_cls(surface=surface)


class TestDropdownElement:
    def test_dropdown_enabled_by_choice(self):
        assert "dropdown" in enabled_element_types(CFG_CHOICE_ONLY)
        assert "dropdown" not in enabled_element_types(CFG_FORMS_ONLY)

    def test_schema_includes_dropdown_under_choice(self):
        json_schema = str(build_surface_args_schema(CFG_CHOICE_ONLY).model_json_schema())
        assert "dropdown" in json_schema

    def test_surface_accepts_dropdown(self):
        surface = [
            {
                "type": "dropdown",
                "id": "db",
                "label": "Database",
                "options": [{"value": "postgresql", "label": "PostgreSQL"}, {"value": "mysql", "label": "MySQL"}],
            }
        ]
        elements = validate_surface(surface, CFG_CHOICE_ONLY)
        assert elements[0].type == "dropdown"

    def _dropdown_request(self, required=False):
        return InteractiveRequest(
            request_id="r1",
            surface=[
                {
                    "type": "dropdown",
                    "id": "db",
                    "label": "Database",
                    "required": required,
                    "options": [{"value": "postgresql", "label": "PostgreSQL"}, {"value": "mysql", "label": "MySQL"}],
                },
                {"type": "button", "id": "ok", "label": "OK"},
            ],
        )

    def test_valid_dropdown_value_passes(self):
        resp = InteractiveResponse(
            request_id="r1", kind="submit", payload={"action": "ok", "answers": {"db": {"value": "mysql"}}}
        )
        validate_response_values(resp, self._dropdown_request())

    def test_unknown_dropdown_value_rejected(self):
        resp = InteractiveResponse(request_id="r1", kind="submit", payload={"answers": {"db": {"value": "oracle"}}})
        with pytest.raises(ValueError, match="[Uu]nknown dropdown"):
            validate_response_values(resp, self._dropdown_request())

    def test_required_dropdown_missing_rejected(self):
        resp = InteractiveResponse(request_id="r1", kind="submit", payload={"answers": {"db": {"value": ""}}})
        with pytest.raises(ValueError, match="required"):
            validate_response_values(resp, self._dropdown_request(required=True))

    def test_optional_dropdown_empty_passes(self):
        resp = InteractiveResponse(request_id="r1", kind="submit", payload={"answers": {"db": {"value": ""}}})
        validate_response_values(resp, self._dropdown_request(required=False))


class TestDatePickerElement:
    def test_date_picker_enabled_by_short_forms(self):
        assert "date_picker" in enabled_element_types(CFG_FORMS_ONLY)
        assert "date_picker" not in enabled_element_types(CFG_CHOICE_ONLY)

    def test_schema_includes_date_picker_under_short_forms(self):
        json_schema = str(build_surface_args_schema(CFG_FORMS_ONLY).model_json_schema())
        assert "date_picker" in json_schema

    def test_surface_accepts_date_picker(self):
        surface = [{"type": "date_picker", "id": "d", "label": "When"}]
        elements = validate_surface(surface, CFG_FORMS_ONLY)
        assert elements[0].type == "date_picker"

    def _date_request(self, required=False, dmin=None, dmax=None):
        element = {"type": "date_picker", "id": "d", "label": "When", "required": required}
        if dmin:
            element["min"] = dmin
        if dmax:
            element["max"] = dmax
        return InteractiveRequest(request_id="r1", surface=[element, {"type": "button", "id": "ok", "label": "OK"}])

    def test_valid_date_passes(self):
        resp = InteractiveResponse(
            request_id="r1", kind="submit", payload={"action": "ok", "answers": {"d": {"value": "2026-07-20"}}}
        )
        validate_response_values(resp, self._date_request())

    def test_malformed_date_rejected(self):
        resp = InteractiveResponse(request_id="r1", kind="submit", payload={"answers": {"d": {"value": "20-07-2026"}}})
        with pytest.raises(ValueError, match="ISO date"):
            validate_response_values(resp, self._date_request())

    def test_date_before_min_rejected(self):
        resp = InteractiveResponse(request_id="r1", kind="submit", payload={"answers": {"d": {"value": "2026-01-01"}}})
        with pytest.raises(ValueError, match="on or after"):
            validate_response_values(resp, self._date_request(dmin="2026-06-01"))

    def test_date_after_max_rejected(self):
        resp = InteractiveResponse(request_id="r1", kind="submit", payload={"answers": {"d": {"value": "2026-12-31"}}})
        with pytest.raises(ValueError, match="on or before"):
            validate_response_values(resp, self._date_request(dmax="2026-08-01"))

    def test_required_date_missing_rejected(self):
        resp = InteractiveResponse(request_id="r1", kind="submit", payload={"answers": {"d": {"value": ""}}})
        with pytest.raises(ValueError, match="required"):
            validate_response_values(resp, self._date_request(required=True))

    def test_optional_date_empty_passes(self):
        resp = InteractiveResponse(request_id="r1", kind="submit", payload={"answers": {"d": {"value": ""}}})
        validate_response_values(resp, self._date_request(required=False))


class TestReviewFixes:
    """Regression tests for the code-review findings CR-002/004/006/007."""

    def test_email_rejects_trailing_newline(self):
        # CR-007: EMAIL_RE.fullmatch (not .match), so a trailing newline is rejected.
        resp = InteractiveResponse(
            request_id="r1", kind="form", payload={"values": {"email": "a@b.co\n", "code": "1234"}}
        )
        with pytest.raises(ValueError, match="email"):
            validate_response_values(resp, _request_with_form())

    def test_form_length_caps_field_without_validation_block(self):
        # CR-004: a text_field with NO validation block is still length-capped in form kind.
        from codemie.core.interactive import MAX_FIELD_VALUE_LEN

        req = InteractiveRequest(request_id="r3", surface=[{"type": "text_field", "id": "note", "label": "Note"}])
        resp = InteractiveResponse(
            request_id="r3", kind="form", payload={"values": {"note": "x" * (MAX_FIELD_VALUE_LEN + 1)}}
        )
        with pytest.raises(ValueError, match="too long"):
            validate_response_values(resp, req)

    def test_choice_kind_rejects_multiple_choice_elements(self):
        # CR-006: kind='choice' is only well-defined for a single choice element.
        req = InteractiveRequest(
            request_id="r4",
            surface=[
                {"type": "multiple_choice", "id": "c1", "options": [{"value": "a", "label": "A"}]},
                {"type": "multiple_choice", "id": "c2", "options": [{"value": "b", "label": "B"}]},
            ],
        )
        resp = InteractiveResponse(request_id="r4", kind="choice", payload={"selected": ["a"]})
        with pytest.raises(ValueError, match="submit"):
            validate_response_values(resp, req)

    def test_catalog_layout_ignores_non_layout_types(self):
        # CR-002a: a leaf type listed under catalog 'layout' is NOT force-enabled.
        catalog = {"layout": ["text", "text_field"], "features": {}}
        types = enabled_element_types(CFG_FORMS_ONLY, catalog)
        assert "text" in types
        assert "text_field" not in types

    def test_args_schema_excludes_containers_when_catalog_drops_them(self):
        # CR-002b: the schema advertises column/row only when the catalog enables them.
        catalog = {"layout": ["text"], "features": {"choice": ["multiple_choice"]}}
        schema_json = json.dumps(build_surface_args_schema(CFG_CHOICE_ONLY, catalog).model_json_schema())
        assert '"column"' not in schema_json
        assert '"row"' not in schema_json
        assert '"multiple_choice"' in schema_json
