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
    """读取时就限流，避免先把任意大小上传完整放入内存。"""
    try:
        data = await file.read(maximum + 1)
        if len(data) > maximum:
            raise BusinessError("file_too_large", "文件超过上传大小限制", 413)
        return data
    finally:
        await file.close()


@router.post("/documents/import", status_code=202)
async def import_document(
    file: Annotated[UploadFile, File()],
    content_type: Annotated[ContentType, Form()],
    course_id: Annotated[str | None, Form()] = None,
    project_id: Annotated[str | None, Form()] = None,
    rt=Depends(runtime),
):
    data = await read_upload(file, rt.settings.max_upload_bytes)
    return await rt.imports.submit(
        file.filename or "upload.md", data, content_type, course_id, project_id
    )


@router.post("/documents/import-folder", status_code=202)
async def import_folder(
    files: Annotated[list[UploadFile], File()],
    content_type: Annotated[ContentType, Form()],
    course_id: Annotated[str | None, Form()] = None,
    project_id: Annotated[str | None, Form()] = None,
    rt=Depends(runtime),
):
    """路径在内存中匹配，图片随所属 Markdown 保存；其他文件分别产生任务。"""
    try:
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
        references, used = {}, set()
        for path, file in indexed.items():
            if PurePath(path).suffix.lower() not in {".md", ".markdown", ".txt"}:
                continue
            data = await file.read(rt.settings.max_upload_bytes + 1)
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
                items.append({"path": path, "code": exc.code, "message": exc.message})
        return {"items": items, "attachments": sorted(used)}
    finally:
        for file in files:
            await file.close()


@router.get("/documents/{identifier}/asset")
async def document_asset(identifier: str, path: str, version_id: str, rt=Depends(runtime)):
    document = await rt.imports.require_document(identifier)
    version = await rt.repo.get("versions", version_id)
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
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )


@router.get("/documents")
async def documents(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), rt=Depends(runtime)
):
    rows = await rt.repo.find(
        "documents",
        {"status": {"$ne": "deleted"}},
        limit=limit + 1,
        offset=offset,
        sort=[("created_at", -1), ("id", -1)],
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
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


@router.get("/import-tasks")
async def tasks(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), rt=Depends(runtime)
):
    rows = await rt.repo.find(
        "tasks",
        {"deleted_at": None},
        limit=limit + 1,
        offset=offset,
        sort=[("created_at", -1), ("id", -1)],
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
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


@router.get("/import-tasks/{task_id}")
async def task(task_id: str, rt=Depends(runtime)):
    result = await rt.repo.get("tasks", task_id)
    if not result or result.get("deleted_at"):
        raise BusinessError("task_not_found", "任务不存在", 404)
    return result


@router.delete("/import-tasks/{task_id}", status_code=204)
async def delete_task(task_id: str, rt=Depends(runtime)):
    await rt.imports.delete_task(task_id)
    return Response(status_code=204)


@router.post("/import-tasks/{task_id}/retry", status_code=202)
async def retry_task(task_id: str, rt=Depends(runtime)):
    return await rt.imports.retry(task_id)


@router.patch("/documents/{identifier}", status_code=202)
async def attribution(identifier: str, body: Attribution, rt=Depends(runtime)):
    return await rt.imports.change_attribution(identifier, body)


@router.get("/documents/{identifier}/catalog-review")
async def catalog_review(identifier: str, rt=Depends(runtime)):
    return await rt.imports.catalog_review(identifier)


@router.post("/documents/{identifier}/catalog-review")
async def confirm_catalog(identifier: str, body: CatalogConfirmation, rt=Depends(runtime)):
    return await rt.imports.confirm_catalog(identifier, body.version_id)


@router.post("/documents/{identifier}/recognize-catalog", status_code=202)
async def recognize_existing_catalog(identifier: str, rt=Depends(runtime)):
    document = await rt.imports.require_document(identifier)
    if document["status"] in {"deleted", "deleting"}:
        raise BusinessError("document_deleted", "文档已删除或正在删除", 409)
    if document["content_type"] != "catalog":
        raise BusinessError("not_catalog", "仅课程目录支持结构识别", 400)
    version = await rt.repo.get("versions", document["latest_version"])
    data = await rt.blobs.get(version["source_key"])
    return await rt.imports.submit(version["source_name"], data, "catalog", document_id=identifier)


@router.post("/documents/{identifier}/versions", status_code=202)
async def new_version(identifier: str, file: Annotated[UploadFile, File()], rt=Depends(runtime)):
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


@router.delete("/documents/{identifier}", status_code=202)
async def delete_document(identifier: str, rt=Depends(runtime)):
    return await rt.imports.delete(identifier)


@router.get("/documents/{identifier}/source")
async def source(identifier: str, version_id: str | None = None, rt=Depends(runtime)):
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
    if suffix in {".md", ".markdown"}:
        content_type = "text/markdown; charset=utf-8"
    return Response(
        data,
        media_type=content_type,
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''"
            + quote(version["source_name"], safe=""),
            "X-Content-Type-Options": "nosniff",
        },
    )
