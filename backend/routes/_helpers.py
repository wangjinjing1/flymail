"""路由层共享辅助函数

集中存放 routes 模块中重复使用的辅助函数和常量，避免代码重复。
被 routes/messages.py、routes/folders.py、routes/compose.py 共同复用。

主要内容：
- Outlook 连接异常的重试逻辑（Outlook IMAP 偶发不可用）
- Token 永久失效时的前端通知
- 账号查找与连接安全断开等通用工具
"""
import asyncio

from errors import AppError

from models import Account
from services.sync import sync_service
from utils.logger import get_logger

logger = get_logger("routes.helpers")


# ==================== Outlook 重试相关常量 ====================

# Outlook 可重试的错误模式
# 命中这些模式时，_with_outlook_retry 会自动重试，避免瞬态连接问题导致请求失败
_OUTLOOK_RETRYABLE_PATTERNS = (
    "User is authenticated but not connected",
    "NOOP verification failed",
    "Outlook 连接异常",
    "Outlook IMAP 认证成功但连接不可用",
    "Outlook token 即将过期",
    "AccessTokenExpired",
)

# Outlook 连接异常时的前端降级提示
_OUTLOOK_RECONNECTING_MSG = "邮箱连接异常，正在尝试重新连接，请稍后再试"


# ==================== 通用工具函数 ====================


async def _safe_disconnect(receiver):
    """安全断开 IMAP 连接，失败不抛异常

    用于 finally 块中确保连接被释放，即使断开过程出错也不影响主流程。
    """
    try:
        await receiver.disconnect()
    except Exception as e:
        logger.debug("断开 IMAP 连接失败: %s", e)


def _find_account_or_error(accounts: list[Account], account_id: str):
    """按账号 ID 查找账号；明确传入无效 ID 时返回错误，避免回退到其他邮箱。

    Args:
        accounts: 当前用户的账号列表
        account_id: 指定账号 ID，为空时使用第一个账号

    Returns:
        (account, None) 元组，account 为找到的账号对象

    Raises:
        AppError: 404 当传入的 account_id 在账号列表中找不到时
    """
    if account_id:
        account = next((a for a in accounts if a.id == account_id), None)
        if not account:
            logger.warning("找不到账号 %s，可用账号: %s", account_id, [a.id for a in accounts])
            raise AppError(404, "账号不存在或已被删除")
        return account, None
    return accounts[0], None


# ==================== Outlook 异常识别与重试 ====================


def _is_outlook_connection_error(account: Account, error_msg: str) -> bool:
    """判断是否为 Outlook 连接异常（用于前端降级提示）

    Outlook IMAP 偶发出现"已认证但未连接"等瞬态错误，
    命中 _OUTLOOK_RETRYABLE_PATTERNS 时返回 True，前端展示"正在重新连接"。
    """
    return account.provider == "outlook" and any(
        p in error_msg for p in _OUTLOOK_RETRYABLE_PATTERNS
    )


async def _notify_if_permanent_token_error(e: Exception, account: Account, user_uid: str = ""):
    """检测 token 永久错误并通知前端显示重新授权按钮

    基于异常类型判断（不再使用字符串匹配）：
    - TokenRefreshError.is_permanent: token.py 包装后的永久错误
    - OAuthTokenError.is_permanent: Provider 层直接抛出的永久错误

    命中永久错误时：
    1. 将账号 ID 加入 sync_service.reauth_account_ids（前端加载账号列表时可见）
    2. 通过 WebSocket 推送 reauth_needed 状态，前端立即显示重新授权按钮
    """
    from services.token import TokenRefreshError
    from providers.base import OAuthTokenError

    is_permanent = False
    if isinstance(e, TokenRefreshError) and e.is_permanent:
        is_permanent = True
    elif isinstance(e, OAuthTokenError) and e.is_permanent:
        is_permanent = True

    if is_permanent:
        sync_service.reauth_account_ids.add(account.id)
        try:
            await sync_service.notify_connection_status(
                account.id, "reauth_needed", user_uid or account.user_uid,
                error=str(e),
            )
        except Exception as ex:
            logger.debug("发送重授权通知失败: %s", ex)
        return True
    return False


async def _with_outlook_retry(account: Account, operation, max_retries_outlook: int = 2):
    """执行 IMAP 操作，Outlook 账号失败时自动重试

    Outlook IMAP 偶发不可用（如连接被服务端踢掉），重试可以恢复。
    其他 provider 不重试（max_retries=1），避免无意义重试浪费时间。

    Args:
        account: 账号对象，需要 provider 属性
        operation: 要执行的异步函数（无参数）
        max_retries_outlook: Outlook 账号最大重试次数（含首次执行）

    Returns:
        operation() 的返回值

    Raises:
        最后一次重试仍失败时抛出原始异常
    """
    max_retries = max_retries_outlook if account.provider == "outlook" else 1
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            return await operation()
        except Exception as e:
            last_error = e
            error_msg = str(e)
            is_retryable = any(p in error_msg for p in _OUTLOOK_RETRYABLE_PATTERNS)
            if is_retryable and attempt < max_retries:
                logger.warning("Outlook 操作失败，3 秒后重试（第 %d 次）: %s", attempt, error_msg)
                await asyncio.sleep(3)
                continue
            raise
    raise last_error
