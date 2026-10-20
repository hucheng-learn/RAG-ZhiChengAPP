"""资料和任务分页覆盖末页、过滤与旧列表上限。"""

import pytest


@pytest.mark.parametrize("resource", ["documents", "import-tasks"])
def test_manage_pagination(system, resource):
    client, runtime = system
    collection = "documents" if resource == "documents" else "tasks"
    # 超过旧列表上限，并用相同时间验证稳定排序。
    count = 201 if collection == "documents" else 51
    for index in range(count):
        runtime.repo.db[collection].insert_one(
            {
                "id": f"item-{index:03}",
                "import_key": f"item-{index:03}",
                "status": "completed",
                "created_at": "2026-01-01",
            }
        )
    hidden = {"id": "hidden", "status": "deleted", "created_at": "2027-01-01"}
    if collection == "tasks":
        hidden["deleted_at"] = "2026-01-02"
    runtime.repo.db[collection].insert_one(hidden)
    identifiers = []
    for offset in range(0, count, 10):
        response = client.get(f"/api/v1/{resource}?limit=10&offset={offset}")
        assert response.status_code == 200
        page = response.json()
        assert len(page["items"]) == min(10, count - offset)
        assert page["has_more"] == (offset + 10 < count)
        identifiers.extend(row["id"] for row in page["items"])
    assert identifiers == [f"item-{index:03}" for index in reversed(range(count))]
    assert client.get(f"/api/v1/{resource}?limit=10&offset={count}").json() == {
        "items": [], "has_more": False
    }
