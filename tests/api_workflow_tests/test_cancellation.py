"""检查流式请求断连后消息取消、会话占用释放。"""

import asyncio
from types import SimpleNamespace

from api.query_service import stream
from schemas.api import QueryRequest
from tests.api_workflow_tests.test_workflows import course, session, upload, wait_task


def test_stream_disconnect_cancels_message_and_releases_session(system):
    client, runtime = system
    course(client)
    wait_task(client, upload(client)["task_id"])
    sid = session(client)
    runtime.llm.gate.clear()

    async def disconnect():
        body = QueryRequest(session_id=sid, question="Python列表是什么")
        response = await stream(
            body, SimpleNamespace(state=SimpleNamespace(request_id="disconnect")), runtime
        )
        generator = response.body_iterator
        assert "event: meta" in await anext(generator)
        assert "event: status" in await anext(generator)
        pending = asyncio.create_task(anext(generator))
        await asyncio.to_thread(runtime.llm.entered.wait, 3)
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await generator.aclose()
        assert sid not in runtime.query.busy
        rows = await runtime.repo.find("messages", {"session_id": sid, "role": "assistant"})
        assert rows[0]["status"] == "cancelled"
        runtime.llm.gate.set()

    try:
        client.portal.call(disconnect)
    finally:
        runtime.llm.gate.set()
