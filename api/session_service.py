from uuid import uuid4

from fastapi import APIRouter, Depends, Query

from api.dependencies import runtime
from common.errors import BusinessError
from schemas.api import SessionCreate
from services.imports import now

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


@router.post("", status_code=201, summary="创建会话")
async def create_session(body: SessionCreate, rt=Depends(runtime)):
    """创建问答会话并关联课程；会话内的提问历史将用于追问改写。"""
    for course_id in body.course_ids:
        await rt.catalog.course(course_id)
    identifier = uuid4().hex
    record = {"id": identifier, "session_id": identifier, **body.model_dump(), "created_at": now()}
    await rt.repo.put("sessions", record)
    return record


@router.get("", summary="查询会话列表")
async def sessions(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), rt=Depends(runtime)
):
    """分页查询会话列表，按创建时间倒序返回。"""
    return {
        "items": await rt.repo.find(
            "sessions", limit=limit, offset=offset, sort=[("created_at", -1)]
        )
    }


@router.get("/{identifier}/messages", summary="查询会话历史消息")
async def messages(
    identifier: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    rt=Depends(runtime),
):
    """按时间正序返回会话消息，并为每条引用补充原件是否仍可访问的状态。"""
    if not await rt.repo.get("sessions", identifier):
        raise BusinessError("session_not_found", "会话不存在", 404)
    rows = await rt.repo.find(
        "messages",
        {"session_id": identifier},
        limit=limit,
        offset=offset,
        sort=[("created_at", -1)],
    )
    for row in rows:
        for citation in row.get("citations", []):
            document = await rt.repo.get("documents", citation["document_id"])
            citation["source_available"] = bool(
                document and document["status"] not in {"deleted", "deleting"}
            )
    return {"items": list(reversed(rows))}


@router.delete("/{identifier}", status_code=204, summary="删除会话")
async def delete_session(identifier: str, rt=Depends(runtime)):
    """与提问使用同一会话占用标记，删除与新请求不会在 await 间隙穿插。"""
    if identifier in rt.query.busy:
        raise BusinessError("session_busy", "会话正在执行提问，请稍后删除", 409)
    rt.query.busy.add(identifier)
    try:
        await rt.repo.delete("messages", {"session_id": identifier})
        await rt.repo.delete("sessions", {"id": identifier})
    finally:
        rt.query.busy.discard(identifier)
