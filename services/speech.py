"""短录音转文字：音频仅在内存中处理，凭据与供应商错误不返回浏览器。"""

import base64
import io
import wave

import httpx

from common.errors import BusinessError

MAX_SECONDS = 120
SAMPLE_RATE = 16000
MAX_AUDIO_BYTES = SAMPLE_RATE * 2 * MAX_SECONDS + 4096


class SpeechRecognition:
    def __init__(self, settings, *, transport=None):
        self.settings = settings
        self.transport = transport

    @property
    def available(self):
        return bool(self.settings.asr_api_key.get_secret_value() and self.settings.asr_url)

    def capabilities(self):
        return {"available": self.available, "max_seconds": MAX_SECONDS,
                "max_audio_bytes": MAX_AUDIO_BYTES}

    async def transcribe(self, audio: bytes) -> str:
        if not self.available:
            raise BusinessError("asr_unconfigured", "语音识别尚未配置，请先配置语音服务密钥", 503)
        if len(audio) > MAX_AUDIO_BYTES:
            raise BusinessError("audio_too_large", "录音过大，请控制在 2 分钟以内", 413)
        try:
            with wave.open(io.BytesIO(audio), "rb") as recording:
                frames = recording.getnframes()
                if (recording.getnchannels() != 1 or recording.getsampwidth() != 2
                        or recording.getframerate() != SAMPLE_RATE
                        or recording.getcomptype() != "NONE"):
                    raise ValueError("unsupported PCM")
                if not 0 < frames <= SAMPLE_RATE * MAX_SECONDS:
                    raise ValueError("invalid duration")
                if len(recording.readframes(frames)) != frames * 2:
                    raise ValueError("truncated audio")
        except (wave.Error, EOFError, ValueError) as exc:
            raise BusinessError(
                "invalid_audio", "请提供 2 分钟以内、16 kHz 单声道的 PCM WAV 录音", 422
            ) from exc
        data_uri = "data:audio/wav;base64," + base64.b64encode(audio).decode("ascii")
        payload = {
            "model": self.settings.asr_model,
            "input": {"messages": [{"role": "user", "content": [
                {"type": "input_audio", "input_audio": {"data": data_uri}}
            ]}]},
            "parameters": {"format": "wav", "sample_rate": str(SAMPLE_RATE)},
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.asr_timeout_seconds, transport=self.transport
            ) as client:
                response = await client.post(
                    self.settings.asr_url,
                    headers={
                        "Authorization": "Bearer " + self.settings.asr_api_key.get_secret_value(),
                        "X-DashScope-SSE": "disable",
                    },
                    json=payload,
                )
                response.raise_for_status()
                result = response.json()
            text = result["output"]["text"]
            if not isinstance(text, str):
                raise ValueError("invalid text")
        except httpx.TimeoutException as exc:
            raise BusinessError("asr_timeout", "语音识别超时，请稍后重试", 504) from exc
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise BusinessError(
                "asr_failed", "语音识别失败，请检查服务配置或稍后重试", 502
            ) from exc
        if not text.strip():
            raise BusinessError("asr_empty", "未识别到文字，请靠近麦克风后重试", 422)
        return text.strip()
