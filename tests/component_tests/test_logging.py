"""验证日志落盘、轮转、重复配置与 HTTP 错误的排查信息。"""

import asyncio
import logging
from logging.handlers import RotatingFileHandler
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.query_service import stream
from app import create_app
from common.config import Settings
from common.logging import configure_logging, request_id_context
from schemas.api import QueryRequest


@pytest.fixture(autouse=True)
def restore_loggers():
    targets = [logging.getLogger(name) for name in ("zhichengapp", "uvicorn.error")]
    saved = [(target.handlers[:], target.level, target.propagate) for target in targets]
    for target in targets:
        target.handlers = []
    yield
    handlers = {handler for target in targets for handler in target.handlers}
    for target, (original, level, propagate) in zip(targets, saved, strict=True):
        target.handlers = original
        target.setLevel(level)
        target.propagate = propagate
    for handler in handlers:
        handler.close()


def test_file_logging_is_utf8_has_request_id_and_respects_level(tmp_path):
    path = tmp_path / "nested" / "app.log"
    configure_logging("INFO", path)
    token = request_id_context.set("request-123")
    try:
        logger = logging.getLogger("zhichengapp")
        logger.debug("debug-hidden")
        logger.info("知成日志")
    finally:
        request_id_context.reset(token)
    logging.getLogger("uvicorn.error").error("server-error")
    content = path.read_text(encoding="utf-8")
    assert "request_id=request-123 知成日志" in content
    assert "request_id=- server-error" in content
    assert "debug-hidden" not in content


def test_reconfiguration_releases_old_file_and_does_not_duplicate_records(tmp_path):
    old_path, new_path = tmp_path / "old.log", tmp_path / "new.log"
    configure_logging("INFO", old_path)
    old_handler = next(
        h for h in logging.getLogger("zhichengapp").handlers
        if isinstance(h, RotatingFileHandler)
    )
    configure_logging("DEBUG", new_path)
    configure_logging("DEBUG", new_path)
    logging.getLogger("zhichengapp").debug("single-record")
    logging.getLogger("uvicorn.error").error("single-server-record")
    assert old_handler.stream is None
    assert old_path.read_text(encoding="utf-8") == ""
    content = new_path.read_text(encoding="utf-8")
    assert content.count("single-record") == 1
    assert content.count("single-server-record") == 1


def test_rotation_retains_only_configured_backups(tmp_path):
    path = tmp_path / "app.log"
    configure_logging("INFO", path, max_bytes=200, backup_count=2)
    for number in range(10):
        logging.getLogger("zhichengapp").info("record-%s %s", number, "x" * 100)
    assert {p.name for p in tmp_path.iterdir()} == {"app.log", "app.log.1", "app.log.2"}
    assert "record-9" in path.read_text(encoding="utf-8")
    assert "record-8" in (tmp_path / "app.log.1").read_text(encoding="utf-8")


def test_requests_and_tracebacks_are_persisted_without_query_or_body(tmp_path):
    path = tmp_path / "app.log"
    settings = Settings(
        _env_file=None, log_file=path, mongo_uri="", milvus_uri="", minio_endpoint="",
        load_local_models=False,
    )
    application = create_app(settings)

    @application.post("/logging-probe")
    async def probe():
        raise RuntimeError("probe-failure")

    with TestClient(application) as client:
        response = client.post(
            "/logging-probe?secret=query-value",
            headers={"X-Request-ID": "probe-123", "Authorization": "Bearer header-value"},
            json={"secret": "body-value"},
        )
        assert response.status_code == 500
        assert "probe-failure" not in response.text
        assert client.get("/health/live").status_code == 200
    content = path.read_text(encoding="utf-8")
    assert "application_started" in content and "application_stopped" in content
    assert "request_id=probe-123 request_failed method=POST path=/logging-probe" in content
    assert "Traceback" in content and "RuntimeError: probe-failure" in content
    assert "status=500 duration_ms=" in content
    assert "method=GET path=/health/live status=200" in content
    assert all(secret not in content for secret in ("query-value", "body-value", "header-value"))


def test_stream_failure_is_logged_even_after_http_response_started(tmp_path):
    path = tmp_path / "app.log"
    configure_logging("INFO", path)
    query = SimpleNamespace(
        acquire=AsyncMock(return_value={"request_id": "stream-123", "message_id": "message-1"}),
        execute=AsyncMock(side_effect=RuntimeError("stream-probe-failure")),
        finish=AsyncMock(),
    )

    async def consume():
        response = await stream(
            QueryRequest(session_id="session-1", question="日志测试"),
            SimpleNamespace(state=SimpleNamespace(request_id="stream-123")),
            SimpleNamespace(query=query),
        )
        assert response.status_code == 200
        return "".join([chunk async for chunk in response.body_iterator])

    events = asyncio.run(consume())
    assert "event: error" in events and "stream-probe-failure" not in events
    query.finish.assert_awaited_once()
    content = path.read_text(encoding="utf-8")
    assert "query_stream_failed request_id=stream-123" in content
    assert "RuntimeError: stream-probe-failure" in content
