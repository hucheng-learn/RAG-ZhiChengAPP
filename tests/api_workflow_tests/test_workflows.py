"""检查导入、更新重试、引用、问答流和题库练习的完整接口流程。"""

import json
import time
from concurrent.futures import ThreadPoolExecutor


def wait_task(client, identifier):
    for _ in range(200):
        row = client.get(f"/api/v1/import-tasks/{identifier}").json()
        if row["status"] in {"completed", "failed", "interrupted", "cancelled"}:
            return row
        time.sleep(0.02)
    raise AssertionError(f"task timeout: {row}")


def course(client, identifier="python"):
    response = client.post("/api/v1/courses", json={"course_id": identifier, "name": identifier})
    assert response.status_code == 201, response.text


def upload(
    client,
    text="# Python\n列表保存多个元素。\n",
    filename="lecture.md",
    course_id="python",
    content_type="lecture",
    document_id=None,
):
    path = (
        f"/api/v1/documents/{document_id}/versions" if document_id else "/api/v1/documents/import"
    )
    data = {"content_type": content_type}
    if course_id:
        data["course_id"] = course_id
    response = client.post(
        path, data=data, files={"file": (filename, text.encode(), "text/markdown")}
    )
    assert response.status_code == 202, response.text
    return response.json()


def session(client, course_id="python"):
    response = client.post("/api/v1/sessions", json={"course_ids": [course_id]})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def query(client, identifier, **kwargs):
    return client.post(
        "/api/v1/query", json={"session_id": identifier, "question": "Python列表是什么", **kwargs}
    )


def test_import_idempotency_citations_and_deleted_snapshot(system):
    client, runtime = system
    course(client)
    task = upload(client)
    assert wait_task(client, task["task_id"])["status"] == "completed"
    duplicate = upload(client)
    assert duplicate["reused"] is True
    assert duplicate["document_id"] == task["document_id"]
    sid = session(client)
    response = query(client, sid)
    assert response.status_code == 200, response.text
    assert response.json()["citations"][0]["text"].strip() == "列表保存多个元素。"
    assert runtime.vectors.last_filters["course_id"] == ["python"]
    source = client.get(f"/api/v1/documents/{task['document_id']}/source")
    assert source.status_code == 200
    deleted = client.delete(f"/api/v1/documents/{task['document_id']}").json()
    assert wait_task(client, deleted["task_id"])["status"] == "completed"
    assert client.get(f"/api/v1/documents/{task['document_id']}/source").status_code == 410
    assert query(client, sid).json()["status"] == "insufficient_evidence"
    history = client.get(f"/api/v1/sessions/{sid}/messages").json()["items"]
    snapshot = next(row for row in history if row["citations"])
    assert snapshot["citations"][0]["source_available"] is False
    assert not runtime.vectors.rows


def test_failed_update_keeps_old_version_and_retry_is_stable(system):
    client, runtime = system
    course(client)
    task = upload(client)
    wait_task(client, task["task_id"])
    runtime.models.fail_encode = True
    update = upload(client, "# Python\n新版列表支持切片。\n", document_id=task["document_id"])
    assert wait_task(client, update["task_id"])["status"] == "failed"
    documents = client.get("/api/v1/documents").json()["items"]
    assert documents[0]["active_version"] == task["version_id"]
    assert documents[0]["status"] == "ready"
    runtime.models.fail_encode = False
    retried = client.post(f"/api/v1/import-tasks/{update['task_id']}/retry").json()
    assert retried["version_id"] == update["version_id"]
    assert wait_task(client, update["task_id"])["status"] == "completed"
    assert (
        client.get("/api/v1/documents").json()["items"][0]["active_version"] == update["version_id"]
    )
    old_source = client.get(
        f"/api/v1/documents/{task['document_id']}/source?version_id={task['version_id']}"
    )
    assert "多个元素" in old_source.text


def test_same_name_does_not_overwrite_and_other_course_cannot_retrieve(system):
    client, _ = system
    course(client)
    course(client, "java")
    first = upload(client)
    second = upload(client, "# Python\n不同文件。\n")
    assert first["document_id"] != second["document_id"]
    wait_task(client, first["task_id"])
    wait_task(client, second["task_id"])
    assert query(client, session(client, "java")).json()["status"] == "insufficient_evidence"


def test_missing_attribution_requires_review_then_builds_new_version(system):
    client, _ = system
    course(client)
    task = upload(client, course_id=None)
    assert task["status"] == "needs_review"
    confirmed = client.patch(
        f"/api/v1/documents/{task['document_id']}", json={"course_id": "python"}
    ).json()
    assert wait_task(client, confirmed["task_id"])["status"] == "completed"


def test_stream_success_failure_and_session_conflict(system):
    client, runtime = system
    course(client)
    wait_task(client, upload(client)["task_id"])
    sid = session(client)
    body = {"session_id": sid, "question": "Python列表是什么"}
    response = client.post("/api/v1/query/stream", json=body)
    assert response.status_code == 200
    assert response.text.count("event: done\n") == 1
    assert "event: error" not in response.text
    event_ids = [
        json.loads(line[6:])["request_id"]
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert len(set(event_ids)) == 1
    runtime.llm.fail = True
    response = client.post("/api/v1/query/stream", json=body)
    assert response.text.count("event: error\n") == 1
    assert "event: done" not in response.text
    assert "secret-api-key" not in response.text
    runtime.llm.fail = False
    runtime.llm.entered.clear()
    runtime.llm.gate.clear()
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(query, client, sid)
        try:
            assert runtime.llm.entered.wait(3)
            assert query(client, sid).status_code == 409
            other = session(client)
            # 不同会话可进入执行，不共用锁或 SSE 队列。
            other_future = pool.submit(query, client, other)
        finally:
            runtime.llm.gate.set()
        assert future.result(timeout=5).status_code == 200
        assert other_future.result(timeout=5).json()["session_id"] == other


def test_fabricated_citation_rejected(system):
    client, runtime = system
    course(client)
    wait_task(client, upload(client)["task_id"])
    runtime.llm.invalid_citation = True
    result = query(client, session(client)).json()
    assert result["status"] == "insufficient_evidence"
    assert result["citations"] == []


def test_insufficient_evidence_flag_overrides_well_formed_citation(system):
    client, runtime = system
    course(client)
    wait_task(client, upload(client)["task_id"])
    runtime.llm.supported = False
    result = query(client, session(client)).json()
    assert result["status"] == "insufficient_evidence"
    assert result["citations"] == []


def test_question_bank_practice_does_not_leak_answers(system):
    client, _ = system
    course(client)
    source = """## Python题库
- 题库编码: python_bank

### python_bank_q001
- **题型**: 单选题
- **题干**: Python列表存储什么？
- **选项**:
A. 多个元素
B. 只能存数字
- **答案**: A
- **解析**: 列表可保存多个元素。
"""
    task = upload(client, source, "bank.md", content_type="question_bank")
    assert wait_task(client, task["task_id"])["status"] == "completed"
    listed = client.get("/api/v1/questions?course_id=python").json()["items"]
    assert len(listed) == 1
    assert "answer" not in listed[0] and "explanation" not in listed[0]
    assert "A. 多个元素" in listed[0]["options"]
    solution = client.get(f"/api/v1/questions/{listed[0]['question_id']}/solution").json()
    assert solution["answer"] == "A"
    result = query(
        client, session(client), intent="exercise", question_code="python_bank_q001"
    ).json()
    assert "answer" not in result["questions"][0]
    solution_result = query(
        client, session(client), intent="solution", question_code="python_bank_q001"
    ).json()
    assert "标准答案：A" in solution_result["answer"]
