"""检查切片 token 预算，以及课程目录和题库的结构解析。"""

from tests.fakes import CharacterTokenizer
from utils.chunking.token_budget import TokenBudgetChunker
from utils.parsers.education import parse_catalog, parse_questions
from utils.parsers.markdown import parse_markdown


def test_long_code_table_and_single_line_stay_within_budget():
    body = "# Python\n```python\n" + "print('hello')\n" * 30 + "```\n\n" + "x" * 500
    parsed = parse_markdown(body, document_id="d", version_id="v")
    section = parsed.sections[0]
    chunker = TokenBudgetChunker(CharacterTokenizer(), target=100, maximum=130, overlap=10)
    chunks = chunker.split(section, {"source_name": "a.md", "course_name": "Python"})
    assert len(chunks) > 5
    assert all(chunk["token_count"] <= 130 for chunk in chunks)
    for chunk in chunks:
        location = chunk["source"]
        assert (
            chunk["text"] == section.body[location["char_start"] : location["char_end_exclusive"]]
        )
    assert len({chunk["id"] for chunk in chunks}) == len(chunks)


def test_catalog_keeps_series_and_course_codes():
    source = """# 课程
## Python入门
- **系列编码**: python
- **描述**: Python基础

### 课程
- **变量与数据类型**
  - 编码: python_m1, 课时: 8
  - 描述: 变量
"""
    series, courses = parse_catalog(source, "d", "v")
    assert series[0]["series_id"] == "python"
    assert courses[0]["course_id"] == "python_m1"
    assert courses[0]["name"] == "变量与数据类型"


def test_question_fence_does_not_create_fake_question_or_answer():
    source = """## 题库
- 题库编码: bank
### bank_q001
- **题型**: 简答题
- **题干**: 解释代码
```python
### fake_q002
# **答案**: 假答案
```
- **答案**: 真答案
- **解析**: 真实解析
"""
    rows = parse_questions(source, "d", "v", "python")
    assert len(rows) == 1
    assert rows[0]["answer"] == "真答案"
    assert "fake_q002" in rows[0]["stem"]
