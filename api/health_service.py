from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/health/live", summary="存活探针")
async def live():
    """进程存活检查，不探测任何依赖，始终返回 200。"""
    return {"status": "alive"}


@router.get("/health/ready", summary="就绪探针")
async def ready(request: Request):
    """同时报告基础设施与业务能力；连接成功不等于模型和工作流已就绪。"""
    result = await request.app.state.infrastructure.readiness()
    runtime = request.app.state.runtime
    from schemas.health import DependencyStatus

    result.dependencies["business"] = DependencyStatus(
        status="ready" if runtime.storage_ready else "unavailable",
        message=runtime.error or "任务与业务存储已初始化",
    )
    result.dependencies["models"] = DependencyStatus(
        status="ready" if not runtime.models.error else "unavailable",
        message=runtime.models.error
        or f"Embedding 与 Reranker 已加载（{runtime.settings.model_device}）",
    )
    result.dependencies["llm"] = DependencyStatus(
        status="ready" if runtime.llm.client is not None else "unconfigured",
        message="客户端已配置；健康检查不调用付费生成接口"
        if runtime.llm.client is not None
        else "尚未配置完整 LLM 连接信息",
    )
    result.status = (
        "ready"
        if all(dependency.status == "ready" for dependency in result.dependencies.values())
        else "degraded"
    )
    return JSONResponse(
        status_code=200 if result.status == "ready" else 503,
        content=result.model_dump(),
    )
