from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile

from common.errors import BusinessError
from services.speech import MAX_AUDIO_BYTES

router = APIRouter(prefix="/api/v1/speech", tags=["speech"])


@router.post("/transcribe", summary="语音转文字")
async def transcribe(request: Request, audio: Annotated[UploadFile, File()]):
    """上传录音并返回识别文本；仅限本地语音模型，不依赖课程库或向量服务。"""
    try:
        # 限量读取，避免将超大文件全部载入内存；不依赖课程库或向量服务。
        data = await audio.read(MAX_AUDIO_BYTES + 1)
        if len(data) > MAX_AUDIO_BYTES:
            raise BusinessError("audio_too_large", "录音过大，请控制在 2 分钟以内", 413)
        text = await request.app.state.speech.transcribe(data)
        return {"text": text, "request_id": request.state.request_id}
    finally:
        await audio.close()
