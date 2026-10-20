"""上传格式及文件夹路径处理；不将用户路径写入文件系统。"""

import posixpath
import re
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


def image_references(source, env=None):
    refs = []
    for block in MarkdownIt("commonmark").parse(source, env):
        for token in block.children or []:
            if token.type == "image" and token.attrGet("src"):
                src = token.attrGet("src")
                if src not in refs:
                    refs.append(src)
    return refs


def append_image_text(source, texts):
    lines = source.splitlines(keepends=True)
    insertions = {}
    for token in MarkdownIt("commonmark").parse(source):
        if not token.map:
            continue
        refs = {child.attrGet("src") for child in token.children or [] if child.type == "image"}
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
