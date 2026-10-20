"""用固定模型响应检查课程目录识别、分批处理、超时与结果校验。"""

import asyncio
import json
from copy import deepcopy

import pytest

from common.errors import BusinessError
from utils.parsers.catalog import catalog_batches, recognize_catalog

SOURCE = """# 培训目录
通用编程入门班
系列编码：general
包含课程：
1. 语法基础与开发环境
   编码：general_m1
2. 流程控制与数据结构入门
"""
RESPONSE = {
    "series": [{"name": "通用编程入门班", "name_line": 2, "code": "general", "code_line": 3}],
    "courses": [
        {
            "name": "语法基础与开发环境",
            "name_line": 5,
            "code": "general_m1",
            "code_line": 6,
            "series_line": 2,
        },
        {"name": "流程控制与数据结构入门", "name_line": 7, "series_line": 2},
    ],
}


class CatalogLLM:
    def __init__(self, response):
        self.response = response
        self.messages = []

    async def complete(self, messages):
        self.messages.append(messages)
        return json.dumps(self.response, ensure_ascii=False)


def recognize(response=None, source=SOURCE, version="v"):
    return asyncio.run(
        recognize_catalog(source, "document", version, CatalogLLM(response or deepcopy(RESPONSE)))
    )


def test_free_form_catalog_no_declared_counts_and_stable_generated_code():
    series, courses, report = recognize()
    assert len(series) == 1 and len(courses) == 2
    assert report["issues"] == []
    assert courses[0]["series_id"] == "general"
    assert courses[0]["course_id"] == "general_m1"
    assert courses[1]["generated_code"] is True
    assert courses[1]["source"]["start"] == 7
    assert recognize(version="next")[1][1]["course_id"] == courses[1]["course_id"]


def test_stalled_batch_is_cancelled_at_deadline_and_identified_in_error():
    cancelled = []

    class StalledLLM:
        async def complete(self, messages):
            try:
                await asyncio.Future()
            finally:
                cancelled.append(True)

    with pytest.raises(BusinessError, match=r"第 1/1 批识别超时") as error:
        asyncio.run(recognize_catalog(SOURCE, "document", "v", StalledLLM(), timeout_seconds=0.01))
    assert error.value.code == "catalog_timeout"
    assert cancelled == [True]


@pytest.mark.parametrize(
    "mutation,message",
    [
        (lambda r: r["courses"][0].update(name="模型编造的课程"), "名称无法对应"),
        (lambda r: r["courses"][0].update(code="fake_code"), "编码无法对应"),
        (lambda r: r["courses"][0].update(series_line=99), "缺少可核实"),
        (lambda r: r["courses"][0].update(description="凭空生成的简介"), "描述无法对应"),
        (lambda r: r["courses"][0].update(name_line=1000), "名称无法对应"),
        (lambda r: r["courses"][1].update(code="general_m1", code_line=6), "重复编码"),
    ],
)
def test_unverifiable_model_output_is_rejected(mutation, message):
    response = deepcopy(RESPONSE)
    mutation(response)
    with pytest.raises(ValueError, match=message):
        recognize(response)


def test_declared_count_mismatch_missing_codes_and_uncertainty_require_review():
    response = deepcopy(RESPONSE)
    response["courses"] = [response["courses"][1]]
    response["courses"][0]["uncertain"] = True
    _, _, report = recognize(response, SOURCE + "\n共 219 个课程系列\n")
    assert any("219" in issue for issue in report["issues"])
    assert any("general_m1" in issue for issue in report["issues"])
    assert any("层级或条目需确认" in issue for issue in report["issues"])


def test_fenced_examples_do_not_add_declared_counts_or_missing_codes():
    _, _, report = recognize(source=SOURCE + "\n```\n共219个课程系列\n编码：fake\n```\n")
    assert report["issues"] == []


def test_series_without_courses_and_independent_courses_are_valid():
    response = deepcopy(RESPONSE)
    response["courses"][0]["series_line"] = None
    series, courses, _ = recognize(response)
    assert len(series) == 1 and courses[0]["series_id"] is None
    assert recognize({"series": response["series"], "courses": []})[0]


def test_batches_cover_all_lines_and_carry_heading_context():
    source = "# 目录\n## 入门班\n" + "- 课程内容\n" * 200
    batches = list(catalog_batches(source, maximum=300))
    assert len(batches) > 1
    assert [row["line"] for batch in batches for row in batch["lines"]] == list(
        range(1, len(source.splitlines()) + 1)
    )
    assert any(row["line"] == 2 for row in batches[-1]["context_lines"])


def test_dense_catalog_batches_limit_rows_without_rejecting_long_paragraphs():
    source = "# 目录\n## 系列\n" + "".join(f"- 课程 {index}\n" for index in range(300))
    batches = list(catalog_batches(source))
    assert len(batches) > 1
    assert all(len(batch["lines"]) <= 80 for batch in batches)
    assert [row["line"] for batch in batches for row in batch["lines"]] == list(
        range(1, len(source.splitlines()) + 1)
    )
    assert any(row["line"] == 2 for row in batches[-1]["context_lines"])
    assert len(list(catalog_batches("一段较长的目录介绍" * 900))) == 1


def test_series_code_on_next_batch_can_enrich_a_previously_seen_name():
    source = "背景说明\n" * 79 + "## 通用编程入门班\n系列编码：general\n"

    class SplitEntryLLM:
        async def complete(self, messages):
            batch = json.loads(messages[1][1])
            visible = {row["line"] for row in batch["lines"] + batch["context_lines"]}
            entry = {"name": "通用编程入门班", "name_line": 80}
            if 81 in visible:
                entry.update(code="general", code_line=81)
            return json.dumps({"series": [entry], "courses": []}, ensure_ascii=False)

    series, _, report = asyncio.run(recognize_catalog(source, "doc", "v", SplitEntryLLM()))
    assert series[0]["series_id"] == "general"
    assert series[0]["code_source_line"] == 81
    assert series[0]["generated_code"] is False
    assert report["issues"] == []


def test_model_cannot_import_courses_from_code_examples():
    response = deepcopy(RESPONSE)
    response["courses"].append({"name": "假课程", "name_line": 10, "series_line": 2})
    with pytest.raises(ValueError, match="名称无法对应"):
        recognize(response, SOURCE + "\n```\n假课程\n```\n")
