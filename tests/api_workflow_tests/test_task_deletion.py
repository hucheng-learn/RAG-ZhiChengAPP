"""检查任务删除的状态限制，并确保不删除资料或破坏导入幂等性。"""

import pytest

from tests.api_workflow_tests.test_workflows import course, query, session, upload, wait_task


def test_delete_task_preserves_documents_and_import_idempotency(system):
    client, runtime = system
    course(client)
    task = upload(client)
    identifier = task["task_id"]
    assert wait_task(client, identifier)["status"] == "completed"
    assert client.delete(f"/api/v1/import-tasks/{identifier}").status_code == 204
    assert client.get("/api/v1/import-tasks").json()["items"] == []
    assert client.get(f"/api/v1/import-tasks/{identifier}").status_code == 404
    assert client.delete(f"/api/v1/import-tasks/{identifier}").status_code == 204
    assert client.post(f"/api/v1/import-tasks/{identifier}/retry").status_code == 404
    assert client.get(f"/api/v1/documents/{task['document_id']}/source").status_code == 200
    assert query(client, session(client)).json()["citations"]
    duplicate = upload(client)
    assert duplicate["reused"] is True
    assert duplicate["task_id"] == identifier
    assert runtime.repo.db.tasks.count_documents({}) == 1


@pytest.mark.parametrize(
    "status",
    ["completed", "failed", "interrupted", "cancelled", "queued", "processing", "needs_review"],
)
def test_delete_task_status_rules_and_list_filter(system, status):
    client, runtime = system
    for identifier in ("target", "keep"):
        runtime.repo.db.tasks.insert_one(
            {"id": identifier, "task_id": identifier, "status": status, "created_at": "2026-01-01"}
        )
    response = client.delete("/api/v1/import-tasks/target")
    terminal = status in {"completed", "failed", "interrupted", "cancelled"}
    assert response.status_code == (204 if terminal else 409)
    if not terminal:
        assert response.json()["code"] == "task_busy"
    items = client.get("/api/v1/import-tasks").json()["items"]
    assert {row["id"] for row in items} == ({"keep"} if terminal else {"target", "keep"})
    assert client.delete("/api/v1/import-tasks/missing").status_code == 204
