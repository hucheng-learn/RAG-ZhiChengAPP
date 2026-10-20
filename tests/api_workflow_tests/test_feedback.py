"""检查版本名称、章节归属，以及流式草稿和最终引用的反馈。"""

import asyncio
import json
from types import SimpleNamespace

from api.query_service import stream
from schemas.api import QueryRequest
from tests.api_workflow_tests.test_workflows import course, session, upload, wait_task


def test_task_names_follow_versions_and_sections_include_document(system):
    client, _ = system
    course(client)
    first = upload(client, filename="original.md")
    wait_task(client, first["task_id"])
    second = upload(client, filename="updated.md", document_id=first["document_id"])
    wait_task(client, second["task_id"])
    tasks = {row["id"]: row for row in client.get("/api/v1/import-tasks").json()["items"]}
    assert tasks[first["task_id"]]["source_name"] == "original.md"
    assert tasks[second["task_id"]]["source_name"] == "updated.md"
    sections = client.get("/api/v1/courses/python/sections").json()["items"]
    assert sections and all(row["source_name"] for row in sections)


def test_sse_emits_model_draft_before_completion_and_replaces_invalid_citations(system):
    client, runtime = system
    course(client)
    wait_task(client, upload(client)["task_id"])
    sid = session(client)

    async def verify():
        released = asyncio.Event()

        async def model_stream(messages):
            yield '{"supported":true,"answer":"实时草稿'
            await released.wait()
            yield ' [99]","citation_ids":[99]}'

        runtime.llm.stream = model_stream
        response = await stream(
            QueryRequest(session_id=sid, question="列表是什么"),
            SimpleNamespace(state=SimpleNamespace(request_id="live-stream")),
            runtime,
        )
        generator = response.body_iterator
        try:
            assert "event: meta" in await anext(generator)
            assert "event: status" in await anext(generator)
            # 各查询阶段都会推送状态事件，数量随流程调整；跳过状态直至首个模型 token
            token = None
            while token is None:
                item = await asyncio.wait_for(anext(generator), 3)
                if "event: token" in item:
                    token = item
                else:
                    assert "event: status" in item
            assert "实时草稿" in token
            assert not released.is_set()
            released.set()
            remaining = [item async for item in generator]
            final = next(item for item in remaining if "event: done" in item)
            result = json.loads(final.split("data: ", 1)[1])["result"]
            assert result["status"] == "insufficient_evidence"
            assert "实时草稿" not in result["answer"]
            assert result["citations"] == []
            rows = await runtime.repo.find("messages", {"session_id": sid, "role": "assistant"})
            assert rows[0]["content"] == result["answer"]
        finally:
            released.set()
            await generator.aclose()

    client.portal.call(verify)
