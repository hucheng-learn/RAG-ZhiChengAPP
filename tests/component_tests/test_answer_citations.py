"""检查回答中的引用编号解析，并通过接口验证数组下标不影响真实引用。"""

import pytest

from services.query import QueryService


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("列表 nums[0] = 10，元组不能修改 point[0][1][2][3]。", {1, 2, 3}),
        ("列表可变[1]；元组不可变[2][3]。", {1, 2, 3}),
        ("修改 `nums[0]`，参考[1]。", {1}),
        ("示例：\n```python\nnums = [0]\nnums[99] = 1\n```\n依据[2]。", {2}),
        ("`nums[1]`", set()),
        ("错误引用[99]，非法引用[0]。", {0, 99}),
    ],
)
def test_citations_distinguish_code_from_sources(answer, expected):
    assert QueryService.answer_citation_ids(answer) == expected


def test_real_answer_with_array_subscripts(system):
    from tests.api_workflow_tests.test_workflows import course, query, session, upload, wait_task

    client, runtime = system
    course(client)
    assert wait_task(client, upload(client)["task_id"])["status"] == "completed"

    async def complete(messages):
        return '{"supported":true,"answer":"列表 nums[0] 可修改[1]。","citation_ids":[1]}'

    runtime.llm.complete = complete
    result = query(client, session(client)).json()
    assert result["status"] == "completed"
    assert result["citations"][0]["id"] == 1
