"""CommonMark 章节识别；token 预算切片属于后续的独立处理步骤。"""

from markdown_it import MarkdownIt

from schemas.document import ParsedDocument, Section, SourceLocation


def parse_markdown(source: str, *, document_id: str, version_id: str) -> ParsedDocument:
    """只认文档顶层标题，保留前言、重复标题和原始行位置，不承担 token 切片。"""
    lines = source.splitlines(keepends=True)
    # 按 CommonMark 规范初始化解析器、显式开启表格支持、将 Markdown 源码解析为 token 序列。
    tokens = MarkdownIt("commonmark").enable("table").parse(source)
    headings = []
    # 提取顶层标题
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.level == 0 and token.map is not None:
            headings.append(
                (token.map[0], token.map[1], int(token.tag[1:]), tokens[index + 1].content)
            )

    sections = []
    stack: list[Section] = []

    # 闭包
    def append(start, end, level=0, title="", heading_start=None):
        # 用栈来维护标题层级树
        # 遇到新标题 level，把栈顶所有级别 ≥ 当前的节点弹出
        # 弹出后栈顶就是当前标题的父节点（如果栈空则无父）
        while stack and stack[-1].heading_level >= level:
            stack.pop()
        parent = stack[-1] if stack else None

        section = Section(
            section_id=f"{document_id}:{version_id}:s{len(sections)}",
            parent_section_id=parent.section_id if parent else None,
            document_id=document_id,
            version_id=version_id,
            section_index=len(sections),
            title=title,
            title_path=([*parent.title_path, title] if parent else [title]) if title else [],
            heading_level=level,
            body="".join(lines[start:end]),
            source=SourceLocation(
                format="markdown",
                unit="line",
                start=start + 1,
                end_exclusive=end + 1,
                heading_start=heading_start,
            ),
        )
        sections.append(section)
        if level:
            stack.append(section)

    if not headings:
        append(0, len(lines))
    else:
        if headings[0][0] > 0:
            append(0, headings[0][0])
        for index, (start, body_start, level, title) in enumerate(headings):
            end = headings[index + 1][0] if index + 1 < len(headings) else len(lines)
            append(body_start, end, level, title, start + 1)
    return ParsedDocument(parser_version="markdown-v1", sections=sections)
