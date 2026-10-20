import asyncio
import logging
from functools import partial

from common.errors import BusinessError

logger = logging.getLogger("zhichengapp")


class LocalModels:
    """本地模型适配器；导入依赖和加载权重均在生命周期内，绝不自动下载。"""

    def __init__(self, settings, executor):
        self.settings, self.executor = settings, executor
        self.embedding = self.reranker = self.tokenizer = None
        self.dimension = 1024
        self.semaphore = asyncio.Semaphore(settings.model_concurrency)
        self.error = "尚未启用本地模型"

    async def call(self, operation, *args, **kwargs):
        # 调用取消时仍等待工作线程完成，防止释放信号量后超额占用 GPU。
        async with self.semaphore:
            future = asyncio.get_running_loop().run_in_executor(
                self.executor, partial(operation, *args, **kwargs)
            )
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                await future
                raise

    async def start(self):
        if not self.settings.load_local_models:
            return
        try:
            await self.call(self._load)
            self.error = ""
        except BusinessError as exc:
            self.error = exc.message
            logger.warning("local_models_load_failed code=%s", exc.code)
        except Exception as exc:
            self.error = f"本地模型加载失败（{type(exc).__name__}），请检查 extra、路径和设备"
            logger.warning("local_models_load_failed type=%s", type(exc).__name__)

    def _load(self):
        """显式请求 CUDA 时先验证设备，不允许模型框架静默回退到 CPU。"""
        import torch
        from FlagEmbedding import BGEM3FlagModel, FlagReranker
        from transformers import AutoTokenizer

        s = self.settings
        if s.model_device.startswith("cuda"):
            if not torch.cuda.is_available():
                raise BusinessError(
                    "cuda_unavailable", "CUDA 不可用，请检查 PyTorch CUDA wheel 与显卡驱动", 503
                )
            device = torch.device(s.model_device)
            if device.index is not None and device.index >= torch.cuda.device_count():
                raise ValueError("指定的 CUDA 设备不存在")
            # 提前执行 CUDA 运算，尽早暴露驱动/架构不匹配，避免导入开始后才失败。
            try:
                torch.zeros(1, device=device).add_(1)
            except RuntimeError as exc:
                raise BusinessError(
                    "cuda_execution_failed",
                    "CUDA 运算失败，请检查显卡驱动与 PyTorch 架构兼容性",
                    503,
                ) from exc
        for path in (s.embedding_model_path, s.reranker_model_path):
            if path is None or not path.is_dir():
                raise ValueError("本地模型目录不存在")
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(s.embedding_model_path), local_files_only=True, trust_remote_code=False
        )
        self.embedding = BGEM3FlagModel(
            str(s.embedding_model_path),
            devices=[s.model_device],
            use_fp16=s.model_device.startswith("cuda"),
        )
        self.reranker = FlagReranker(
            str(s.reranker_model_path),
            devices=[s.model_device],
            use_fp16=s.model_device.startswith("cuda"),
        )

    def require(self):
        if self.error:
            raise BusinessError("models_unavailable", self.error, 503)

    async def encode(self, texts):
        """返回 BGE-M3 稠密和学习型稀疏向量；稀疏权重不是 BM25。"""
        self.require()
        result = await self.call(
            self.embedding.encode,
            texts,
            batch_size=self.settings.model_batch_size,
            max_length=self.settings.chunk_max_tokens,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )
        return [list(map(float, row)) for row in result["dense_vecs"]], [
            {int(key): float(value) for key, value in row.items() if float(value) > 0}
            for row in result["lexical_weights"]
        ]

    async def rerank(self, question, texts):
        self.require()
        result = await self.call(
            self.reranker.compute_score,
            [[question, text] for text in texts],
            normalize=True,
            batch_size=self.settings.model_batch_size,
        )
        return [float(result)] if isinstance(result, (int, float)) else list(map(float, result))

    async def close(self):
        """消费者停机后解除权重引用；CUDA 缓存释放不代表外部调用可即时取消。"""
        self.embedding = self.reranker = self.tokenizer = None


class LanguageModel:
    def __init__(self, settings):
        self.client = None
        self.http = None
        self.sync_http = None
        if settings.llm_base_url and settings.llm_api_key.get_secret_value() and settings.llm_model:
            import httpx
            from langchain_openai import ChatOpenAI

            self.http = httpx.AsyncClient(timeout=settings.llm_timeout_seconds)
            self.sync_http = httpx.Client(timeout=settings.llm_timeout_seconds)
            self.client = ChatOpenAI(
                model=settings.llm_model,
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key.get_secret_value(),
                temperature=0,
                timeout=settings.llm_timeout_seconds,
                max_retries=1,
                http_async_client=self.http,
                http_client=self.sync_http,
                http_socket_options=(),
            )

    async def complete(self, messages):
        if self.client is None:
            raise BusinessError("llm_unavailable", "尚未配置完整 LLM 连接信息", 503)
        result = await self.client.ainvoke(messages)
        if not isinstance(result.content, str):
            raise ValueError("LLM 返回了不支持的内容格式")
        return result.content

    async def stream(self, messages):
        """直接消费模型增量；取消上层请求时同时关闭模型流。"""
        if self.client is None:
            raise BusinessError("llm_unavailable", "尚未配置完整 LLM 连接信息", 503)
        async for chunk in self.client.astream(messages):
            if not isinstance(chunk.content, str):
                raise ValueError("LLM 返回了不支持的内容格式")
            if chunk.content:
                yield chunk.content

    async def close(self):
        if self.http:
            await self.http.aclose()
        if self.sync_http:
            self.sync_http.close()
