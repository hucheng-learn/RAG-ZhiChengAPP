from services.catalog import CatalogService
from tests.api_workflow_tests.test_workflows import course, upload, wait_task


def test_project_files_share_one_chapter_without_reimport(system):
    client, _ = system
    course(client)
    for name in ("01_掌柜智库项目全景.md", "02_掌柜智库项目环境配置.md"):
        task = upload(client, "# 概述\n正文\n## 配置\n配置内容", filename=name)
        assert wait_task(client, task["task_id"])["status"] == "completed"
    rows = client.get("/api/v1/courses/python/sections").json()["items"]
    assert len(rows) == 4
    assert len({row["document_id"] for row in rows}) == 2
    assert {row["chapter_title"] for row in rows} == {"掌柜智库项目"}
    assert len({row["chapter_id"] for row in rows}) == 1


def test_explicit_project_and_folder_grouping():
    doc = {"id": "a", "source_name": "01_示例项目介绍.md", "project_id": "实战项目"}
    assert CatalogService.chapter_group(doc, {}) == ("project:实战项目", "实战项目")
    doc = {"id": "b", "source_name": "说明.md"}
    assert CatalogService.chapter_group(doc, {"source_path": "课程实战/模块/说明.md"}) == (
        "folder:课程实战",
        "课程实战",
    )
    assert CatalogService.chapter_group(doc, {}) == ("document:b", "说明.md")


def test_questions_page_boundaries_and_active_version_filter(system):
    client, runtime = system

    async def seed():
        await runtime.repo.put("documents", {"id": "d", "status": "ready", "active_version": "v"})
        for index in range(22):
            await runtime.repo.put(
                "questions",
                {
                    "id": f"q{index:02}",
                    "document_id": "d",
                    "version_id": "v",
                    "question_id": f"q{index:02}",
                    "course_id": "python",
                    "stem": "题目",
                    "answer": "保密",
                    "explanation": "保密",
                },
            )
        await runtime.repo.put(
            "questions",
            {
                "id": "q-stale",
                "document_id": "d",
                "version_id": "old",
                "question_id": "stale",
                "course_id": "python",
                "answer": "保密",
            },
        )

    client.portal.call(seed)
    pages = [
        client.get(f"/api/v1/questions?course_id=python&offset={offset}").json()
        for offset in (0, 10, 20)
    ]
    assert [len(page["items"]) for page in pages] == [10, 10, 2]
    assert [page["has_more"] for page in pages] == [True, True, False]
    identifiers = [row["question_id"] for page in pages for row in page["items"]]
    assert len(set(identifiers)) == 22
    assert all(
        "answer" not in row and "explanation" not in row for page in pages for row in page["items"]
    )
    assert client.get("/api/v1/questions?course_id=other").json() == {
        "items": [],
        "has_more": False,
    }


def test_sections_beyond_old_document_limit_are_available(system):
    _, runtime = system

    async def verify():
        for index in range(201):
            document_id = f"d{index:03}"
            await runtime.repo.put(
                "documents",
                {
                    "id": document_id,
                    "import_key": document_id,
                    "status": "ready",
                    "active_version": "v",
                    "source_name": f"{index:03}.md",
                    "course_id": "python",
                },
            )
            await runtime.repo.put(
                "sections",
                {
                    "id": document_id + ":s",
                    "document_id": document_id,
                    "version_id": "v",
                    "section_index": 0,
                },
            )
        rows = await runtime.catalog.sections("python")
        assert len(rows) == 201

    system[0].portal.call(verify)
