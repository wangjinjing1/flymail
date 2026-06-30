"""统一错误处理模块

定义应用级异常类和全局异常处理器，统一 API 错误响应格式。

用法：
    raise AppError(404, "账号不存在")
    raise AppError(400, "邮箱地址和授权码不能为空")
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from utils.logger import get_logger

logger = get_logger("errors")


class AppError(Exception):
    """应用级异常，用于统一 API 错误响应

    Attributes:
        code: HTTP 状态码
        message: 错误消息（面向用户，中文）
    """

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """全局异常处理器：将 AppError 转换为统一格式的 JSON 响应

    响应格式：{"error": "错误消息"}
    """
    logger.warning("API 错误: %s %s -> %d %s",
                   request.method, request.url.path, exc.code, exc.message)
    return JSONResponse(content={"error": exc.message}, status_code=exc.code)
