from fastapi import APIRouter, Depends, Query

from api.dependencies import runtime

router = APIRouter(prefix="/api/v1/questions", tags=["questions"])


@router.get("")
async def questions(
    course_id: str | None = None,
    code: str | None = None,
    question_type: str | None = None,
    knowledge_point: str | None = None,
    limit: int = Query(10, ge=1, le=200),
    offset: int = Query(0, ge=0),
    rt=Depends(runtime),
):
    rows = await rt.catalog.questions(
        course_id, code, question_type, knowledge_point, limit + 1, offset
    )
    return {"items": rows[:limit], "has_more": len(rows) > limit}


@router.get("/{question_id}/solution")
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


@router.get("/{question_id}")
async def question(question_id: str, rt=Depends(runtime)):
    return await rt.catalog.question(question_id)
