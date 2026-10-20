import logging
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_context.get()
        return True


def configure_logging(
    level: str,
    log_file: Path | None = None,
    *,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> None:
    """配置应用日志；重复调用时替换自有 handler，避免重复写入和文件句柄泄漏。"""
    logger = logging.getLogger("zhichengapp")
    logger.setLevel(level)
    logger.propagate = False
    server_logger = logging.getLogger("uvicorn.error")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
            )
        )
    old_handlers = {
        handler
        for target in (logger, server_logger)
        for handler in target.handlers
        if handler.name == "zhichengapp.logging"
    }
    for target in (logger, server_logger):
        for handler in old_handlers:
            target.removeHandler(handler)
    for handler in old_handlers:
        handler.close()
    for handler in handlers:
        handler.set_name("zhichengapp.logging")
        handler.setLevel(level)
        handler.addFilter(RequestIdFilter())
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s"
            )
        )
        logger.addHandler(handler)
        if isinstance(handler, RotatingFileHandler):
            server_logger.addHandler(handler)
