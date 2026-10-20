"""面向现有教育资料格式的结构化导入，不把题目与答案拆成普通切片。"""

import re

from utils.parsers.markdown import parse_markdown


def fields(body: str) -> dict[str, str]:
    """仅在段落级列表字段中识别标签；代码围栏里的答案等标签不会被误解析。"""
    from markdown_it import MarkdownIt

    tokens = MarkdownIt("commonmark").parse(body)
    entries = []
    for token in tokens:
        if token.type != "inline" or token.map is None:
            continue
        match = re.match(
            r"^\*{0,2}([^:*\n：]+)\*{0,2}[:：]\s*(.*)", token.content.split("\n", 1)[0]
        )
        if match:
            entries.append((token.map[0], match[1].strip(), match[2].strip()))
    lines = body.splitlines()
    result = {}
    for index, (start, key, first) in enumerate(entries):
        end = entries[index + 1][0] if index + 1 < len(entries) else len(lines)
        result[key] = "\n".join([first, *lines[start + 1 : end]]).strip()
    return result


def parse_catalog(source: str, document_id: str, version_id: str):
    """旧格式离线兼容解析；生产目录导入使用 catalog.recognize_catalog。"""
    sections = parse_markdown(source, document_id=document_id, version_id=version_id).sections
    series, courses, current = [], [], None
    for section in sections:
        if section.heading_level == 2:
            data = fields(section.body)
            code = data.get("系列编码")
            if not code:
                raise ValueError(f"系列缺少编码：{section.title}")
            current = {
                "id": f"{document_id}:{version_id}:{code}",
                "series_id": code,
                "name": section.title,
                "description": data.get("描述", ""),
                "document_id": document_id,
                "version_id": version_id,
            }
            series.append(current)
        elif section.heading_level == 3 and current:
            # 课程文件格式固定：一级列表是课程名，嵌套列表包含编码和描述。
            from markdown_it import MarkdownIt

            tokens = MarkdownIt("commonmark").parse(section.body)
            name = None
            for token in tokens:
                if token.type != "inline":
                    continue
                text = token.content
                if text.startswith("**") and text.endswith("**"):
                    name = text.strip("*")
                match = re.match(r"编码[:：]\s*([\w-]+)", text)
                if match and name:
                    code = match[1]
                    courses.append(
                        {
                            "id": f"{document_id}:{version_id}:{code}",
                            "course_id": code,
                            "series_id": current["series_id"],
                            "name": name,
                            "aliases": [],
                            "description": current["description"],
                            "document_id": document_id,
                            "version_id": version_id,
                        }
                    )
                    name = None
    if not series or not courses:
        raise ValueError("未识别出有效课程系列和课程条目")
    return series, courses


def parse_questions(source: str, document_id: str, version_id: str, course_id: str | None):
    """题干、选项、答案、解析保持同一业务对象；向量文本由调用方排除答案。"""
    sections = parse_markdown(source, document_id=document_id, version_id=version_id).sections
    bank_id = None
    questions = []
    for section in sections:
        if section.heading_level == 2:
            bank_id = fields(section.body).get("题库编码")
        elif section.heading_level == 3:
            data = fields(section.body)
            if not bank_id or not data.get("题干") or not data.get("答案"):
                raise ValueError(f"题目字段不完整：{section.title}")
            questions.append(
                {
                    "id": f"{document_id}:{version_id}:{section.title}",
                    "question_id": f"{document_id}:{section.title}",
                    "code": section.title,
                    "bank_id": bank_id,
                    "course_id": course_id,
                    "question_type": data.get("题型", "未知"),
                    "stem": data["题干"],
                    "options": data.get("选项", ""),
                    "answer": data["答案"],
                    "explanation": data.get("解析", ""),
                    "knowledge_points": [
                        item.strip() for item in data.get("知识点", "").split(",") if item.strip()
                    ],
                    "document_id": document_id,
                    "version_id": version_id,
                    "source": section.source.model_dump(),
                    "review_status": "unreviewed",
                }
            )
    if not questions:
        raise ValueError("未识别出题目，请核对题库 Markdown 格式")
    return questions
