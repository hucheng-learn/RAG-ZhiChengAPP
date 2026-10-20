"""检查应用启动/关闭、健康接口、配置校验与错误信息脱敏。"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import create_app
from common.config import Settings


@pytest.fixture
def settings():
    return Settings(_env_file=None, mongo_uri="", milvus_uri="", minio_endpoint="")


def test_health_lifecycle_and_missing_dependencies(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").json() == {"status": "alive"}
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert all(
            response.json()["dependencies"][name]["status"] == "unconfigured"
            for name in ("mongo", "milvus", "minio")
        )
        assert client.get("/").status_code == 200


def test_errors_have_safe_request_id(settings):
    with TestClient(create_app(settings)) as client:
        response = client.get("/missing", headers={"X-Request-ID": "test-123"})
        assert response.status_code == 404
        assert response.json()["request_id"] == response.headers["X-Request-ID"] == "test-123"
        response = client.get("/missing", headers={"X-Request-ID": "invalid request id"})
        assert len(response.headers["X-Request-ID"]) == 32


def test_validation_and_internal_errors_do_not_leak_input(settings):
    application = create_app(settings)

    @application.get("/probe")
    async def probe(count: int):
        raise RuntimeError("sensitive-connection-string")

    with TestClient(application) as client:
        invalid = client.get("/probe?count=secret-input")
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "validation_error"
        assert "secret-input" not in invalid.text
        failed = client.get("/probe?count=1")
        assert failed.status_code == 500
        assert failed.json()["code"] == "internal_error"
        assert "sensitive-connection-string" not in failed.text
        assert failed.json()["request_id"] == failed.headers["X-Request-ID"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("mongo_database", "old-project"),
        ("milvus_collection", "old_chunks"),
        ("minio_bucket", "old-files"),
        ("chunk_target_tokens", 20),
        ("minio_endpoint", "http://localhost:9000"),
    ],
)
def test_settings_reject_unsafe_namespaces_and_invalid_limits(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})
