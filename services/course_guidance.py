"""按真实课程元数据提供可解释的候选，不把候选自动变成检索范围。"""

import re

TOPICS = (
    ("python", "爬虫", "数据分析", "自动化脚本"),
    ("java", "spring", "后端开发"),
    ("前端", "html", "css", "javascript", "vue", "react", "网页"),
    ("数据库", "sql", "mysql", "查询语句"),
    ("linux", "运维", "docker", "服务器"),
    ("人工智能", "机器学习", "深度学习", "大模型", "rag"),
    ("测试", "pytest", "自动化测试"),
)


def recommend(courses, question):
    """只使用名称、别名和简介匹配；信息不足时追问，不随机推荐。"""
    question = question.casefold()
    tokens = set(re.findall(r"[a-z][a-z0-9+#.]*|[\u4e00-\u9fff]{2,}", question))
    # 中文没有空格，用连续双字词辅助匹配；排除选课话术中的泛词。
    generic = "不知道选什么课程推荐学习一下怎么哪个适合我想可以基础入门"
    fragments = {
        token[i : i + 2]
        for token in tokens
        if re.fullmatch(r"[\u4e00-\u9fff]+", token)
        for i in range(len(token) - 1)
        if token[i : i + 2] not in generic
    }
    ranked = []
    for course in courses:
        labels = [course["name"], *course.get("aliases", [])]
        metadata = " ".join([*labels, course.get("description", "")]).casefold()
        exact = [label for label in labels if label and label.casefold() in question]
        terms = sorted({t for t in tokens | fragments if t in metadata and t not in generic})
        topics = [
            group[0]
            for group in TOPICS
            if any(term in question for term in group) and any(term in metadata for term in group)
        ]
        score = len(exact) * 10 + len(terms) * 2 + len(topics) * 3
        if not score:
            continue
        matched = list(dict.fromkeys(term.casefold() for term in [*exact, *terms, *topics]))[:3]
        ranked.append(
            (
                score,
                {
                    "course_id": course["course_id"],
                    "name": course["name"],
                    "reason": "课程名称或简介与「" + "、".join(matched) + "」相关",
                    "material_status": course.get("material_status", "missing"),
                },
            )
        )
    ranked.sort(key=lambda item: (-item[0], item[1]["name"]))
    if not ranked:
        return []
    minimum = ranked[0][0] * 0.35
    return [item[1] for item in ranked[:3] if item[0] >= minimum]
