"""题目服务：提供题目的查询、筛选与答案查看接口。"""

from fastapi import APIRouter, Depends, Query

from api.dependencies import runtime

router = APIRouter(prefix="/api/v1/questions", tags=["questions"])


@router.get("", summary="查询题目列表")
async def questions(
    course_id: str | None = None,
    code: str | None = None,
    question_type: str | None = None,
    knowledge_point: str | None = None,
    limit: int = Query(10, ge=1, le=200),
    offset: int = Query(0, ge=0),
    rt=Depends(runtime),
):
    """分页查询题目列表，支持按课程、编号、题型、知识点过滤。"""
    rows = await rt.catalog.questions(
        course_id, code, question_type, knowledge_point, limit + 1, offset
    )
    # 多取一条用于判断是否还有下一页，返回前裁剪掉
    return {"items": rows[:limit], "has_more": len(rows) > limit}


@router.get("/{question_id}/solution", summary="查看题目答案与解析")
async def solution(question_id: str, rt=Depends(runtime)):
    """独立端点仅在学员主动请求时提供答案，练习列表永远不包含这些字段。"""
    row = await rt.catalog.question(question_id, solution=True)
    return {
        "question_id": row["question_id"],
        "images": row.get("solution_images", []),
        "answer": row["answer"],
        "explanation": row["explanation"],
        "source": row["source"],
        "review_status": row["review_status"],
    }


@router.get("/{question_id}", summary="查询题目详情")
async def question(question_id: str, rt=Depends(runtime)):
    """查询单个题目详情（不含答案字段）。"""
    return await rt.catalog.question(question_id)
