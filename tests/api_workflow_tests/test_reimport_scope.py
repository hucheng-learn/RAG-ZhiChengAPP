"""检查删除后重新导入，以及课程范围不明确时的澄清。"""

from tests.api_workflow_tests.test_workflows import course, query, session, upload, wait_task


def test_deleted_file_can_be_imported_again_without_resurrecting_tombstone(system):
    client, _ = system
    course(client)
    first = upload(client)
    wait_task(client, first["task_id"])
    deleted = client.delete(f"/api/v1/documents/{first['document_id']}").json()
    assert wait_task(client, deleted["task_id"])["status"] == "completed"
    second = upload(client)
    assert second["document_id"] != first["document_id"]
    assert wait_task(client, second["task_id"])["status"] == "completed"
    assert query(client, session(client)).json()["citations"]


def test_ambiguous_scope_requires_clarification(system):
    client, runtime = system
    course(client)
    course(client, "java")
    sid = client.post("/api/v1/sessions", json={}).json()["session_id"]
    response = query(client, sid, question="列表是什么")
    assert response.json()["status"] == "needs_clarification"
    assert runtime.vectors.last_filters is None
