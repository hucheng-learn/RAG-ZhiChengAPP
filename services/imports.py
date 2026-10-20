import asyncio
import hashlib
import json
import logging
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import PurePath
from uuid import uuid4

from common.errors import BusinessError
from schemas.api import Attribution
from schemas.document import Section, SourceLocation
from utils.chunking.token_budget import TokenBudgetChunker
from utils.parsers.catalog import CATALOG_BATCH_MAX_LINES, recognize_catalog
from utils.parsers.docx import docx_images, parse_docx
from utils.parsers.education import parse_questions
from utils.parsers.files import (
    REMOTE_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    append_image_text,
    image_references,
)
from utils.parsers.markdown import parse_markdown
from utils.parsers.question_recognition import recognize_questions

logger = logging.getLogger("zhichengapp")


def now():
    return datetime.now(UTC).isoformat()


class ImportService:
    def __init__(self, runtime):
        self.runtime = runtime
        self.repo, self.blobs, self.vectors = runtime.repo, runtime.blobs, runtime.vectors
        self.settings = runtime.settings
        self.queue = asyncio.Queue(maxsize=self.settings.import_queue_size)
        self.submission_lock = asyncio.Lock()
        self.document_locks = {}
        self.consumer = None

    async def start(self):
        """重启只标记中断，不假装断点恢复；未执行的持久化排队任务重新入队。"""
        await self.repo.update_many(
            "tasks",
            {"status": "processing"},
            {
                "status": "interrupted",
                "finished_at": now(),
                "error": "服务重启导致任务中断，可从原件重试",
            },
        )
        self.consumer = asyncio.create_task(self._consume(), name="zhichengapp-import-consumer")

    def lock(self, document_id):
        return self.document_locks.setdefault(document_id, asyncio.Lock())

    async def _attribution(self, content_type, course_id, project_id):
        scope = Attribution(course_id=course_id or None, project_id=project_id or None)
        if scope.course_id:
            await self.runtime.catalog.course(scope.course_id)
        return content_type == "catalog" or bool(scope.course_id or scope.project_id)

    def _validate(self, filename, data, content_type):
        """在存储前限制大小、格式和 DOCX 解压尺寸，避免后台才发现无效上传。"""
        suffix = PurePath(filename).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise BusinessError(
                "unsupported_file", "不支持此格式，请上传 Markdown、Word、PDF、PPT 或图片"
            )
        if suffix in REMOTE_EXTENSIONS:
            self.runtime.mineru.require()
        if not data or len(data) > self.settings.max_upload_bytes:
            raise BusinessError("invalid_file_size", "文件为空或超过上传大小限制", 413)
        if suffix == ".docx":
            try:
                with zipfile.ZipFile(BytesIO(data)) as archive:
                    if sum(item.file_size for item in archive.infolist()) > (
                        self.settings.max_docx_unpacked_bytes
                    ):
                        limit_mb = self.settings.max_docx_unpacked_bytes / (1024 * 1024)
                        raise BusinessError(
                            "docx_too_large",
                            f"文件本身未超限，但 DOCX 解压后的内容超过 {limit_mb:g} MB 限制。"
                            "请拆分文档，或压缩文档中的图片后重试。",
                            413,
                        )
                    if "word/document.xml" not in archive.namelist():
                        raise ValueError("not a docx")
            except (zipfile.BadZipFile, ValueError) as exc:
                raise BusinessError("invalid_docx", "文件不是有效的 DOCX") from exc
        elif suffix in {".md", ".markdown", ".txt"}:
            try:
                data.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise BusinessError("invalid_encoding", "Markdown 请使用 UTF-8 编码") from exc

    def fingerprint(self, content_type=None):
        s = self.settings
        configuration = {
            "parser": "education-unified-v3",
            "docx_heading_styles": s.docx_heading_styles,
            "mineru": [self.runtime.mineru.available, s.mineru_base_url, s.mineru_model_version],
            "chunker": TokenBudgetChunker.version,
            "target": s.chunk_target_tokens,
            "max": s.chunk_max_tokens,
            "overlap": s.chunk_overlap_tokens,
            "embedding": str(s.embedding_model_path),
        }
        if content_type == "catalog":
            configuration.update(
                parser="llm-catalog-v1",
                catalog_model=s.llm_model,
                catalog_batch_max_lines=CATALOG_BATCH_MAX_LINES,
            )
        return hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()

    async def submit(
        self,
        filename,
        data,
        content_type,
        course_id=None,
        project_id=None,
        document_id=None,
        *,
        assets=None,
        source_path=None,
    ):
        """上传先持久化原件和任务，再排队；同内容、来源、归属和处理配置幂等复用。

        首次上传以内容哈希区分同名文件。只有显式 document_id 的版本接口会更新文档。
        """
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1][:200]
        self._validate(filename, data, content_type)
        if content_type == "catalog" and self.runtime.llm.client is None:
            raise BusinessError("llm_unavailable", "课程目录识别需要先配置大模型连接", 503)
        if content_type == "catalog":
            # 目录定义全库的系列和课程，不沿用上传页面或旧版本的单课程归属。
            course_id = project_id = None
        confirmed = await self._attribution(content_type, course_id, project_id)
        if content_type != "catalog":
            self.runtime.require_models()
        assets = assets or {}
        if sum(len(value) for value in assets.values()) + len(data) > self.settings.max_batch_bytes:
            raise BusinessError("batch_too_large", "文档及关联图片超过批次总大小限制", 413)
        for value in assets.values():
            if not value or len(value) > self.settings.max_upload_bytes:
                raise BusinessError("invalid_asset_size", "关联图片为空或超过单文件限制", 413)
        digest = hashlib.sha256(data).hexdigest()
        asset_digest = [
            (name, hashlib.sha256(value).hexdigest()) for name, value in sorted(assets.items())
        ]
        signature = hashlib.sha256(
            json.dumps(
                [
                    filename,
                    digest,
                    content_type,
                    course_id,
                    project_id,
                    self.fingerprint(content_type),
                    source_path,
                    asset_digest,
                ]
            ).encode()
        ).hexdigest()
        is_update = document_id is not None
        async with self.submission_lock:
            if document_id:
                document = await self.require_document(document_id)
                if document["status"] in {"deleting", "deleted"}:
                    raise BusinessError("document_deleted", "文档已删除或正在删除", 409)
                version_id = hashlib.sha256(f"{document_id}:{signature}".encode()).hexdigest()[:32]
            else:
                existing = await self.repo.find("documents", {"import_key": signature}, limit=1)
                if existing and existing[0]["status"] == "deleting":
                    raise BusinessError("document_deleting", "文档正在删除，请完成清理后重试", 409)
                if existing and existing[0]["status"] not in {"deleted", "deleting"}:
                    document_id = existing[0]["id"]
                    version_id = existing[0]["latest_version"]
                    tasks = await self.repo.find(
                        "tasks",
                        {
                            "document_id": document_id,
                            "version_id": version_id,
                        },
                        limit=1,
                        sort=[("created_at", -1)],
                    )
                    return {**tasks[0], "reused": True}
                document_id = existing[0]["id"] if existing else uuid4().hex
                version_id = hashlib.sha256(f"{document_id}:{signature}".encode()).hexdigest()[:32]
                document = {
                    "id": document_id,
                    "import_key": signature,
                    "source_name": filename,
                    "content_type": content_type,
                    "course_id": course_id,
                    "project_id": project_id,
                    "active_version": None,
                    "status": "building" if confirmed else "needs_review",
                    "created_at": now(),
                }
            async with self.lock(document_id):
                if is_update:
                    document = await self.require_document(document_id)
                pending = await self.repo.find(
                    "tasks",
                    {
                        "document_id": document_id,
                        "status": {"$in": ["queued", "processing", "needs_review"]},
                    },
                    limit=1,
                )
                if pending:
                    if (
                        is_update
                        and content_type == "catalog"
                        and all(task["status"] == "needs_review" for task in pending)
                    ):
                        await self.repo.update_many(
                            "tasks",
                            {"document_id": document_id, "status": "needs_review"},
                            {"status": "cancelled", "finished_at": now()},
                        )
                    else:
                        raise BusinessError("document_busy", "文档已有待处理任务", 409)
                version = await self.repo.get("versions", version_id)
                if version and document.get("active_version") == version_id:
                    tasks = await self.repo.find("tasks", {"version_id": version_id}, limit=1)
                    return {**tasks[0], "reused": True}
                source_key = (
                    f"documents/{document_id}/{version_id}/source{PurePath(filename).suffix}"
                )
                await self.blobs.put(source_key, data)
                asset_records = await self._save_assets(document_id, version_id, assets, "original")
                version = {
                    "id": version_id,
                    "document_id": document_id,
                    "source_key": source_key,
                    "source_name": filename,
                    "content_hash": digest,
                    "source_path": source_path,
                    "original_assets": asset_records,
                    "fingerprint": self.fingerprint(content_type),
                    "content_type": content_type,
                    "course_id": course_id,
                    "project_id": project_id,
                    "status": "building",
                    "created_at": now(),
                    "parser_version": "llm-catalog-v1"
                    if content_type == "catalog"
                    else "education-unified-v3",
                    "chunker_version": TokenBudgetChunker.version,
                    "embedding_model": str(self.settings.embedding_model_path),
                }
                task = self._task(document_id, version_id, "import", confirmed)
                document["latest_version"] = version_id
                await self.repo.put("versions", version)
                await self.repo.put("documents", document)
                await self.repo.put("tasks", task)
                if confirmed:
                    if not self.queue.full():
                        self.queue.put_nowait(task["id"])
                return {**task, "reused": False}

    def _task(self, document_id, version_id, kind, confirmed=True):
        identifier = uuid4().hex
        return {
            "id": identifier,
            "task_id": identifier,
            "document_id": document_id,
            "version_id": version_id,
            "kind": kind,
            "status": "queued" if confirmed else "needs_review",
            "stage": "waiting",
            "progress": 0,
            "retries": 0,
            "error": None,
            "created_at": now(),
        }

    async def require_document(self, identifier):
        document = await self.repo.get("documents", identifier)
        if not document:
            raise BusinessError("document_not_found", "文档不存在", 404)
        return document

    async def delete_task(self, task_id):
        """移除已结束任务的展示记录，保留幂等回执和资料；与重试互斥。"""
        async with self.submission_lock:
            task = await self.repo.get("tasks", task_id)
            if not task or task.get("deleted_at"):
                return
            if task["status"] not in {"completed", "failed", "interrupted", "cancelled"}:
                raise BusinessError("task_busy", "任务尚未结束，请处理完成后再删除", 409)
            await self.repo.patch("tasks", task_id, {"deleted_at": now()})

    async def retry(self, task_id):
        """复用任务/版本/切片稳定 ID；失败的新版本从原件重建，旧活动版本不变。"""
        async with self.submission_lock:
            task = await self.repo.get("tasks", task_id)
            if not task or task.get("deleted_at"):
                raise BusinessError("task_not_found", "任务不存在", 404)
            if task["status"] not in {"failed", "interrupted"}:
                if task["status"] in {"queued", "processing"}:
                    return task

                raise BusinessError("task_not_retryable", "仅失败或中断任务可以重试", 409)
            document = await self.require_document(task["document_id"])
            if task["kind"] == "import" and document.get("active_version") == task["version_id"]:
                # 活动指针已提交但任务回执丢失时，确认完成，不能删掉当前可查询索引再重建。
                await self.repo.patch("versions", task["version_id"], {"status": "ready"})
                return await self.repo.patch(
                    "tasks",
                    task_id,
                    {
                        "status": "completed",
                        "progress": 100,
                        "stage": "done",
                        "error": None,
                        "finished_at": now(),
                    },
                )
            if document["status"] == "deleted" or (
                document["status"] == "deleting" and task["kind"] != "delete"
            ):
                raise BusinessError("document_deleted", "文档不可重建", 409)
            if task["kind"] == "import" and document["latest_version"] != task["version_id"]:
                raise BusinessError("superseded_task", "已有更新版本，请重试最新版本任务", 409)
            other = await self.repo.find(
                "tasks",
                {
                    "document_id": task["document_id"],
                    "id": {"$ne": task_id},
                    "status": {"$in": ["queued", "processing"]},
                },
                limit=1,
            )
            if other:
                raise BusinessError("document_busy", "同一文档已有其他任务", 409)
            task.update(
                status="queued",
                stage="waiting",
                progress=0,
                error=None,
                retries=task["retries"] + 1,
                finished_at=None,
            )
            await self.repo.put("tasks", task)
            if not self.queue.full():
                self.queue.put_nowait(task_id)
            return task

    async def catalog_review(self, identifier):
        document = await self.require_document(identifier)
        if document["status"] in {"deleted", "deleting"}:
            raise BusinessError("document_deleted", "文档已删除或正在删除", 409)
        version = await self.repo.get("versions", document["latest_version"])
        if version["content_type"] != "catalog" or not version.get("catalog_report"):
            raise BusinessError("review_not_found", "暂无课程目录识别结果", 404)
        query = {"document_id": identifier, "version_id": version["id"]}
        return {
            "version_id": version["id"],
            "status": version["status"],
            **version["catalog_report"],
            "series": await self.repo.find("series", query, limit=10000),
            "courses": await self.repo.find("courses", query, limit=10000),
        }

    async def confirm_catalog(self, identifier, version_id):
        """确认明确的识别版本，过期页面不能激活另一批结果。"""
        async with self.submission_lock, self.lock(identifier):
            report = await self.catalog_review(identifier)
            if report["version_id"] != version_id or report["status"] != "needs_review":
                raise BusinessError("stale_review", "识别结果已变更，请刷新后重新确认", 409)
            tasks = await self.repo.find(
                "tasks", {"version_id": version_id, "status": "needs_review"}, limit=1
            )
            if not tasks:
                raise BusinessError("review_not_found", "待确认任务不存在", 409)
            task = tasks[0]
            for collection in ("series", "courses"):
                await self.repo.update_many(
                    collection,
                    {
                        "document_id": identifier,
                        "version_id": version_id,
                    },
                    {"review_status": "confirmed"},
                )
            await self.repo.patch(
                "versions",
                version_id,
                {
                    "catalog_confirmed_at": now(),
                    "catalog_report": {
                        key: report[key]
                        for key in (
                            "recognizer",
                            "series_count",
                            "course_count",
                            "declared_series_counts",
                            "issues",
                        )
                    },
                },
            )
            try:
                await self.activate(
                    {
                        "task_id": task["id"],
                        "version_id": version_id,
                        "chunk_count": 0,
                        "record_count": report["course_count"],
                    }
                )
            except Exception:
                document = await self.require_document(identifier)
                if document.get("active_version") != version_id:
                    await self.repo.patch("versions", version_id, {"status": "needs_review"})
                raise
            await self.repo.patch(
                "tasks",
                task["id"],
                {
                    "status": "completed",
                    "stage": "done",
                    "progress": 100,
                    "error": None,
                    "finished_at": now(),
                },
            )
            return {"status": "completed"}

    async def change_attribution(self, identifier, attribution):
        """人工确认触发新版本；元数据不直接改动仍在使用的旧版检索范围。"""
        document = await self.require_document(identifier)
        version = await self.repo.get("versions", document["latest_version"])
        if not await self._attribution(
            version["content_type"], attribution.course_id, attribution.project_id
        ):
            raise BusinessError("missing_attribution", "请选择课程或填写项目归属")
        await self.repo.update_many(
            "tasks",
            {"document_id": identifier, "status": "needs_review"},
            {"status": "cancelled", "finished_at": now()},
        )
        data = await self.blobs.get(version["source_key"])
        return await self.submit(
            version["source_name"],
            data,
            version["content_type"],
            attribution.course_id,
            attribution.project_id,
            identifier,
            assets=await self._original_assets(version),
            source_path=version.get("source_path"),
        )

    async def delete(self, identifier):
        """先撤销可查询性，再异步清理跨库对象；失败保留删除状态并允许重试。"""
        async with self.submission_lock:
            await self.require_document(identifier)
            existing = await self.repo.find(
                "tasks",
                {
                    "document_id": identifier,
                    "kind": "delete",
                    "status": {"$in": ["queued", "processing", "completed"]},
                },
                limit=1,
            )
            if existing:
                return existing[0]
            await self.repo.patch("documents", identifier, {"status": "deleting"})
            await self.repo.update_many(
                "tasks",
                {
                    "document_id": identifier,
                    "kind": "import",
                    "status": {"$in": ["queued", "processing", "needs_review"]},
                },
                {"status": "cancelled", "finished_at": now()},
            )
            task = self._task(identifier, None, "delete")
            await self.repo.put("tasks", task)
            if not self.queue.full():
                self.queue.put_nowait(task["id"])
            return task

    async def _consume(self):
        """单消费者保证每次仅一份文档写索引；队列句柄不替代 MongoDB 任务事实。"""
        from processor.import_processor.main_graph import build_graph

        graph = build_graph(self)
        while True:
            task_id = None
            from_queue = False
            try:
                try:
                    task_id = await asyncio.wait_for(self.queue.get(), timeout=1)
                    from_queue = True
                except TimeoutError:
                    waiting = await self.repo.find(
                        "tasks", {"status": "queued"}, limit=1, sort=[("created_at", 1)]
                    )
                    if not waiting:
                        continue
                    task_id = waiting[0]["id"]
                task = await self.repo.get("tasks", task_id)
                if not task or task["status"] != "queued":
                    continue
                async with self.lock(task["document_id"]):
                    await self.repo.patch(
                        "tasks",
                        task_id,
                        {"status": "processing", "started_at": now(), "stage": "starting"},
                    )
                    if task["kind"] == "delete":
                        await self._cleanup(task["document_id"])
                    else:
                        result = await graph.ainvoke(
                            {"task_id": task_id, "version_id": task["version_id"]}
                        )
                        if result.get("catalog_needs_review"):
                            await self.repo.patch(
                                "versions",
                                task["version_id"],
                                {
                                    "status": "needs_review",
                                },
                            )
                            await self.repo.patch(
                                "tasks",
                                task_id,
                                {
                                    "status": "needs_review",
                                    "stage": "catalog_review",
                                    "progress": 90,
                                    "error": "目录识别存在待确认项，请查看识别结果",
                                },
                            )
                            document = await self.require_document(task["document_id"])
                            if not document.get("active_version"):
                                await self.repo.patch(
                                    "documents",
                                    task["document_id"],
                                    {
                                        "status": "needs_review",
                                    },
                                )
                            continue
                    await self.repo.patch(
                        "tasks",
                        task_id,
                        {
                            "status": "completed",
                            "progress": 100,
                            "stage": "done",
                            "finished_at": now(),
                        },
                        condition={"status": "processing"},
                    )
            except asyncio.CancelledError:
                if task_id:
                    await self.repo.patch(
                        "tasks",
                        task_id,
                        {
                            "status": "interrupted",
                            "error": "服务停止，可从原件重新执行",
                            "finished_at": now(),
                        },
                        condition={"status": "processing"},
                    )
                raise
            except Exception as exc:
                logger.warning("import_failed task_id=%s type=%s", task_id, type(exc).__name__)
                if task_id:
                    task = await self.repo.get("tasks", task_id)
                    if task and task["kind"] == "import":
                        document = await self.repo.get("documents", task["document_id"])
                        if document and document.get("active_version") == task["version_id"]:
                            # 切换已成功，随后写任务回执失败不能把活动版本改成 failed。
                            await self.repo.patch(
                                "versions", task["version_id"], {"status": "ready"}
                            )
                            await self.repo.patch(
                                "tasks",
                                task_id,
                                {
                                    "status": "completed",
                                    "stage": "done",
                                    "progress": 100,
                                    "error": None,
                                    "finished_at": now(),
                                },
                            )
                            continue
                    await self.repo.patch(
                        "tasks",
                        task_id,
                        {
                            "status": "failed",
                            "error": (
                                str(exc)[:200]
                                if isinstance(exc, (BusinessError, ValueError))
                                else "处理失败，请检查服务状态后重试"
                            ),
                            "finished_at": now(),
                        },
                        condition={"status": "processing"},
                    )
                    if task and task["kind"] == "import":
                        await self.repo.patch("versions", task["version_id"], {"status": "failed"})
                        # 未激活版本不会被查询；清理失败可由同一任务重试时再次清除。
                        try:
                            document = await self.repo.get("documents", task["document_id"])
                            if document.get("active_version") != task["version_id"]:
                                await self.vectors.delete_version(
                                    task["document_id"], task["version_id"]
                                )
                        except Exception:
                            logger.warning("inactive_index_cleanup_failed task_id=%s", task_id)
            finally:
                # 只有通过队列取得的句柄才减少 unfinished_tasks。
                if from_queue:
                    self.queue.task_done()

    async def _save_assets(self, document_id, version_id, assets, kind):
        records = []
        for path, value in assets.items():
            key = (
                f"documents/{document_id}/{version_id}/{kind}/"
                + hashlib.sha256(path.encode()).hexdigest()
            )
            await self.blobs.put(key, value)
            records.append({"path": path, "key": key})
        return records

    async def _original_assets(self, version):
        return {
            item["path"]: await self.blobs.get(item["key"])
            for item in version.get("original_assets", [])
        }

    async def parse_and_store(self, state):
        """解析及持久化正文，State 只携带 ID 和计数，不携带整批向量。"""
        version = await self.repo.get("versions", state["version_id"])
        document_id = version["document_id"]
        if version["content_type"] == "catalog":
            await self.repo.patch("versions", version["id"], {"catalog_report": None})
            await self.repo.patch("tasks", state["task_id"], {"catalog_report": None})
        await self.repo.patch("tasks", state["task_id"], {"stage": "parsing", "progress": 10})
        for collection in ("chunks", "sections", "courses", "series", "questions"):
            await self.repo.delete(
                collection, {"document_id": document_id, "version_id": version["id"]}
            )
        await self.vectors.delete_version(document_id, version["id"])
        for item in version.get("assets", []):
            await self.blobs.remove(item["key"])
        await self.repo.patch("versions", version["id"], {"assets": []})
        data = await self.blobs.get(version["source_key"])
        suffix = PurePath(version["source_name"]).suffix.lower()
        assets = await self._original_assets(version)
        converted = False

        async def progress(stage):
            await self.repo.patch("tasks", state["task_id"], {"stage": stage, "progress": 12})

        if suffix in REMOTE_EXTENSIONS or (suffix == ".docx" and self.runtime.mineru.available):
            source, extracted = await self.runtime.mineru.parse(
                version["source_name"], data, progress
            )
            assets.update(extracted)
            converted = True
            parsed = parse_markdown(source, document_id=document_id, version_id=version["id"])
        elif suffix == ".docx":
            parsed = await self.repo.call(
                parse_docx,
                data,
                document_id=document_id,
                version_id=version["id"],
                heading_styles=self.settings.docx_heading_styles,
                include_images=True,
            )
            source = "\n\n".join(
                (
                    "#" * section.heading_level + " " + section.title + "\n"
                    if section.heading_level
                    else ""
                )
                + section.body
                for section in parsed.sections
            )
            assets.update(await self.repo.call(docx_images, data))
            if assets:
                parsed.warnings.append("Word 图片已保留，当前未识别图片中的文字")
        else:
            source = data.decode("utf-8-sig")
            parsed = await self.repo.call(
                parse_markdown, source, document_id=document_id, version_id=version["id"]
            )
        refs = image_references(source)
        missing = [ref for ref in refs if ref not in assets]
        if missing:
            parsed.warnings.append(
                f"有 {len(missing)} 张图片未随文件上传或为外部链接，未读取图片内容"
            )
        # Markdown 自带图片时逐张识别，原图片继续保留用于练习和引用。
        if suffix in {".md", ".markdown", ".txt"} and self.runtime.mineru.available:
            recognized_images = {}
            for ref in refs:
                if ref in assets:
                    recognized, _ = await self.runtime.mineru.parse(ref, assets[ref], progress)
                    recognized_images[ref] = recognized
                    converted = True
            if recognized_images:
                source = append_image_text(source, recognized_images)
                parsed = parse_markdown(source, document_id=document_id, version_id=version["id"])
        if assets and not self.runtime.mineru.available and suffix in {".md", ".markdown", ".txt"}:
            parsed.warnings.append("关联图片已保存，当前未识别图片中的文字")
        if converted:
            for section in parsed.sections:
                section.source.format = "normalized"
        asset_records = await self._save_assets(document_id, version["id"], assets, "parsed")
        await self.repo.patch("versions", version["id"], {"assets": asset_records})
        normalized_key = f"documents/{document_id}/{version['id']}/normalized.md"
        await self.blobs.put(normalized_key, source.encode("utf-8"), "text/markdown; charset=utf-8")
        await self.repo.patch(
            "versions",
            version["id"],
            {
                "normalized_key": normalized_key,
                "warnings": parsed.warnings,
            },
        )
        if version["content_type"] == "catalog":

            async def progress(index, total):
                await self.repo.patch(
                    "tasks",
                    state["task_id"],
                    {
                        "stage": "recognizing_catalog",
                        "progress": 10 + int(65 * index / total),
                        "catalog_batch": min(index + 1, total),
                        "catalog_batches": total,
                        "catalog_completed_batches": index,
                        **({"catalog_batch_started_at": now()} if index < total else {}),
                    },
                )

            series, courses, report = await recognize_catalog(
                source,
                document_id,
                version["id"],
                self.runtime.llm,
                progress,
                timeout_seconds=self.settings.llm_timeout_seconds,
            )
            await self.repo.patch("versions", version["id"], {"catalog_report": report})
            if suffix not in {".md", ".markdown", ".txt"} or converted:
                for record in [*series, *courses]:
                    record["source"]["format"] = "normalized"
            for collection, records in (("series", series), ("courses", courses)):
                for record in records:
                    await self.repo.put(collection, record)
            await self.repo.patch("tasks", state["task_id"], {"catalog_report": report})
            return {
                "chunk_count": 0,
                "record_count": len(courses),
                "catalog_needs_review": bool(report["issues"]),
            }
        self.runtime.require_models()
        course_name = ""
        if version.get("course_id"):
            course_name = (await self.runtime.catalog.course(version["course_id"]))["name"]
        chunker = TokenBudgetChunker(
            self.runtime.models.tokenizer,
            self.settings.chunk_target_tokens,
            self.settings.chunk_max_tokens,
            self.settings.chunk_overlap_tokens,
        )
        metadata = {
            key: version.get(key)
            for key in ("course_id", "project_id", "content_type", "source_name")
        }
        metadata["course_name"] = course_name
        chunks = 0
        if version["content_type"] == "question_bank":
            try:
                questions = await self.repo.call(
                    parse_questions, source, document_id, version["id"], version.get("course_id")
                )
                if suffix not in {".md", ".markdown", ".txt"} or converted:
                    for question in questions:
                        question["source"]["format"] = "normalized"
            except ValueError:
                await progress("recognizing_questions")
                questions = await recognize_questions(
                    source,
                    document_id,
                    version["id"],
                    version.get("course_id"),
                    self.runtime.llm,
                    self.settings.llm_timeout_seconds,
                )
                parsed.warnings.append("题目由模型自动识别，答案沿用原文，尚未人工审核")
                await self.repo.patch("versions", version["id"], {"warnings": parsed.warnings})
            from markdown_it import MarkdownIt

            image_env = {}
            MarkdownIt("commonmark").parse(source, image_env)
            for index, question in enumerate(questions):
                question["images"] = image_references(
                    question["stem"] + "\n" + question["options"], dict(image_env)
                )
                question["solution_images"] = image_references(
                    question["answer"] + "\n" + question["explanation"], dict(image_env)
                )
                question["project_id"] = version.get("project_id")
                await self.repo.put("questions", question)
                section = Section(
                    section_id=question["id"],
                    document_id=document_id,
                    version_id=version["id"],
                    section_index=index,
                    title=question["code"],
                    title_path=[question["code"]],
                    heading_level=3,
                    body=question["stem"]
                    + "\n"
                    + question["options"]
                    + "\n"
                    + ", ".join(question["knowledge_points"]),
                    source=SourceLocation(**question["source"]),
                )
                question_chunks = await self.repo.call(
                    chunker.split,
                    section,
                    {
                        **metadata,
                        "question_type": question["question_type"],
                        "question_id": question["question_id"],
                    },
                )
                for chunk in question_chunks:
                    # 题目切片中的文本是结构化重组，来源定位用原题块范围而非推算行号。
                    chunk["source"] = question["source"]
                    await self.repo.put("chunks", chunk)
                    chunks += 1
        else:
            for section in parsed.sections:
                await self.repo.put("sections", {"id": section.section_id, **section.model_dump()})
                for chunk in await self.repo.call(chunker.split, section, metadata):
                    await self.repo.put("chunks", chunk)
                    chunks += 1
        if not chunks:
            raise ValueError("未提取到可索引正文")
        return {"chunk_count": chunks, "record_count": len(parsed.sections)}

    async def index(self, state):
        await self.repo.patch("tasks", state["task_id"], {"stage": "embedding", "progress": 35})
        if not state["chunk_count"]:
            return {}
        offset, size = 0, self.settings.model_batch_size
        while True:
            rows = await self.repo.find(
                "chunks",
                {"version_id": state["version_id"]},
                limit=size,
                offset=offset,
                sort=[("id", 1)],
            )
            if not rows:
                break
            dense, sparse = await self.runtime.models.encode(
                [row["embedding_text"] for row in rows]
            )
            vectors = []
            for row, d, s in zip(rows, dense, sparse, strict=True):
                vectors.append(
                    {
                        "id": row["id"],
                        "dense": d,
                        "sparse": s,
                        **{
                            key: row.get(key) or ""
                            for key in (
                                "document_id",
                                "version_id",
                                "course_id",
                                "project_id",
                                "content_type",
                                "question_type",
                            )
                        },
                    }
                )
            await self.vectors.upsert(vectors)
            offset += len(rows)
            await self.repo.patch(
                "tasks",
                state["task_id"],
                {
                    "processed_chunks": offset,
                    "progress": 35 + int(50 * offset / state["chunk_count"]),
                },
            )
        return {}

    async def activate(self, state):
        """强一致性数量校验通过后原子切换；删除标记优先于正在完成的导入。"""
        if state.get("catalog_needs_review"):
            return {}
        version = await self.repo.get("versions", state["version_id"])
        if state["chunk_count"]:
            await self.vectors.verify(version["document_id"], version["id"], state["chunk_count"])
        await self.repo.patch("versions", version["id"], {"status": "ready", **state})
        updated = await self.repo.patch(
            "documents",
            version["document_id"],
            {
                "active_version": version["id"],
                "status": "ready",
                "updated_at": now(),
                **{
                    key: version[key]
                    for key in ("course_id", "project_id", "content_type", "source_name")
                },
            },
            condition={"status": {"$nin": ["deleted", "deleting"]}},
        )
        if not updated:
            raise BusinessError("document_deleted", "文档已标记删除，未激活新版本", 409)
        await self.repo.patch(
            "tasks",
            state["task_id"],
            {"chunk_count": state["chunk_count"], "record_count": state["record_count"]},
        )
        return {}

    async def _cleanup(self, identifier):
        await self.vectors.delete_version(identifier)
        versions = await self.repo.find("versions", {"document_id": identifier}, limit=10000)
        for version in versions:
            for item in [*version.get("assets", []), *version.get("original_assets", [])]:
                await self.blobs.remove(item["key"])
            for key in ("source_key", "normalized_key"):
                if version.get(key):
                    await self.blobs.remove(version[key])
            await self.repo.patch("versions", version["id"], {"status": "deleted"})
        for collection in ("chunks", "sections", "courses", "series", "questions"):
            await self.repo.delete(collection, {"document_id": identifier})
        document = await self.require_document(identifier)
        await self.repo.patch(
            "documents",
            identifier,
            {
                "status": "deleted",
                "active_version": None,
                "import_key": document["import_key"] + ":deleted:" + uuid4().hex,
            },
        )

    async def close(self):
        if self.consumer:
            self.consumer.cancel()
            try:
                await self.consumer
            except asyncio.CancelledError:
                pass
