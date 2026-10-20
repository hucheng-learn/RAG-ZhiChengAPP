from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ZHICHENGAPP_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "知成 ZhiChengApp"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_file: Path = Path(__file__).resolve().parents[2] / "logs" / "zhichengapp.log"
    log_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    log_backup_count: int = Field(default=5, ge=1)
    dependency_timeout_seconds: float = Field(default=3, ge=0.1, le=30)
    mongo_uri: SecretStr = SecretStr("")
    mongo_database: Literal["zhichengapp"] = "zhichengapp"
    milvus_uri: str = ""
    milvus_token: SecretStr = SecretStr("")
    milvus_collection: Literal["zhichengapp_chunks"] = "zhichengapp_chunks"
    minio_endpoint: str = ""
    minio_access_key: SecretStr = SecretStr("")
    minio_secret_key: SecretStr = SecretStr("")
    minio_secure: bool = False
    minio_bucket: Literal["zhichengapp-files"] = "zhichengapp-files"
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    asr_url: str = (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    )
    # 语音识别复用 llm_api_key；模型名称由 .env 显式配置。
    asr_model: str = ""
    asr_timeout_seconds: float = Field(default=60, ge=1, le=180)
    embedding_model_path: Path | None = None
    reranker_model_path: Path | None = None
    model_device: str = "cuda:0"
    docx_heading_styles: dict[str, int] = Field(
        default_factory=lambda: {
            "一级标题": 1,
            "二级标题": 2,
            "三级标题": 3,
            "四级标题": 4,
            "五级标题": 5,
            "六级标题": 6,
            "半括号标题（五级）": 5,
            "圆括号标题（六级标题）": 6,
        }
    )
    load_local_models: bool = False
    max_upload_bytes: int = Field(default=200 * 1024 * 1024, ge=1024)
    max_docx_unpacked_bytes: int = Field(default=200 * 1024 * 1024, ge=1024)
    # MinerU v4 服务；不在日志或能力接口暴露令牌。
    mineru_base_url: str = "https://mineru.net/api/v4"
    mineru_api_token: SecretStr = SecretStr("")
    mineru_download_trust_env: bool = False
    mineru_model_version: Literal["vlm", "pipeline"] = "vlm"
    mineru_timeout_seconds: float = Field(default=1800, ge=1, le=7200)
    mineru_poll_seconds: float = Field(default=3, ge=0.01, le=30)
    max_batch_files: int = Field(default=200, ge=1, le=500)
    max_batch_bytes: int = Field(default=200 * 1024 * 1024, ge=1024)
    model_batch_size: int = Field(default=8, ge=1, le=64)
    llm_timeout_seconds: float = Field(default=90, ge=1, le=600)
    retrieval_limit: int = Field(default=20, ge=5, le=100)
    rerank_limit: int = Field(default=5, ge=1, le=20)
    context_max_tokens: int = Field(default=4000, ge=256, le=16000)
    # 重排分数未校准时仅排序；默认不硬截断，充分性由真实上下文判断。
    min_relevance_score: float = Field(default=0, ge=0, le=1)
    import_queue_size: int = Field(default=8, ge=1, le=128)
    model_concurrency: int = Field(default=1, ge=1, le=8)
    chunk_target_tokens: int = Field(default=512, ge=1)
    chunk_max_tokens: int = Field(default=768, ge=1)
    chunk_overlap_tokens: int = Field(default=64, ge=0)

    @field_validator("embedding_model_path", "reranker_model_path", mode="before")
    @classmethod
    def empty_model_path(cls, value):
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_limits(self) -> "Settings":
        """启动时阻止越界预算和错误来源配置，避免任务运行中才暴露配置问题。"""
        if any(not 1 <= value <= 9 for value in self.docx_heading_styles.values()):
            raise ValueError("DOCX 标题映射层级须为 1–9")
        if not self.chunk_overlap_tokens < self.chunk_target_tokens <= self.chunk_max_tokens:
            raise ValueError("切片参数须满足 overlap < target <= max")
        if self.minio_endpoint and "://" in self.minio_endpoint:
            raise ValueError("MinIO endpoint 不包含协议，请通过 minio_secure 配置 TLS")
        return self
