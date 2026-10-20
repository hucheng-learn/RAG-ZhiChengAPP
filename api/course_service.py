from fastapi import APIRouter, Depends, Query

from api.dependencies import runtime
from common.errors import BusinessError
from schemas.api import CourseCreate

router = APIRouter(prefix="/api/v1/courses", tags=["courses"])


@router.get("")
async def courses(
    search: str = Query("", max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    rt=Depends(runtime),
):
    return {"items": await rt.catalog.courses(search, limit, offset)}


@router.post("", status_code=201)
async def create_course(body: CourseCreate, rt=Depends(runtime)):
    if await rt.catalog.active_rows("courses", {"course_id": body.course_id}, limit=1):
        raise BusinessError("course_exists", "课程编码已存在", 409)
    record = {"id": f"manual:{body.course_id}", **body.model_dump(), "manual": True}
    await rt.repo.put("courses", record)
    return record


@router.get("/series")
async def series(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    rt=Depends(runtime),
):
    return {"items": await rt.catalog.active_rows("series", limit=limit, offset=offset)}


@router.get("/{course_id}")
async def course(course_id: str, rt=Depends(runtime)):
    return await rt.catalog.course(course_id)


@router.get("/{course_id}/sections")
async def sections(course_id: str, rt=Depends(runtime)):
    await rt.catalog.course(course_id)
    return {"items": await rt.catalog.sections(course_id)}
