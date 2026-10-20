import logging

from common.errors import BusinessError
from repositories.mongo import MongoRepository
from services.catalog import CatalogService
from services.imports import ImportService, now
from services.query import QueryService
from utils.clients.mineru import MinerUClient
from utils.clients.object_store import ObjectStore
from utils.clients.vector_store import VectorStore
from utils.llm.models import LanguageModel, LocalModels
from utils.parsers.files import SUPPORTED_EXTENSIONS

logger = logging.getLogger("zhichengapp")


class Runtime:
    """协调依赖和业务生命周期；依赖不完整时保留健康页面，业务 API 返回明确 503。"""

    def __init__(
        self,
        settings,
        infrastructure,
        *,
        repo=None,
        blobs=None,
        vectors=None,
        models=None,
        llm=None,
    ):
        self.settings, self.infrastructure = settings, infrastructure
        self.repo = repo or (
            MongoRepository(infrastructure.mongo, settings.mongo_database, infrastructure.pool)
            if infrastructure.mongo is not None
            else None
        )
        self.blobs = blobs or (
            ObjectStore(infrastructure.minio, settings.minio_bucket, infrastructure.pool)
            if infrastructure.minio
            else None
        )
        self.vectors = vectors or (
            VectorStore(infrastructure.milvus, settings.milvus_collection, infrastructure.pool)
            if infrastructure.milvus
            else None
        )
        self.models = models or LocalModels(settings, infrastructure.pool)
        self.llm = llm or LanguageModel(settings)
        self.mineru = MinerUClient(settings)
        self.catalog = CatalogService(self.repo) if self.repo else None
        self.imports = self.query = None
        self.error = "业务依赖未配置或不可用"
        self.storage_ready = False
        self.vector_ready = False

    async def start(self):
        await self.models.start()
        if self.repo is None or self.blobs is None or self.vectors is None:
            return
        try:
            await self.repo.initialize()
            await self.blobs.initialize()
            if not self.models.error:
                await self.vectors.initialize(self.models.dimension)
                self.vector_ready = True
            self.imports = ImportService(self)
            self.query = QueryService(self)
            # 未正常结束的消息不进入下一轮上下文。
            await self.repo.update_many(
                "messages", {"status": "processing"}, {"status": "cancelled", "finished_at": now()}
            )
            await self.imports.start()
            self.storage_ready = True
            self.error = ""
        except Exception as exc:
            logger.warning("business_start_failed type=%s", type(exc).__name__)
            self.error = "业务资源初始化失败，请检查基础服务后重启应用"

    def require_storage(self):
        if not self.storage_ready:
            raise BusinessError("storage_unavailable", self.error, 503)

    def require_models(self):
        self.require_storage()
        self.models.require()
        if not self.vector_ready:
            raise BusinessError("vector_unavailable", "向量集合尚未就绪", 503)

    async def close(self):
        """先停止任务，再关闭模型和 HTTP；数据库由外层 lifespan 最后关闭。"""
        if self.imports:
            await self.imports.close()
        await self.models.close()
        await self.llm.close()

    def capabilities(self):
        return {
            "upload_limits": {
                "max_file_bytes": self.settings.max_upload_bytes,
                "max_docx_unpacked_bytes": self.settings.max_docx_unpacked_bytes,
                "max_batch_files": self.settings.max_batch_files,
                "max_batch_bytes": self.settings.max_batch_bytes,
                "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
                "mineru": self.mineru.available,
                "available_queue_slots": (
                    self.imports.queue.maxsize - self.imports.queue.qsize() if self.imports else 0
                ),
            },
            "model_device": self.settings.model_device,
            "storage": self.storage_ready,
            "import": self.vector_ready and not self.models.error,
            "models": not self.models.error,
            "llm": self.llm.client is not None,
            "query": self.vector_ready and not self.models.error and self.llm.client is not None,
            "model_message": self.models.error,
            "message": self.error,
        }
