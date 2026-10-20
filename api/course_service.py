from fastapi import APIRouter, Depends, Query

from api.dependencies import runtime
from common.errors import BusinessError
from schemas.api import CourseCreate

router = APIRouter(prefix="/api/v1/courses", tags=["courses"])


@router.get("", summary="查询课程列表")
async def courses(
    search: str = Query("", max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    rt=Depends(runtime),
):
    """按关键词搜索课程，支持分页；返回手动创建与导入识别的课程。"""
    return {"items": await rt.catalog.courses(search, limit, offset)}


@router.post("", status_code=201, summary="创建课程")
async def create_course(body: CourseCreate, rt=Depends(runtime)):
    """手动创建课程；课程编码已存在时返回 409。"""
    if await rt.catalog.active_rows("courses", {"course_id": body.course_id}, limit=1):
        raise BusinessError("course_exists", "课程编码已存在", 409)
    record = {"id": f"manual:{body.course_id}", **body.model_dump(), "manual": True}
    await rt.repo.put("courses", record)
    return record


@router.get("/series", summary="查询课程系列列表")
async def series(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    rt=Depends(runtime),
):
    """分页查询课程系列，仅返回活动状态的记录。"""
    return {"items": await rt.catalog.active_rows("series", limit=limit, offset=offset)}


@router.get("/{course_id}", summary="查询课程详情")
async def course(course_id: str, rt=Depends(runtime)):
    """查询单个课程的详情，包括课程编码、名称与活动版本信息。"""
    return await rt.catalog.course(course_id)


@router.get("/{course_id}/sections", summary="查询课程章节")
async def sections(course_id: str, rt=Depends(runtime)):
    """查询课程下活动版本的章节树，保留父子层级与原文位置。"""
    await rt.catalog.course(course_id)
    return {"items": await rt.catalog.sections(course_id)}
