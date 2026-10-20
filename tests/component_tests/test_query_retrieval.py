"""验证候选批量读取、排序与活动版本过滤，不降低检索范围。"""

from tests.api_workflow_tests.test_workflows import course, upload, wait_task


def test_retrieval_batches_chunks_and_skips_exhausted_candidate_pool(system):
    client, runtime = system
    course(client)
    task = upload(client)
    wait_task(client, task["task_id"])
    calls = []
    original_find = runtime.repo.find
    original_get = runtime.repo.get
    original_search = runtime.vectors.search

    async def find(collection, *args, **kwargs):
        calls.append(("find", collection))
        return await original_find(collection, *args, **kwargs)

    async def get(collection, identifier):
        calls.append(("get", collection))
        return await original_get(collection, identifier)

    async def search(*args):
        calls.append(("search", "vectors"))
        hits = await original_search(*args)
        return [{"id": "stale", "document_id": task["document_id"], "version_id": "old"}, *hits]

    runtime.repo.find, runtime.repo.get, runtime.vectors.search = find, get, search
    result = client.portal.call(runtime.query.retrieve, {
        "question": "Python列表是什么", "filters": {"course_id": ["python"]}
    })
    assert result["evidence"]
    assert all(row["version_id"] == task["version_id"] for row in result["evidence"])
    assert calls.count(("find", "chunks")) == 1
    assert ("get", "chunks") not in calls
    assert calls.count(("search", "vectors")) == 1
