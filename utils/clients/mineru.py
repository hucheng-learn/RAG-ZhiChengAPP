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
                "mineru_unavailable", "此格式需要 MinerU，请配置 ZHICHENGAPP_MINERU_API_TOKEN", 503
            )

    @staticmethod
    def _url(value):
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
            raise ValueError("MinerU 返回了无效下载地址")
        return value

    @staticmethod
    def _result(response):
        response.raise_for_status()
        body = response.json()
        if body.get("code") != 0:
            # 上游错误可能含签名地址或文件正文，不原样展示。
            raise BusinessError(
                "mineru_rejected", "MinerU 拒绝解析请求，请检查令牌、额度及文件格式"
            )
        return body["data"]

    def unpack(self, data):
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > 10000 or sum(i.file_size for i in entries) > (
                    self.settings.max_docx_unpacked_bytes
                ):
                    raise ValueError("MinerU 解析结果解压后超过限制")
                paths = {relative_path(i.filename): i for i in entries if not i.is_dir()}
                markdowns = [name for name in paths if name.lower().endswith(".md")]
                if not markdowns:
                    raise ValueError("MinerU 解析结果缺少 Markdown")
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
                    raise ValueError("MinerU 未识别出可索引正文")
                return source, assets
        except (zipfile.BadZipFile, UnicodeDecodeError, KeyError) as exc:
            raise ValueError("MinerU 返回了无效解析结果") from exc

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
                                "mineru_failed", "MinerU 解析失败，请检查文件或稍后重试"
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
                                            raise ValueError("MinerU 下载结果超过大小限制")
                                        archive.extend(block)
                            return await asyncio.to_thread(self.unpack, archive)
                        await asyncio.sleep(self.settings.mineru_poll_seconds)
        except TimeoutError as exc:
            raise BusinessError("mineru_timeout", "MinerU 解析等待超时，可从原件重试", 504) from exc
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            raise BusinessError(
                "mineru_connection", "MinerU 服务请求失败，请检查网络和服务配置", 502
            ) from exc
