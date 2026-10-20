"""上传格式及文件夹路径处理；不将用户路径写入文件系统。"""

import posixpath
import re
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

from common.errors import BusinessError

TEXT_EXTENSIONS = {".md", ".markdown", ".txt"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".jp2", ".webp", ".gif", ".bmp"}
REMOTE_EXTENSIONS = IMAGE_EXTENSIONS | {".pdf", ".doc", ".ppt", ".pptx"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | REMOTE_EXTENSIONS | {".docx"}


def relative_path(value):
    value = value.replace("\\", "/")
    if (
        not value
        or value.startswith("/")
        or re.match(r"^[A-Za-z]:", value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(ord(char) < 32 for char in value)
    ):
        raise BusinessError("invalid_path", "文件夹中存在无效的相对路径")
    return str(PurePosixPath(value))


class _HTMLImages(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs = []
        self.literal_tags = []

    def handle_starttag(self, tag, attrs):
        if tag in {"pre", "code", "script", "style", "textarea"}:
            self.literal_tags.append(tag)
        if tag == "img" and not self.literal_tags:
            src = dict(attrs).get("src")
            if src:
                self.refs.append(src)

    def handle_endtag(self, tag):
        if self.literal_tags and self.literal_tags[-1] == tag:
            self.literal_tags.pop()


def _block_image_references(block):
    """只检查渲染内容，避免将 Markdown 代码示例当作图片。"""
    html = _HTMLImages()
    refs = []
    for token in [block, *(block.children or [])]:
        if token.type == "image" and token.attrGet("src"):
            refs.append(token.attrGet("src"))
        elif token.type in {"html_inline", "html_block"}:
            html.feed(token.content)
            refs.extend(html.refs)
            html.refs.clear()
    return list(dict.fromkeys(refs))


def image_references(source, env=None):
    return list(dict.fromkeys(
        ref
        for block in MarkdownIt("commonmark").parse(source, env)
        for ref in _block_image_references(block)
    ))


def append_image_text(source, texts):
    lines = source.splitlines(keepends=True)
    insertions = {}
    for token in MarkdownIt("commonmark").parse(source):
        if not token.map:
            continue
        refs = _block_image_references(token)
        content = [f"图片文字（{ref}）：\n{texts[ref]}" for ref in refs if ref in texts]
        if content:
            # 引用块中的标题不会成为新的顶层题目；文字仍可被识别器读取。
            rendered = (
                "\n\n"
                + "\n\n".join(
                    "\n".join("> " + line for line in text.splitlines()) for text in content
                )
                + "\n\n"
            )
            insertions.setdefault(token.map[1], []).append(rendered)
    for end in sorted(insertions, reverse=True):
        lines.insert(end, "".join(insertions[end]))
    return "".join(lines)


def resolve_image(document_path, reference):
    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None
    candidate = posixpath.normpath(
        posixpath.join(posixpath.dirname(document_path), unquote(parsed.path).replace("\\", "/"))
    )
    try:
        return relative_path(candidate)
    except BusinessError:
        return None
