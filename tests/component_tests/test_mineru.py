"""网络均由 MockTransport 提供，不上传真实文档或消耗额度。"""

import asyncio
import json
import zipfile
from io import BytesIO

import httpx
import pytest

from common.config import Settings
from common.errors import BusinessError
from utils.clients.mineru import MinerUClient


def archive(entries):
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for name, data in entries.items():
            bundle.writestr(name, data)
    return output.getvalue()


def test_submit_poll_download_keeps_credentials_off_signed_urls():
    calls = []
    data = archive({"result/full.md": "# 正文\n资料", "result/images/a.png": b"image"})

    def respond(request):
        calls.append(request)
        if request.url.path.endswith("file-urls/batch"):
            assert json.loads(request.content)["files"][0]["name"] == "课件.pdf"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"batch_id": "batch", "file_urls": ["https://storage.test/upload"]},
                },
            )
        if request.method == "PUT":
            assert request.content == b"pdf" and "authorization" not in request.headers
            return httpx.Response(200)
        if request.url.host == "storage.test":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=data)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "extract_result": [
                        {"state": "done", "full_zip_url": "https://storage.test/result.zip"}
                    ]
                },
            },
        )

    settings = Settings(_env_file=None, mineru_api_token="test-secret")
    source, assets = asyncio.run(
        MinerUClient(settings, transport=httpx.MockTransport(respond)).parse("课件.pdf", b"pdf")
    )
    assert source == "# 正文\n资料" and assets == {"images/a.png": b"image"}
    assert len(calls) == 4


@pytest.mark.parametrize(
    "entries",
    [
        {"../full.md": "bad"},
        {"full.md": "", "a.png": b"a"},
        {"a.txt": "no markdown"},
    ],
)
def test_rejects_unsafe_or_empty_results(entries):
    with pytest.raises((BusinessError, ValueError)):
        MinerUClient(Settings(_env_file=None)).unpack(archive(entries))


def test_rejects_oversized_unpacked_result():
    settings = Settings(_env_file=None, max_docx_unpacked_bytes=1024)
    with pytest.raises(ValueError, match="超过"):
        MinerUClient(settings).unpack(archive({"full.md": "x" * 1025}))


def test_timeout_and_upstream_error_are_actionable_and_do_not_leak():
    settings = Settings(
        _env_file=None,
        mineru_api_token="test-secret",
        mineru_timeout_seconds=1,
        mineru_poll_seconds=0.01,
    )

    def respond(request):
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"batch_id": "batch", "file_urls": ["https://storage.test/upload"]},
                },
            )
        if request.method == "PUT":
            return httpx.Response(200)
        return httpx.Response(503)

    with pytest.raises(BusinessError, match="超时"):
        asyncio.run(
            MinerUClient(settings, transport=httpx.MockTransport(respond)).parse("a.pdf", b"pdf")
        )
