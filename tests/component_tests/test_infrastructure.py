"""检查基础服务就绪状态、连接关闭及故障信息脱敏。"""

import asyncio
from unittest.mock import MagicMock

from common.config import Settings
from utils.clients.infrastructure import Infrastructure


def infrastructure():
    return Infrastructure(Settings(_env_file=None, mongo_uri="", milvus_uri="", minio_endpoint=""))


def test_readiness_is_read_only_and_closes_clients():
    async def run():
        clients = infrastructure()
        clients.mongo = MagicMock()
        clients.milvus = MagicMock()
        clients.minio = MagicMock()
        clients.milvus.get_server_version.return_value = "pkg/v2.5.5"
        clients.milvus.has_collection.return_value = True
        clients.minio.bucket_exists.return_value = True
        result = await clients.readiness()
        assert result.status == "ready"
        clients.mongo.__getitem__.assert_called_once_with("zhichengapp")
        clients.mongo.__getitem__.return_value.command.assert_called_once_with("ping")
        clients.milvus.has_collection.assert_called_once_with("zhichengapp_chunks", timeout=3)
        clients.minio.bucket_exists.assert_called_once_with("zhichengapp-files")
        clients.milvus.create_collection.assert_not_called()
        clients.minio.make_bucket.assert_not_called()
        await clients.close()
        clients.mongo.close.assert_called_once()
        clients.milvus.close.assert_called_once()

    asyncio.run(run())


def test_incompatible_milvus_missing_bucket_and_safe_errors():
    async def run():
        clients = infrastructure()
        clients.mongo = MagicMock()
        clients.mongo.__getitem__.return_value.command.side_effect = RuntimeError("secret-token")
        clients.milvus = MagicMock()
        clients.milvus.get_server_version.return_value = "2.6.0"
        clients.minio = MagicMock()
        clients.minio.bucket_exists.return_value = False
        try:
            result = await clients.readiness()
            assert result.status == "degraded"
            assert result.dependencies["mongo"].status == "unavailable"
            assert result.dependencies["milvus"].status == "unavailable"
            assert result.dependencies["minio"].status == "pending"
            assert "secret-token" not in result.model_dump_json()
        finally:
            await clients.close()

    asyncio.run(run())
