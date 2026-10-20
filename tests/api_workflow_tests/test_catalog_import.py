"""检查课程目录从上传、识别、人工核对到启用的接口流程。"""

import asyncio
import time
from copy import deepcopy

from tests.api_workflow_tests.test_workflows import upload
from tests.component_tests.test_catalog_recognition import RESPONSE, SOURCE, CatalogLLM


def wait_catalog(client, task):
    for _ in range(200):
        result = client.get(f"/api/v1/import-tasks/{task['task_id']}").json()
        if result["status"] in {"completed", "failed", "needs_review"}:
            return result
        time.sleep(0.02)
    raise AssertionError(result)


def test_model_catalog_import_and_legacy_series_are_browsable(system):
    client, runtime = system
    runtime.llm.complete = CatalogLLM(RESPONSE).complete
    task = upload(client, SOURCE, "catalog.md", course_id=None, content_type="catalog")
    done = wait_catalog(client, task)
    assert done["status"] == "completed", done
    assert done["catalog_completed_batches"] == done["catalog_batches"] == 1
    assert done["catalog_batch"] == 1
    listed = client.get("/api/v1/import-tasks").json()["items"]
    assert listed[0]["content_type"] == "catalog"
    assert done["catalog_report"]["series_count"] == 1
    assert len(client.get("/api/v1/courses/series").json()["items"]) == 1
    courses = client.get("/api/v1/courses").json()["items"]
    assert len(courses) == 2
    assert all(row["series_id"] == "general" for row in courses)
    report = client.get(f"/api/v1/documents/{task['document_id']}/catalog-review").json()
    assert report["status"] == "ready" and report["course_count"] == 2


def test_review_preserves_active_version_and_requires_exact_version(system):
    client, runtime = system
    runtime.llm.complete = CatalogLLM(RESPONSE).complete
    first = upload(client, SOURCE, "catalog.md", course_id=None, content_type="catalog")
    assert wait_catalog(client, first)["status"] == "completed"
    response = deepcopy(RESPONSE)
    response["courses"][1]["uncertain"] = True
    runtime.llm.complete = CatalogLLM(response).complete
    second = upload(
        client,
        SOURCE + "\n共 219 个课程系列",
        "catalog.md",
        course_id=None,
        content_type="catalog",
        document_id=first["document_id"],
    )
    assert wait_catalog(client, second)["status"] == "needs_review"
    assert client.get("/api/v1/courses").json()["items"][0]["version_id"] == first["version_id"]
    path = f"/api/v1/documents/{first['document_id']}/catalog-review"
    assert client.post(path, json={"version_id": "stale"}).status_code == 409
    assert client.post(path, json={"version_id": second["version_id"]}).status_code == 200
    assert client.get("/api/v1/courses").json()["items"][0]["version_id"] == second["version_id"]
    assert client.post(path, json={"version_id": second["version_id"]}).status_code == 409


def test_catalog_ignores_stale_upload_course_and_project_selection(system):
    client, runtime = system
    runtime.llm.complete = CatalogLLM(RESPONSE).complete
    response = client.post(
        "/api/v1/documents/import",
        data={"content_type": "catalog", "course_id": "old-course", "project_id": "old-project"},
        files={"file": ("catalog.md", SOURCE.encode(), "text/markdown")},
    )
    assert response.status_code == 202, response.text
    assert wait_catalog(client, response.json())["status"] == "completed"
    document = client.get("/api/v1/documents").json()["items"][0]
    assert document["course_id"] is None and document["project_id"] is None


def test_catalog_timeout_preserves_existing_courses_and_explains_failed_batch(system):
    client, runtime = system
    runtime.llm.complete = CatalogLLM(RESPONSE).complete
    first = upload(client, SOURCE, "catalog.md", course_id=None, content_type="catalog")
    assert wait_catalog(client, first)["status"] == "completed"

    async def stalled(messages):
        await asyncio.Future()

    runtime.settings.llm_timeout_seconds = 0.02
    runtime.llm.complete = stalled
    second = upload(
        client,
        SOURCE + "\n新版说明",
        "catalog.md",
        course_id=None,
        content_type="catalog",
        document_id=first["document_id"],
    )
    done = wait_catalog(client, second)
    assert done["status"] == "failed"
    assert "第 1/1 批识别超时" in done["error"]
    assert done["catalog_completed_batches"] == 0
    assert done["catalog_batch_started_at"]
    document = client.get("/api/v1/documents").json()["items"][0]
    assert document["active_version"] == first["version_id"]
    courses = client.get("/api/v1/courses").json()["items"]
    assert len(courses) == 2 and all(row["version_id"] == first["version_id"] for row in courses)


def test_pending_catalog_can_be_replaced_and_hallucinations_never_activate(system):
    client, runtime = system
    response = deepcopy(RESPONSE)
    response["series"][0]["uncertain"] = True
    runtime.llm.complete = CatalogLLM(response).complete
    first = upload(client, SOURCE, "catalog.md", course_id=None, content_type="catalog")
    assert wait_catalog(client, first)["status"] == "needs_review"
    assert client.get("/api/v1/courses/series").json()["items"] == []
    response["courses"][0]["name"] = "编造课程"
    second = upload(
        client,
        SOURCE + "\n修订",
        "catalog.md",
        course_id=None,
        content_type="catalog",
        document_id=first["document_id"],
    )
    assert wait_catalog(client, second)["status"] == "failed"
    assert client.get("/api/v1/courses").json()["items"] == []
    assert client.get(f"/api/v1/import-tasks/{first['task_id']}").json()["status"] == "cancelled"


def test_219_legacy_series_paginate_without_reimport(system):
    client, runtime = system
    runtime.repo.db.documents.insert_one({"id": "legacy", "status": "ready", "active_version": "v"})
    runtime.repo.db.series.insert_many(
        [
            {
                "id": f"legacy:v:s{i:03}",
                "series_id": f"s{i}",
                "name": f"系列 {i}",
                "document_id": "legacy",
                "version_id": "v",
            }
            for i in range(219)
        ]
    )
    first = client.get("/api/v1/courses/series?limit=200").json()["items"]
    second = client.get("/api/v1/courses/series?limit=200&offset=200").json()["items"]
    assert len(first) == 200 and len(second) == 19
    assert len({row["series_id"] for row in first + second}) == 219


def test_existing_legacy_catalog_can_use_stored_source_for_model_recognition(system):
    client, runtime = system
    runtime.repo.db.documents.insert_one(
        {
            "id": "legacy",
            "import_key": "legacy",
            "content_type": "catalog",
            "source_name": "catalog.md",
            "status": "ready",
            "active_version": "old",
            "latest_version": "old",
            "course_id": "stale-course",
        }
    )
    runtime.repo.db.versions.insert_one(
        {
            "id": "old",
            "document_id": "legacy",
            "content_type": "catalog",
            "source_name": "catalog.md",
            "source_key": "legacy/source.md",
            "status": "ready",
            "course_id": "stale-course",
        }
    )
    runtime.blobs.objects["legacy/source.md"] = SOURCE.encode()
    runtime.llm.complete = CatalogLLM(RESPONSE).complete
    response = client.post("/api/v1/documents/legacy/recognize-catalog")
    assert response.status_code == 202, response.text
    assert wait_catalog(client, response.json())["status"] == "completed"
    assert len(client.get("/api/v1/courses/series").json()["items"]) == 1
    document = client.get("/api/v1/documents").json()["items"][0]
    assert document["course_id"] is None and document["project_id"] is None
