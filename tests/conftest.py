"""pytest 自动加载的共享测试配置；system 提供隔离的应用与运行时。"""

import mongomock
import pytest
from fastapi.testclient import TestClient

from app import create_app
from common.config import Settings
from repositories.mongo import MongoRepository
from services.runtime import Runtime
from tests.fakes import FakeLLM, FakeModels, FakeObjects, FakeVectors


@pytest.fixture
def system():
    """返回 (HTTP 测试客户端, Runtime)，用内存替身代替数据库、存储和模型。"""
    # 禁用 .env 与外部连接，避免测试读取本机服务配置。
    settings = Settings(
        _env_file=None,
        mongo_uri="",
        milvus_uri="",
        minio_endpoint="",
        llm_base_url="",
        mineru_api_token="",
        chunk_target_tokens=192,
        chunk_max_tokens=256,
        chunk_overlap_tokens=16,
    )
    captured = {}

    def factory(settings, infrastructure):
        runtime = Runtime(
            settings,
            infrastructure,
            repo=MongoRepository(mongomock.MongoClient(), "zhichengapp", infrastructure.pool),
            blobs=FakeObjects(),
            vectors=FakeVectors(),
            models=FakeModels(),
            llm=FakeLLM(),
        )
        captured["runtime"] = runtime
        return runtime

    # 上下文管理器负责启动/关闭应用；yield 后 pytest 会继续执行清理。
    with TestClient(create_app(settings, runtime_factory=factory)) as client:
        yield client, captured["runtime"]
