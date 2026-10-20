"""MinerU v4：异步提交、限时轮询、受限下载；不解压到任意磁盘路径。"""

import asyncio
import zipfile
from io import BytesIO
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit

import httpx

from common.errors import BusinessError
from utils.parsers.files import IMAGE_EXTENSIONS, relative_path


class MinerUClient:
    def __init__(self, settings, *, transport=None):
        self.settings = settings
        self.transport = transport

    @property
    def available(self):
        return bool(
            self.settings.mineru_base_url and self.settings.mineru_api_token.get_secret_value()
        )

    def require(self):
        if not self.available:
            raise BusinessError(
                "mineru_unavailable", "此格式的文件解析暂时不可用，请稍后重试。", 503
            )

    @staticmethod
    def _url(value):
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
            raise ValueError("文件解析结果暂时不可用，请稍后重试。")
        return value

    @staticmethod
    def _error_message(code, message, fallback):
        """只将已知错误转换成固定文案，绝不回传上游原文或签名地址。"""
        code = str(code)
        message = message.casefold() if isinstance(message, str) else ""
        if code == "-60006" or any(
            text in message
            for text in ("number of pages exceeds limit", "page count exceeds", "文件页数超过")
        ):
            return "文件超过 200 页，请拆分后重新上传。"
        if code in {"-60015", "-60016"} or any(
            text in message for text in ("file conversion failed", "文件转换失败")
        ):
            return "文件转换失败，请另存为 PDF 后重新上传。"
        if code in {"-60003", "-60004", "-60021"} or any(
            text in message for text in ("file read failed", "文件读取失败", "空文件")
        ):
            return "无法读取文件，请确认文件可正常打开，或重新另存后上传。"
        if code == "-60005":
            return "文件超过 200 MB，请缩小文件后重新上传。"
        if code == "-60002":
            return "无法识别文件格式，请确认文件内容与扩展名一致后重新上传。"
        if code == "-60008" or any(
            text in message for text in ("file read timeout", "文件读取超时")
        ):
            return "文件解析超时，请稍后重试。"
        if code in {"A0202", "A0211", "-10001", "-60007", "-60009", "-60018", "-60019"}:
            return "文件解析暂时不可用，请稍后重试。"
        return fallback

    @staticmethod
    def _result(response):
        response.raise_for_status()
        body = response.json()
        if body.get("code") != 0:
            # 上游错误可能含签名地址或文件正文，不原样展示。
            raise BusinessError(
                "mineru_rejected",
                MinerUClient._error_message(
                    body.get("code"), body.get("msg"), "文件解析暂时不可用，请稍后重试。"
                ),
            )
        return body["data"]

    def unpack(self, data):
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > 10000 or sum(i.file_size for i in entries) > (
                    self.settings.max_docx_unpacked_bytes
                ):
                    raise ValueError("解析后的内容超过大小限制，请拆分文件后重新上传。")
                paths = {relative_path(i.filename): i for i in entries if not i.is_dir()}
                markdowns = [name for name in paths if name.lower().endswith(".md")]
                if not markdowns:
                    raise ValueError("文件解析未生成正文，请检查文件或重新另存后上传。")
                chosen = next(
                    (p for p in markdowns if PurePosixPath(p).name == "full.md"),
                    sorted(markdowns)[0],
                )
                source = archive.read(paths[chosen]).decode("utf-8-sig")
                parent = PurePosixPath(chosen).parent
                assets = {}
                for name, entry in paths.items():
                    path = PurePosixPath(name)
                    if path.suffix.lower() in IMAGE_EXTENSIONS and path.is_relative_to(parent):
                        assets[str(path.relative_to(parent))] = archive.read(entry)
                if not source.strip():
                    raise ValueError("未识别出有效正文，请检查文件内容后重新上传。")
                return source, assets
        except (zipfile.BadZipFile, UnicodeDecodeError, KeyError) as exc:
            raise ValueError("文件解析结果无效，请稍后重试。") from exc

    async def parse(self, filename, data, progress=None):
        self.require()
        base = self.settings.mineru_base_url.rstrip("/")
        headers = {"Authorization": "Bearer " + self.settings.mineru_api_token.get_secret_value()}
        try:
            async with asyncio.timeout(self.settings.mineru_timeout_seconds):
                async with httpx.AsyncClient(
                    timeout=120, transport=self.transport, follow_redirects=True
                ) as client:
                    batch = self._result(
                        await client.post(
                            base + "/file-urls/batch",
                            headers=headers,
                            json={
                                "files": [{"name": PurePosixPath(filename).name, "is_ocr": True}],
                                "model_version": self.settings.mineru_model_version,
                                "enable_table": True,
                                "enable_formula": True,
                            },
                        )
                    )
                    # 上传/下载使用签名地址，绝不携带 MinerU Authorization。
                    upload = self._url(batch["file_urls"][0])
                    response = await client.put(upload, content=data)
                    response.raise_for_status()
                    batch_id = quote(batch["batch_id"], safe="")
                    while True:
                        if progress:
                            await progress("recognizing_document")
                        try:
                            response = await client.get(
                                base + "/extract-results/batch/" + batch_id, headers=headers
                            )
                            if response.status_code >= 500 or response.status_code == 429:
                                await asyncio.sleep(self.settings.mineru_poll_seconds)
                                continue
                            result = self._result(response)["extract_result"][0]
                        except (httpx.TimeoutException, httpx.NetworkError):
                            await asyncio.sleep(self.settings.mineru_poll_seconds)
                            continue
                        if result["state"] == "failed":
                            raise BusinessError(
                                "mineru_failed",
                                self._error_message(
                                    result.get("err_code", result.get("code")),
                                    result.get("err_msg"),
                                    "文件解析失败，请检查文件或稍后重试。",
                                ),
                            )
                        if result["state"] == "done":
                            url = self._url(result["full_zip_url"])
                            archive = bytearray()
                            # 旧项目已验证 CDN 下载需独立控制代理；API 仍沿用系统代理。
                            async with httpx.AsyncClient(
                                timeout=120,
                                transport=self.transport,
                                follow_redirects=True,
                                trust_env=self.settings.mineru_download_trust_env,
                            ) as downloads:
                                async with downloads.stream("GET", url) as response:
                                    response.raise_for_status()
                                    async for block in response.aiter_bytes():
                                        if (
                                            len(archive) + len(block)
                                            > self.settings.max_docx_unpacked_bytes
                                        ):
                                            raise ValueError(
                                                "解析后的内容超过大小限制，请拆分文件后重新上传。"
                                            )
                                        archive.extend(block)
                            return await asyncio.to_thread(self.unpack, archive)
                        await asyncio.sleep(self.settings.mineru_poll_seconds)
        except TimeoutError as exc:
            raise BusinessError("mineru_timeout", "文件解析超时，请稍后重试。", 504) from exc
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            raise BusinessError(
                "mineru_connection", "文件解析暂时不可用，请稍后重试。", 502
            ) from exc
