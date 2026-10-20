"""生命周期内管理客户端。健康检查只读，不自动创建数据库、桶或集合。"""

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import urllib3
from minio import Minio
from pymilvus import MilvusClient
from pymongo import MongoClient

from common.config import Settings
from schemas.health import DependencyStatus, Readiness

logger = logging.getLogger("zhichengapp")


class Infrastructure:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.mongo: MongoClient | None = None
        self.milvus: MilvusClient | None = None
        self.minio: Minio | None = None
        self.http = urllib3.PoolManager(
            timeout=urllib3.Timeout(total=settings.dependency_timeout_seconds), retries=False
        )
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="zhichengapp-io")
        self.lock = asyncio.Lock()
        self.initial_errors: set[str] = set()

    async def start(self) -> None:
        # 客户端构造也可能执行同步连接操作，放入有界线程池。
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self.pool, self._start)

    def _start(self) -> None:
        s = self.settings
        factories = {}
        if s.mongo_uri.get_secret_value():
            factories["mongo"] = lambda: MongoClient(
                s.mongo_uri.get_secret_value(),
                serverSelectionTimeoutMS=int(s.dependency_timeout_seconds * 1000),
                connectTimeoutMS=int(s.dependency_timeout_seconds * 1000),
                socketTimeoutMS=int(s.dependency_timeout_seconds * 1000),
                connect=False,
            )
        if s.milvus_uri:
            factories["milvus"] = lambda: MilvusClient(
                uri=s.milvus_uri,
                token=s.milvus_token.get_secret_value(),
                timeout=s.dependency_timeout_seconds,
            )
        if (
            s.minio_endpoint
            and s.minio_access_key.get_secret_value()
            and s.minio_secret_key.get_secret_value()
        ):
            factories["minio"] = lambda: Minio(
                s.minio_endpoint,
                access_key=s.minio_access_key.get_secret_value(),
                secret_key=s.minio_secret_key.get_secret_value(),
                secure=s.minio_secure,
                http_client=self.http,
            )
        for name, factory in factories.items():
            try:
                setattr(self, name, factory())
            except Exception:
                self.initial_errors.add(name)
                logger.warning("dependency=%s initialization_failed", name)

    def _probe(self, name: str) -> DependencyStatus:
        s = self.settings
        client = getattr(self, name)
        if client is None:
            failed = name in self.initial_errors
            return DependencyStatus(
                status="unavailable" if failed else "unconfigured",
                message="客户端初始化失败，请核对配置并重启" if failed else "尚未配置完整连接信息",
            )
        try:
            if name == "mongo":
                client[s.mongo_database].command("ping")
                message = f"已连接，业务数据库：{s.mongo_database}（未检查集合）"
            elif name == "milvus":
                version = client.get_server_version(timeout=s.dependency_timeout_seconds)
                if not re.fullmatch(r"(?:pkg/)?v?2\.5\.\d+(?:[-+].*)?", version):
                    return DependencyStatus(
                        status="unavailable", message="服务器不是 2.5.x，请先验证 SDK 兼容性"
                    )
                exists = client.has_collection(
                    s.milvus_collection, timeout=s.dependency_timeout_seconds
                )
                return DependencyStatus(
                    status="ready" if exists else "pending",
                    message=f"Milvus {version}；集合" + ("已存在" if exists else "尚未初始化"),
                )
            else:
                exists = client.bucket_exists(s.minio_bucket)
                return DependencyStatus(
                    status="ready" if exists else "pending",
                    message="业务桶已存在" if exists else "业务桶尚未初始化",
                )
            return DependencyStatus(status="ready", message=message)
        except Exception:
            # 不向页面或日志输出可能包含认证信息的 SDK 异常文本。
            logger.warning("dependency=%s probe_failed", name)
            return DependencyStatus(
                status="unavailable", message="连接或鉴权失败，请核对服务与配置"
            )

    async def readiness(self) -> Readiness:
        # 串行化整组检查，避免多个轮询请求耗尽线程池。
        async with self.lock:
            loop = asyncio.get_running_loop()
            names = ("mongo", "milvus", "minio")
            values = await asyncio.gather(
                *(loop.run_in_executor(self.pool, self._probe, name) for name in names)
            )
        return Readiness(
            status="ready" if all(item.status == "ready" for item in values) else "degraded",
            dependencies=dict(zip(names, values, strict=True)),
        )

    async def close(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self.pool, self._close)
        self.pool.shutdown(wait=True, cancel_futures=True)

    def _close(self) -> None:
        for client in (self.mongo, self.milvus):
            if client is not None:
                try:
                    client.close()
                except Exception:
                    logger.warning("client_close_failed")
        self.http.clear()
