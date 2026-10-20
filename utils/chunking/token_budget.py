"""章节解析与预算切分分离；所有预算包含标题前缀和 tokenizer 特殊 token。"""

from hashlib import sha256

from markdown_it import MarkdownIt


class TokenBudgetChunker:
    version = "token-budget-v1"

    def __init__(self, tokenizer, target=512, maximum=768, overlap=64):
        self.tokenizer = tokenizer
        self.target, self.maximum, self.overlap = target, maximum, overlap

    def count(self, text):
        return len(self.tokenizer.encode(text, add_special_tokens=True))

    def split(self, section, metadata):
        """优先保留整行；极端长行按字符边界硬切，原文与字符位置始终可回查。

        原始正文只取源文件子串。代码围栏/表格表头仅在 embedding_text 中补上下文，
        不伪造引用原文。DOCX 字符位置属于标准化正文，原件定位仍使用块编号。
        """
        body = section.body
        if not body.strip():
            return []
        title = " / ".join(section.title_path)
        prefix = (
            f"文件：{metadata['source_name']}\n"
            f"课程：{metadata.get('course_name', '')}\n章节：{title}\n"
        )
        # 很长的文件名和标题也必须计入预算；不允许前缀吞掉全部可用空间。
        if self.count(prefix) >= self.target:
            raise ValueError("文件名或标题过长，超过切片目标预算")
        lines = body.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        blocks = []
        boundaries = set()
        for token in MarkdownIt("commonmark").enable("table").parse(body):
            if token.level == 0 and token.map is not None:
                left, right = token.map
                boundaries.add(offsets[right])
                if token.type in {"fence", "table_open"}:
                    context = (
                        token.markup + token.info + "\n"
                        if token.type == "fence"
                        else "".join(lines[left : left + 2])
                    )
                    blocks.append(
                        (offsets[left], offsets[right], token.type, context, token.markup)
                    )
        chunks, start = [], 0
        while start < len(body):
            context = ""
            current = next((block for block in blocks if block[0] <= start < block[1]), None)
            if current and start > current[0]:
                context = current[3]
            base = prefix + context

            def render(end, base=base, start=start, current=current):
                text = base + body[start:end]
                if current and current[2] == "fence" and end < current[1]:
                    text += "\n" + current[4]
                return text

            low, high, best = start + 1, len(body), start
            while low <= high:
                middle = (low + high) // 2
                if self.count(render(middle)) <= self.target:
                    best, low = middle, middle + 1
                else:
                    high = middle - 1
            if best == start:
                raise ValueError("无法在切片预算内保留正文")
            # 优先按段落或行结束，不因一个很长的块无止境扩大预算。
            if best < len(body):
                complete = [boundary for boundary in boundaries if start < boundary <= best]
                if complete:
                    best = max(complete)
                else:
                    boundary = body.rfind("\n", start, best)
                    if boundary > start:
                        best = boundary + 1
            text = body[start:best]
            embedding_text = render(best)
            count = self.count(embedding_text)
            if count > self.maximum:
                raise ValueError("切片硬上限校验失败")
            source = section.source.model_dump()
            if source["format"] in {"markdown", "normalized"}:
                source["start"] += body[:start].count("\n")
                source["end_exclusive"] = (
                    section.source.start
                    + body[:best].count("\n")
                    + (0 if body[:best].endswith("\n") else 1)
                )
                source["char_start"] = start
                source["char_end_exclusive"] = best
            else:
                source["normalized_char_start"] = start
                source["normalized_char_end_exclusive"] = best
            identifier = sha256(f"{section.section_id}:{start}:{best}".encode()).hexdigest()
            chunks.append(
                {
                    "id": identifier,
                    "section_id": section.section_id,
                    "document_id": section.document_id,
                    "version_id": section.version_id,
                    "title_path": section.title_path,
                    "text": text,
                    "embedding_text": embedding_text,
                    "token_count": count,
                    "source": source,
                    "continuation": bool(start),
                    **metadata,
                }
            )
            # 重叠仅用于短的完整行，避免重复半个代码语句；不超过配置上限。
            next_start = best
            if self.overlap and best < len(body) and not current:
                last_line = text.rstrip("\n").split("\n")[-1] + "\n"
                if len(last_line) < len(text) // 2 and self.count(last_line) <= self.overlap:
                    candidate = body.rfind(last_line, start + 1, best + 1)
                    if candidate > start:
                        next_start = candidate
            start = next_start
        return chunks
