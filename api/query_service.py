import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from api.dependencies import runtime
from common.errors import BusinessError
from schemas.api import QueryRequest

router = APIRouter(prefix="/api/v1/query", tags=["query"])
logger = logging.getLogger("zhichengapp")


@router.post("", summary="同步课程问答")
async def query(body: QueryRequest, request: Request, rt=Depends(runtime)):
    """阻塞式问答：检索课程证据并返回带引用的最终回答；适合脚本与评测调用。"""
    run = await rt.query.acquire(body, request.state.request_id)
    try:
        result = await rt.query.execute(run)
    except BaseException as exc:
        await rt.query.finish(
            run, status="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
        )
        raise
    await rt.query.finish(run, result)
    return result


def event(name, request_id, **data):
    payload = json.dumps({"request_id": request_id, **data}, ensure_ascii=False)
    return f"event: {name}\ndata: {payload}\n\n"


@router.post("/stream", summary="流式课程问答（SSE）")
async def stream(body: QueryRequest, request: Request, rt=Depends(runtime)):
    """模型增量作为待核对草稿输出；done 用已校验结果替换草稿。"""
    run = await rt.query.acquire(body, request.state.request_id)
    request_id = run["request_id"]

    async def generate():
        operation = None
        pending = None
        terminal = False
        deltas = asyncio.Queue(maxsize=32)
        run["emit"] = deltas.put

        async def emit_status(message):
            run["stage_message"] = message
            await deltas.put({"message": message})

        run["emit_status"] = emit_status
        try:
            yield event(
                "meta", request_id, session_id=body.session_id, message_id=run["message_id"]
            )
            yield event("status", request_id, message="正在检索并核对课程证据")
            operation = asyncio.create_task(rt.query.execute(run))
            while not operation.done() or not deltas.empty():
                pending = asyncio.create_task(deltas.get())
                done, _ = await asyncio.wait(
                    {operation, pending}, timeout=10, return_when=asyncio.FIRST_COMPLETED
                )
                if pending in done:
                    value = pending.result()
                    if isinstance(value, dict):
                        yield event("status", request_id, **value)
                    else:
                        yield event("token", request_id, text=value, provisional=True)
                else:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                pending = None
                if not done:
                    yield event(
                        "status",
                        request_id,
                        message=run.get("stage_message", "正在检索并核对课程证据") + "，请稍候",
                    )
            result = await operation
            yield event("citations", request_id, citations=result["citations"])
            await rt.query.finish(run, result)
            terminal = True
            yield event("done", request_id, result=result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if isinstance(exc, BusinessError):
                logger.warning("query_stream_failed request_id=%s code=%s", request_id, exc.code)
            else:
                logger.exception("query_stream_failed request_id=%s", request_id)
            await rt.query.finish(run, status="failed")
            terminal = True
            yield event(
                "error",
                request_id,
                code=exc.code if isinstance(exc, BusinessError) else "query_failed",
                message=exc.message if isinstance(exc, BusinessError) else "问答失败，请稍后重试",
            )
        finally:
            if pending:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            if operation and not operation.done():
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
            if not terminal:
                await rt.query.finish(run, status="cancelled")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
