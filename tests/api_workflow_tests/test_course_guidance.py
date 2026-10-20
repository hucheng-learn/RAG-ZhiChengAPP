"""检查课程推荐的追问、选择记忆、分页读取与候选数量。"""

from tests.api_workflow_tests.test_workflows import course, query


def test_guidance_asks_before_recommending_and_remembers_choices(system):
    client, _ = system
    course(client, "python")
    course(client, "java")
    sid = client.post("/api/v1/sessions", json={}).json()["id"]
    result = query(client, sid, question="不知道选什么课程", intent="recommend").json()
    assert result["choices"] == []
    assert "想解决什么问题" in result["answer"]
    result = query(client, sid, question="想用 Python 处理 Excel", intent="recommend").json()
    assert [c["course_id"] for c in result["choices"]] == ["python"]
    assert result["choices"][0]["reason"]
    history = client.get(f"/api/v1/sessions/{sid}/messages").json()["items"]
    assert any(row.get("choices") == result["choices"] for row in history)
    assert client.get("/api/v1/sessions").json()["items"][0]["course_ids"] == []


def test_recommendation_reads_beyond_first_catalog_page(system):
    client, _ = system
    for index in range(205):
        course(client, f"unrelated_{index:03}")
    course(client, "zz_python")
    sid = client.post("/api/v1/sessions", json={}).json()["id"]
    result = query(client, sid, question="想用爬虫抓取数据", intent="recommend").json()
    assert [c["course_id"] for c in result["choices"]] == ["zz_python"]


def test_unknown_goal_does_not_suggest_random_courses(system):
    client, _ = system
    course(client, "python")
    course(client, "java")
    sid = client.post("/api/v1/sessions", json={}).json()["id"]
    result = query(client, sid, question="想学园艺", intent="recommend").json()
    assert result["choices"] == []


def test_unscoped_question_offers_no_more_than_three_choices(system):
    client, _ = system
    for index in range(6):
        course(client, f"python_{index}")
    sid = client.post("/api/v1/sessions", json={}).json()["id"]
    result = query(client, sid, question="爬虫该怎么做").json()
    assert len(result["choices"]) == 3
    assert result["status"] == "needs_clarification"
