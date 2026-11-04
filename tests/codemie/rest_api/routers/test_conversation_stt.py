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

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from codemie.configs.llm_config import LLMModel
from codemie.core.exceptions import ExtendedHTTPException, UnknownSttModelError
from codemie.rest_api.routers import llm_models
from codemie.rest_api.routers.conversation import _resolve_audio_filename
from codemie.rest_api.routers.conversation import router as conversation_router
from codemie.rest_api.routers.user import get_user_data
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.stt_service import SttResult

app = FastAPI()


@app.exception_handler(ExtendedHTTPException)
async def _extended_http_exception_handler(request: Request, exc: ExtendedHTTPException):
    return JSONResponse(status_code=exc.code, content={"message": exc.message, "details": exc.details})


app.include_router(conversation_router)
app.include_router(llm_models.router)
client = TestClient(app)

SPEECH_URL = "/v1/speech-recognition"
RECOGNIZE = "codemie.rest_api.routers.conversation.messages.ConversationService.recognize_speech"


@pytest.fixture
def user():
    return User(id="123", username="testuser", name="Test User")


@pytest.fixture(autouse=True)
def override_dependency(user):
    app.dependency_overrides[authenticate] = lambda: user
    yield
    app.dependency_overrides = {}


def _post(data=None, filename="recording.webm", content_type="audio/webm", headers=None):
    return client.post(
        SPEECH_URL,
        files={"file": (filename, b"audio-bytes", content_type)},
        data=data or {},
        headers=headers or {},
    )


class TestSpeechRecognition:
    def test_returns_the_transcript_in_message_without_data_when_there_is_no_metadata(self):
        with patch(RECOGNIZE, return_value=SttResult(text="hello")) as recognize:
            response = _post()

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"message": "hello", "data": None}
        recognize.assert_called_once()

    def test_model_and_language_form_fields_reach_the_service(self, user):
        with patch(RECOGNIZE, return_value=SttResult(text="hello")) as recognize:
            _post(data={"model": "custom-stt", "language": "de"})

        kwargs = recognize.call_args.kwargs
        assert kwargs["user"].id == user.id
        assert kwargs["model"] == "custom-stt"
        assert kwargs["language"] == "de"

    def test_conversation_id_form_field_reaches_the_service(self):
        with patch(RECOGNIZE, return_value=SttResult(text="hello")) as recognize:
            _post(data={"conversation_id": "conv-42"})

        assert recognize.call_args.kwargs["conversation_id"] == "conv-42"

    def test_request_headers_are_handed_to_the_service(self):
        with patch(RECOGNIZE, return_value=SttResult(text="hello")) as recognize:
            _post(headers={"x-conversation-id": "conv-1"})

        assert recognize.call_args.kwargs["forward_headers"]["x-conversation-id"] == "conv-1"

    def test_upload_is_named_after_its_real_audio_format(self):
        with patch(RECOGNIZE, return_value=SttResult(text="hello")) as recognize:
            _post(filename="blob", content_type="audio/webm;codecs=opus")

        assert recognize.call_args.args[0].name == "audio.webm"

    def test_provider_headers_and_fields_are_returned_as_headers_and_in_data(self):
        result = SttResult(
            text="hello",
            headers={"x-transaction-id": "tx-1", "x-file-uri": "https://bucket/key.wav"},
            fields={"file_path": "s3://bucket/key.wav"},
        )
        with patch(RECOGNIZE, return_value=result):
            response = _post()

        assert response.headers["x-transaction-id"] == "tx-1"
        assert response.headers["x-file-uri"] == "https://bucket/key.wav"
        assert response.json()["data"] == {
            "x-transaction-id": "tx-1",
            "x-file-uri": "https://bucket/key.wav",
            "file_path": "s3://bucket/key.wav",
        }

    def test_unknown_model_returns_400_with_the_reason(self):
        with patch(RECOGNIZE, side_effect=UnknownSttModelError("Unknown or not allowed STT model: nope")):
            response = _post(data={"model": "nope"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "nope" in response.text

    def test_other_value_errors_are_not_reported_as_client_errors(self):
        with patch(RECOGNIZE, side_effect=ValueError("internal parse problem")):
            with pytest.raises(ValueError, match="internal parse problem"):
                _post()

    def test_provider_error_status_is_returned(self):
        error = ExtendedHTTPException(code=413, message="Speech recognition failed", details="too large", help="")
        with patch(RECOGNIZE, side_effect=error):
            response = _post()

        assert response.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE


class TestResolveAudioFilename:
    def test_supported_extension_is_kept(self):
        assert _resolve_audio_filename("clip.WEBM", None) == "clip.WEBM"

    def test_extension_comes_from_the_content_type_when_the_name_has_none(self):
        assert _resolve_audio_filename("blob", "audio/mpeg") == "audio.mp3"
        assert _resolve_audio_filename(None, "audio/ogg;codecs=opus") == "audio.ogg"

    def test_default_is_wav(self):
        assert _resolve_audio_filename(None, None) == "audio.wav"
        assert _resolve_audio_filename("blob", "application/octet-stream") == "audio.wav"


class TestSttModelsEndpoint:
    def test_returns_the_models_allowed_for_the_user(self, user):
        models = [
            LLMModel(base_name="whisper", deployment_name="whisper", enabled=True, label="whisper"),
            LLMModel(base_name="custom-stt", deployment_name="custom-stt", enabled=True),
        ]
        with patch("codemie.rest_api.routers.llm_models.llm_service") as service:
            service.get_allowed_audio_transcription_models.return_value = models
            response = client.get("/v1/audio_transcription_models")

        assert response.status_code == status.HTTP_200_OK
        assert [m["base_name"] for m in response.json()] == ["whisper", "custom-stt"]
        service.get_allowed_audio_transcription_models.assert_called_once()

    def test_returns_an_empty_list_when_no_model_is_available(self):
        with patch("codemie.rest_api.routers.llm_models.llm_service") as service:
            service.get_allowed_audio_transcription_models.return_value = []
            response = client.get("/v1/audio_transcription_models")

        assert response.json() == []


class TestSttSupportFlag:
    @pytest.mark.parametrize(("models", "expected"), [([], False), ([MagicMock()], True)])
    def test_flag_follows_the_available_models(self, user, models, expected):
        user_data = MagicMock()
        user_data.dict.return_value = {"user_id": user.id}
        with (
            patch("codemie.rest_api.routers.user.UserData.get_by_fields", return_value=user_data),
            patch("codemie.rest_api.routers.user.llm_service") as service,
        ):
            service.get_allowed_audio_transcription_models.return_value = models
            data = get_user_data(user)

        assert data["stt_support"] is expected
