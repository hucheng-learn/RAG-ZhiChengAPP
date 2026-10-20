"""应用入口：组装 FastAPI 应用，包括生命周期管理、中间件、异常处理与路由注册。"""

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from api.course_service import router as course_router
from api.file_import_service import router as import_router
from api.health_service import router as health_router
from api.query_service import router as query_router
from api.question_service import router as question_router
from api.session_service import router as session_router
from api.speech_service import router as speech_router
from common.config import Settings
from common.errors import BusinessError
from common.logging import configure_logging, request_id_context
from services.runtime import Runtime
from services.speech import SpeechRecognition
from utils.clients.infrastructure import Infrastructure

# 应用统一的日志记录器
logger = logging.getLogger("zhichengapp")
# 项目根目录，用于定位静态页面等资源
ROOT = Path(__file__).resolve().parent


def create_app(settings: Settings | None = None, *, runtime_factory=Runtime) -> FastAPI:
    """组合路由与资源生命周期；工厂注入只用于隔离测试中的外部依赖。"""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        # 应用启动时：初始化日志、基础设施（数据库/消息队列等）与运行时服务
        configure_logging(
            settings.log_level,
            settings.log_file,
            max_bytes=settings.log_max_bytes,
            backup_count=settings.log_backup_count,
        )
        # 生命周期内管理客户端。健康检查只读，不自动创建数据库、桶或集合。
        infrastructure = Infrastructure(settings)
        application.state.infrastructure = infrastructure
        runtime = None
        try:
            await infrastructure.start()
            # 通过工厂创建运行时，便于测试时替换为 mock 实现
            runtime = runtime_factory(settings, infrastructure)
            application.state.runtime = runtime
            # 包含创建消费者，services.imports.ImportService.start
            await runtime.start()
            logger.info("application_started")
            # yield 之前为启动逻辑，之后为请求服务阶段
            yield
        except Exception:
            logger.exception("application_failed")
            raise
        finally:
            # 应用关闭时按相反顺序释放资源
            if runtime:
                await runtime.close()
            await infrastructure.close()
            logger.info("application_stopped")

    application = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

    # 语音识别服务独立于 runtime，直接挂载到 app.state
    application.state.speech = SpeechRecognition(settings)

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        # 请求上下文中间件：为每个请求分配唯一的 request_id，贯穿日志与响应
        provided = request.headers.get("X-Request-ID", "")
        # 校验外部传入的 request_id，格式非法则重新生成
        request_id = provided if re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", provided) else uuid4().hex
        request.state.request_id = request_id
        # 绑定到 contextvars，使日志中能记录当前请求的 request_id
        token = request_id_context.set(request_id)
        started = perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                # 兜底异常处理：未捕获的异常统一转为 500 响应
                logger.exception(
                    "request_failed method=%s path=%s", request.method, request.url.path
                )
                response = JSONResponse(
                    status_code=500,
                    content={
                        "code": "internal_error",
                        "message": "服务内部错误",
                        "request_id": request_id,
                    },
                )
            # 将 request_id 写回响应头，便于前后端问题排查
            response.headers["X-Request-ID"] = request_id
            # 流式响应此处记录响应头就绪时间，流内错误由生成器单独记录。
            log = logger.warning if response.status_code >= 400 else logger.info
            log(
                "request_completed method=%s path=%s status=%s duration_ms=%.2f",
                request.method, request.url.path, response.status_code,
                (perf_counter() - started) * 1000,
            )
            return response
        finally:
            # 还原上下文，避免协程间串号
            request_id_context.reset(token)

    # 全局异常处理器：统一错误响应格式 {code, message, request_id}

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        # HTTP 异常（如 404/401）
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={
                "code": f"http_{exc.status_code}",
                "message": str(exc.detail),
                "request_id": request.state.request_id,
            },
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # 请求参数校验失败（Pydantic 模型校验）
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "message": "请求参数格式不正确",
                "request_id": request.state.request_id,
            },
        )

    @application.exception_handler(BusinessError)
    async def business_error(request: Request, exc: BusinessError):
        # 业务异常：由各业务服务主动抛出，携带状态码与错误码
        return JSONResponse(
            status_code=exc.status,
            content={
                "code": exc.code,
                "message": exc.message,
                "request_id": request.state.request_id,
            },
        )

    # 注册业务路由
    application.include_router(health_router)
    for router in (
        import_router, course_router, question_router, session_router, query_router, speech_router
    ):
        application.include_router(router)
    # 静态资源（前端页面）挂载到 /assets
    application.mount("/assets", StaticFiles(directory=ROOT / "page"), name="assets")

    @application.get("/api/v1/capabilities", tags=["health"], summary="查询服务能力")
    async def capabilities(request: Request):
        """汇总运行时与语音识别服务的能力信息，供客户端探测。"""
        return {**request.app.state.runtime.capabilities(),
                "speech": request.app.state.speech.capabilities()}

    @application.get("/", include_in_schema=False)
    async def index():
        # 首页：返回前端页面
        return FileResponse(ROOT / "page" / "index.html")

    return application


# 模块级应用实例，供 uvicorn 等服务器加载
app = create_app()

if __name__ == "__main__":
    # 本地调试入口，生产环境建议直接用 uvicorn 启动
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8010)
