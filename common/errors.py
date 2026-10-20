class BusinessError(Exception):
    """可安全返回客户端的业务错误；底层 SDK 异常统一转换后再暴露。"""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
