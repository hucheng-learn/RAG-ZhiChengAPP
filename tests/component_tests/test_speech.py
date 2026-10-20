"""验证语音供应商协议、边界条件与接口错误脱敏，不调用真实付费服务。"""

import base64
import io
import json
import wave
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import create_app
from common.config import Settings
from services.speech import MAX_AUDIO_BYTES, SAMPLE_RATE, SpeechRecognition


def wav(frames=1600, rate=SAMPLE_RATE, channels=1):
    output = io.BytesIO()
    with wave.open(output, "wb") as recording:
        recording.setnchannels(channels)
        recording.setsampwidth(2)
        recording.setframerate(rate)
        recording.writeframes(b"\0\0" * frames * channels)
    return output.getvalue()


@pytest.fixture
def application():
    settings = Settings(
        _env_file=None, llm_api_key="private-asr-key",
        llm_model="chat-test-model", asr_model="configured-asr-model"
    )
    return create_app(settings)


def use_provider(application, handler):
    application.state.speech = SpeechRecognition(
        application.state.speech.settings, transport=httpx.MockTransport(handler)
    )
    # 无需启动数据库与模型，语音路由独立可用。
    return TestClient(application)


def test_transcribe_uses_documented_payload_and_preserves_credentials(application):
    audio = wav()

    def provider(request):
        body = json.loads(request.content)
        assert body["model"] == "configured-asr-model"
        assert body["parameters"] == {"format": "wav", "sample_rate": "16000"}
        content = body["input"]["messages"][0]["content"][0]
        assert content["type"] == "input_audio"
        prefix, encoded = content["input_audio"]["data"].split(",", 1)
        assert prefix == "data:audio/wav;base64"
        assert base64.b64decode(encoded) == audio
        assert request.headers["authorization"] == "Bearer private-asr-key"
        assert request.headers["X-DashScope-SSE"] == "disable"
        return httpx.Response(200, json={"output": {"text": "  解释一下 Python 装饰器。  "}})

    client = use_provider(application, provider)
    response = client.post("/api/v1/speech/transcribe", files={"audio": ("recording.wav", audio)})
    assert response.status_code == 200
    assert response.json()["text"] == "解释一下 Python 装饰器。"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert "private-asr-key" not in response.text


@pytest.mark.parametrize("audio", [b"", b"not-wav", wav(0), wav(rate=48000),
                                  wav(channels=2), wav()[:-10], wav(16000 * 121)],
                         ids=["empty", "not-wav", "no-frames", "wrong-rate",
                              "stereo", "truncated", "too-long"])
def test_invalid_audio_never_reaches_provider(application, audio):
    def provider(request):
        pytest.fail("invalid audio reached provider")

    response = use_provider(application, provider).post(
        "/api/v1/speech/transcribe", files={"audio": ("recording.wav", audio)}
    )
    assert response.status_code in (413, 422)


def test_oversized_upload(application):
    client = use_provider(application, lambda request: pytest.fail("oversized audio sent"))
    response = client.post("/api/v1/speech/transcribe", files={
        "audio": ("recording.wav", b"x" * (MAX_AUDIO_BYTES + 1))
    })
    assert response.status_code == 413


@pytest.mark.parametrize("mode,status,code", [
    ("timeout", 504, "asr_timeout"), ("auth", 502, "asr_failed"),
    ("malformed", 502, "asr_failed"), ("empty", 422, "asr_empty"),
])
def test_upstream_errors_are_safe(application, mode, status, code):
    def provider(request):
        if mode == "timeout":
            raise httpx.ReadTimeout("private-asr-key", request=request)
        if mode == "auth":
            return httpx.Response(401, text="private-asr-key")
        if mode == "malformed":
            return httpx.Response(200, json={"secret": "private-asr-key"})
        return httpx.Response(200, json={"output": {"text": " "}})

    response = use_provider(application, provider).post(
        "/api/v1/speech/transcribe", files={"audio": ("recording.wav", wav())}
    )
    assert response.status_code == status
    assert response.json()["code"] == code
    assert "private-asr-key" not in response.text


def test_capabilities_and_unconfigured_service(application):
    application.state.runtime = SimpleNamespace(capabilities=lambda: {"storage": False})
    client = TestClient(application)
    response = client.get("/api/v1/capabilities")
    assert response.json()["speech"]["available"] is True
    assert "private-asr-key" not in response.text
    application.state.speech = SpeechRecognition(
        Settings(_env_file=None, llm_api_key="", asr_model="configured-asr-model")
    )
    assert client.get("/api/v1/capabilities").json()["speech"]["available"] is False
    response = client.post("/api/v1/speech/transcribe", files={"audio": ("x.wav", wav())})
    assert response.status_code == 503
    assert response.json()["code"] == "asr_unconfigured"


def test_maximum_duration_is_accepted(application):
    client = use_provider(application, lambda request: httpx.Response(
        200, json={"output": {"text": "边界录音"}}
    ))
    response = client.post("/api/v1/speech/transcribe", files={
        "audio": ("recording.wav", wav(SAMPLE_RATE * 120))
    })
    assert response.status_code == 200


@pytest.mark.parametrize("model", ["", "   "])
def test_missing_asr_model_does_not_fall_back_to_chat_model(model):
    settings = Settings(
        _env_file=None, llm_api_key="shared-key", llm_model="chat-model", asr_model=model
    )
    assert not SpeechRecognition(settings).available


def test_asr_model_is_loaded_from_env_file(tmp_path, monkeypatch):
    for name in ("ZHICHENGAPP_ASR_MODEL", "ZHICHENGAPP_LLM_MODEL", "ZHICHENGAPP_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ZHICHENGAPP_LLM_MODEL='qwen3.8-flash'\n"
        "ZHICHENGAPP_LLM_API_KEY='shared-key'\n"
        "ZHICHENGAPP_ASR_MODEL='custom-asr-model'\n", encoding="utf-8"
    )
    settings = Settings(_env_file=env_file)
    assert settings.asr_model == "custom-asr-model"
    assert settings.llm_model == "qwen3.8-flash"
    assert SpeechRecognition(settings).available
