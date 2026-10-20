"""检查会话删除、消息清理与忙碌会话保护。"""

def test_delete_session_removes_messages_and_preserves_other_sessions(system):
    client, runtime = system
    first = client.post("/api/v1/sessions", json={"title": "待删除"}).json()["id"]
    second = client.post("/api/v1/sessions", json={"title": "保留"}).json()["id"]
    for identifier in (first, second):
        response = client.post("/api/v1/query", json={"session_id": identifier, "question": "你好"})
        assert response.status_code == 200, response.text
        assert client.get(f"/api/v1/sessions/{identifier}/messages").json()["items"]

    assert client.delete(f"/api/v1/sessions/{first}").status_code == 204
    assert client.get(f"/api/v1/sessions/{first}/messages").status_code == 404
    assert runtime.repo.db.messages.count_documents({"session_id": first}) == 0
    assert [row["id"] for row in client.get("/api/v1/sessions").json()["items"]] == [second]
    assert client.get(f"/api/v1/sessions/{second}/messages").json()["items"]
    assert client.delete(f"/api/v1/sessions/{first}").status_code == 204
    assert first not in runtime.query.busy


def test_delete_busy_session_preserves_history(system):
    client, runtime = system
    identifier = client.post("/api/v1/sessions", json={}).json()["id"]
    client.post("/api/v1/query", json={"session_id": identifier, "question": "你好"})
    before = client.get(f"/api/v1/sessions/{identifier}/messages").json()["items"]
    assert before
    runtime.query.busy.add(identifier)
    try:
        response = client.delete(f"/api/v1/sessions/{identifier}")
        assert response.status_code == 409
        assert response.json()["code"] == "session_busy"
        assert client.get(f"/api/v1/sessions/{identifier}/messages").json()["items"] == before
        assert identifier in runtime.query.busy
    finally:
        runtime.query.busy.discard(identifier)
    assert client.delete(f"/api/v1/sessions/{identifier}").status_code == 204
