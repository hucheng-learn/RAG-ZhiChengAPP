"""检查版本已经启用后，任务回执写入失败不会误报版本失败。"""

from tests.api_workflow_tests.test_workflows import course, upload, wait_task


def test_task_receipt_failure_does_not_fail_committed_version(system):
    client, runtime = system
    course(client)
    original = runtime.repo.patch
    failed = False

    async def patch(collection, identifier, fields, **kwargs):
        nonlocal failed
        if collection == "tasks" and "record_count" in fields and not failed:
            failed = True
            raise RuntimeError("simulated receipt failure")
        return await original(collection, identifier, fields, **kwargs)

    runtime.repo.patch = patch
    task = upload(client)
    assert wait_task(client, task["task_id"])["status"] == "completed"
    document = client.get("/api/v1/documents").json()["items"][0]
    assert document["active_version"] == task["version_id"]
    assert document["status"] == "ready"
