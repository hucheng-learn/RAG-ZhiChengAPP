import asyncio
from functools import partial
from io import BytesIO


class ObjectStore:
    def __init__(self, client, bucket, executor):
        self.client, self.bucket, self.executor = client, bucket, executor

    async def call(self, operation, *args, **kwargs):
        return await asyncio.get_running_loop().run_in_executor(
            self.executor, partial(operation, *args, **kwargs)
        )

    async def initialize(self):
        """只创建本项目桶，不列举或修改其他桶。"""
        if not await self.call(self.client.bucket_exists, self.bucket):
            await self.call(self.client.make_bucket, self.bucket)

    async def put(self, key: str, data: bytes, content_type="application/octet-stream"):
        await self.call(
            self.client.put_object,
            self.bucket,
            key,
            BytesIO(data),
            len(data),
            content_type=content_type,
        )

    async def get(self, key: str) -> bytes:
        """即使读取失败也归还 HTTP 连接；对象键只由服务端记录产生。"""

        def operation():
            response = self.client.get_object(self.bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        return await self.call(operation)

    async def remove(self, key: str):
        await self.call(self.client.remove_object, self.bucket, key)
