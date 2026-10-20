"""文档导入服务：负责文件上传、批量文件夹导入、任务管理、文档删除与原件下载。"""

import mimetypes
from pathlib import PurePath
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response

from api.dependencies import runtime
from common.errors import BusinessError
from schemas.api import Attribution, CatalogConfirmation, ContentType
from utils.parsers.files import (
    IMAGE_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    image_references,
    relative_path,
    resolve_image,
)

router = APIRouter(prefix="/api/v1", tags=["documents"])


async def read_upload(file: UploadFile, maximum):
    """读取时就限流，避免先把任意大小上传 完整放入内存。"""
    try:
        # 多读 1 字节用于判断是否超限，超限直接抛 413
        data = await file.read(maximum + 1)
        if len(data) > maximum:
            raise BusinessError("file_too_large", "文件超过上传大小限制", 413)
        return data
    finally:
        await file.close()


@router.post("/documents/import", status_code=202, summary="导入单个文件")
async def import_document(
    file: Annotated[UploadFile, File()],
    content_type: Annotated[ContentType, Form()],
    course_id: Annotated[str | None, Form()] = None,
    project_id: Annotated[str | None, Form()] = None,
    rt=Depends(runtime),
):
    """单文件导入：读取并校验后提交异步导入任务，返回 202 表示任务已受理。"""
    data = await read_upload(file, rt.settings.max_upload_bytes)
    return await rt.imports.submit(
        file.filename or "upload.md", data, content_type, course_id, project_id
    )


@router.post("/documents/import-folder", status_code=202, summary="批量导入文件夹")
async def import_folder(
    files: Annotated[list[UploadFile], File()],
    content_type: Annotated[ContentType, Form()],
    course_id: Annotated[str | None, Form()] = None,
    project_id: Annotated[str | None, Form()] = None,
    rt=Depends(runtime),
):
    """路径在内存中匹配，图片随所属 Markdown 保存；其他文件分别产生任务。"""
    try:
        # 第一阶段：批量上限与逐文件校验（重复路径、大小、总大小）
        if len(files) > rt.settings.max_batch_files:
            raise BusinessError("batch_too_many", "文件数量超过单批限制")
        indexed, total = {}, 0
        for file in files:
            path = relative_path(file.filename or "")
            if path in indexed:
                raise BusinessError("duplicate_path", "文件夹中存在重复路径")
            size = file.size
            if size is None:
                raise BusinessError("invalid_upload", "无法确认上传文件大小")
            if size > rt.settings.max_upload_bytes:
                raise BusinessError("file_too_large", f"{path} 超过单文件大小限制", 413)
            total += size
            indexed[path] = file
        if total > rt.settings.max_batch_bytes:
            raise BusinessError("batch_too_large", "本批文件总大小超过限制", 413)

        # 第二阶段：扫描 Markdown 内的图片引用，建立 引用路径 -> 实际文件路径 的映射
        references, used = {}, set()
        for path, file in indexed.items():
            if PurePath(path).suffix.lower() not in {".md", ".markdown", ".txt"}:
                continue
            data = await file.read(rt.settings.max_upload_bytes + 1)
            # 读完回退到文件头，后续阶段需要重新读取
            await file.seek(0)
            try:
                refs = image_references(data.decode("utf-8-sig"))
            except UnicodeDecodeError:
                continue  # 单文件回执报告编码问题。
            matches = {}
            for ref in refs:
                target = resolve_image(path, ref)
                if target in indexed and PurePath(target).suffix.lower() in IMAGE_EXTENSIONS:
                    matches[ref] = target
                    used.add(target)
            references[path] = matches

        # 第三阶段：逐文件提交导入任务；被引用的图片随 Markdown 作为附件提交，不单独生成任务
        items = []
        for path, file in indexed.items():
            if path in used:
                continue
            if PurePath(path).suffix.lower() not in SUPPORTED_EXTENSIONS:
                items.append({"path": path, "skipped": True, "message": "跳过不支持的文件格式"})
                continue
            try:
                data = await file.read(rt.settings.max_upload_bytes + 1)
                assets = {}
                for ref, target in references.get(path, {}).items():
                    asset = indexed[target]
                    await asset.seek(0)
                    assets[ref] = await asset.read(rt.settings.max_upload_bytes + 1)
                task = await rt.imports.submit(
                    path, data, content_type, course_id, project_id, assets=assets, source_path=path
                )
                items.append({"path": path, "task": task})
            except BusinessError as exc:
                # 单文件失败不影响整批，错误记录在该文件的回执中
                items.append({"path": path, "code": exc.code, "message": exc.message})
        # attachments：已随 Markdown 提交、不再单独生成任务的图片列表
        return {"items": items, "attachments": sorted(used)}
    finally:
        for file in files:
            await file.close()


@router.get("/documents/{identifier}/asset", summary="预览文档内嵌图片")
async def document_asset(identifier: str, path: str, version_id: str, rt=Depends(runtime)):
    """文档内嵌图片预览：校验文档/版本状态后，从对象存储读取图片返回。"""
    document = await rt.imports.require_document(identifier)
    version = await rt.repo.get("versions", version_id)
    # 文档已删除、版本不存在或归属不符时，图片一律不可访问
    if (
        document["status"] in {"deleted", "deleting"}
        or not version
        or version["document_id"] != identifier
        or version["status"] == "deleted"
    ):
        raise BusinessError("asset_unavailable", "图片已不可访问", 404)
    entry = next((item for item in version.get("assets", []) if item["path"] == path), None)
    if not entry:
        raise BusinessError("asset_not_found", "图片不存在", 404)
    media = mimetypes.guess_type(path)[0] or "application/octet-stream"
    # 仅允许白名单内的图片类型，防止借助预览端点读取任意文件
    if media not in {
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
        "image/bmp",
        "image/jp2",
    }:
        raise BusinessError("asset_type", "此附件不支持图片预览", 415)
    return Response(
        await rt.blobs.get(entry["key"]),
        media_type=media,
        # nosniff 防止浏览器嗅探类型；no-store 保证删除后立即失效
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )


@router.get("/documents", summary="查询文档列表")
async def documents(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), rt=Depends(runtime)
):
    """分页查询文档列表（排除已删除），并补充最新版本的告警与元信息。"""
    rows = await rt.repo.find(
        "documents",
        {"status": {"$ne": "deleted"}},
        limit=limit + 1,
        offset=offset,
        sort=[("created_at", -1), ("id", -1)],
    )
    # 多取一条判断是否有下一页，再裁剪回 limit
    has_more = len(rows) > limit
    rows = rows[:limit]
    # 批量查询各文档最新版本，避免逐条 N+1 查询
    versions = {
        version["id"]: version
        for version in await rt.repo.find(
            "versions",
            {"id": {"$in": [row.get("latest_version") for row in rows]}},
            limit=200,
        )
    }
    for row in rows:
        row["warnings"] = versions.get(row.get("latest_version"), {}).get("warnings", [])
        row["source_path"] = versions.get(row.get("latest_version"), {}).get("source_path")
        row["catalog_review_available"] = bool(
            versions.get(row.get("latest_version"), {}).get("catalog_report")
        )
    return {"items": rows, "has_more": has_more}


@router.get("/import-tasks", summary="查询导入任务列表")
async def tasks(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), rt=Depends(runtime)
):
    """分页查询导入任务列表，联查版本/文档信息补全任务来源名称。"""
    rows = await rt.repo.find(
        "tasks",
        {"deleted_at": None},
        limit=limit + 1,
        offset=offset,
        sort=[("created_at", -1), ("id", -1)],
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    # 批量联查任务对应的版本与文档，避免逐条 N+1 查询
    versions = {
        row["id"]: row
        for row in await rt.repo.find(
            "versions", {"id": {"$in": [task.get("version_id") for task in rows]}}, limit=200
        )
    }
    documents = {
        row["id"]: row
        for row in await rt.repo.find(
            "documents", {"id": {"$in": [task.get("document_id") for task in rows]}}, limit=200
        )
    }
    for row in rows:
        source = versions.get(row.get("version_id")) or documents.get(row.get("document_id"), {})
        row["source_name"] = source.get("source_name", "原文档已不可用")
        row["content_type"] = source.get("content_type")
    return {"items": rows, "has_more": has_more}


@router.get("/import-tasks/{task_id}", summary="查询导入任务详情")
async def task(task_id: str, rt=Depends(runtime)):
    """查询单个导入任务的详情（含处理进度与错误信息）。"""
    result = await rt.repo.get("tasks", task_id)
    if not result or result.get("deleted_at"):
        raise BusinessError("task_not_found", "任务不存在", 404)
    return result


@router.delete("/import-tasks/{task_id}", status_code=204, summary="删除导入任务记录")
async def delete_task(task_id: str, rt=Depends(runtime)):
    """软删除导入任务（仅移除任务记录，不影响已生成的文档）。"""
    await rt.imports.delete_task(task_id)
    return Response(status_code=204)


@router.post("/import-tasks/{task_id}/retry", status_code=202, summary="重试失败的导入任务")
async def retry_task(task_id: str, rt=Depends(runtime)):
    """重试失败的导入任务，返回新任务信息。"""
    return await rt.imports.retry(task_id)


@router.patch("/documents/{identifier}", status_code=202, summary="修改文档归属")
async def attribution(identifier: str, body: Attribution, rt=Depends(runtime)):
    """修改文档的归属（课程/项目）。"""
    return await rt.imports.change_attribution(identifier, body)


@router.get("/documents/{identifier}/catalog-review", summary="查询目录识别结果")
async def catalog_review(identifier: str, rt=Depends(runtime)):
    """查询文档的目录结构识别结果，供人工审核确认。"""
    return await rt.imports.catalog_review(identifier)


@router.post("/documents/{identifier}/catalog-review", summary="确认目录识别结果")
async def confirm_catalog(identifier: str, body: CatalogConfirmation, rt=Depends(runtime)):
    """确认目录识别结果，按确认的结构生成课程内容。"""
    return await rt.imports.confirm_catalog(identifier, body.version_id)


@router.post("/documents/{identifier}/recognize-catalog", status_code=202, summary="重新识别课程目录")
async def recognize_existing_catalog(identifier: str, rt=Depends(runtime)):
    """对已有的课程目录文档重新发起结构识别。"""
    document = await rt.imports.require_document(identifier)
    if document["status"] in {"deleted", "deleting"}:
        raise BusinessError("document_deleted", "文档已删除或正在删除", 409)
    if document["content_type"] != "catalog":
        raise BusinessError("not_catalog", "仅课程目录支持结构识别", 400)
    # 从对象存储取出原始文件，作为新的目录识别任务重新提交
    version = await rt.repo.get("versions", document["latest_version"])
    data = await rt.blobs.get(version["source_key"])
    return await rt.imports.submit(version["source_name"], data, "catalog", document_id=identifier)


@router.post("/documents/{identifier}/versions", status_code=202, summary="上传文档新版本")
async def new_version(identifier: str, file: Annotated[UploadFile, File()], rt=Depends(runtime)):
    """为已有文档上传新版本：沿用原文档的内容类型与归属信息。"""
    document = await rt.imports.require_document(identifier)
    data = await read_upload(file, rt.settings.max_upload_bytes)
    return await rt.imports.submit(
        file.filename or document["source_name"],
        data,
        document["content_type"],
        document.get("course_id"),
        document.get("project_id"),
        identifier,
    )


@router.delete("/documents/{identifier}", status_code=202, summary="删除文档")
async def delete_document(identifier: str, rt=Depends(runtime)):
    """删除文档（异步执行，返回 202 表示删除任务已受理）。"""
    return await rt.imports.delete(identifier)


@router.get("/documents/{identifier}/source", summary="下载文档原件")
async def source(identifier: str, version_id: str | None = None, rt=Depends(runtime)):
    """下载文档原件；未指定版本时优先返回激活版本，否则返回最新版本。"""
    document = await rt.imports.require_document(identifier)
    if document["status"] in {"deleting", "deleted"}:
        raise BusinessError("source_deleted", "原件已删除或不可访问", 410)
    version_id = version_id or document.get("active_version") or document["latest_version"]
    version = await rt.repo.get("versions", version_id)
    if not version or version["document_id"] != identifier or version["status"] == "deleted":
        raise BusinessError("version_not_found", "原件版本不存在或已删除", 404)
    data = await rt.blobs.get(version["source_key"])
    suffix = PurePath(version["source_name"]).suffix.lower()
    content_type = mimetypes.guess_type(version["source_name"])[0] or "application/octet-stream"
    # Markdown 原件强制按 UTF-8 文本返回，保证浏览器正确渲染
    if suffix in {".md", ".markdown"}:
        content_type = "text/markdown; charset=utf-8"
    return Response(
        data,
        media_type=content_type,
        headers={
            # filename* 按 RFC 5987 编码，支持中文文件名下载
            "Content-Disposition": "attachment; filename*=UTF-8''"
            + quote(version["source_name"], safe=""),
            "X-Content-Type-Options": "nosniff",
        },
    )
