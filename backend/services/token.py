"""Token 刷新服务

从 main.py 中提取，解决循环导入问题。
负责 OAuth access_token 的过期检查与自动刷新，并为每个账号维护独立的 asyncio.Lock 防止并发刷新。
内置重试逻辑：瞬态错误（网络/5xx/429）自动重试，永久错误（invalid_grant）才抛出。

错误判断基于 OAuthTokenError.is_permanent 属性（来自 Provider 层的结构化 OAuth error_code），
不再使用字符串匹配。
"""

import time
import json
import asyncio
from providers.base import Credentials, OAuthTokenError
from providers.factory import ProviderFactory
from db import get_account_by_id, update_account_credentials
from utils.logger import get_logger

logger = get_logger("token")

# 每个账号一把锁，防止并发刷新同一账号的 token（不同账号互不阻塞）
_token_locks: dict[str, asyncio.Lock] = {}


class TokenRefreshError(Exception):
    """token 刷新失败的自定义异常，区分瞬态和永久错误"""
    def __init__(self, message: str, is_permanent: bool = False):
        super().__init__(message)
        self.is_permanent = is_permanent


def _get_token_lock(account_id: str) -> asyncio.Lock:
    """获取指定账号的 token 刷新锁，不存在则创建

    修复 P4：用 setdefault 替代 check-then-set，确保原子创建。
    asyncio 单线程模型中 setdefault 内部无 await，不会被协程打断，是安全的。
    """
    return _token_locks.setdefault(account_id, asyncio.Lock())


def remove_token_lock(account_id: str) -> None:
    """删除指定账号的 token 锁，防止内存泄漏（账号删除时调用）"""
    _token_locks.pop(account_id, None)


async def _do_refresh(account, credentials: Credentials, max_retries: int = 2) -> Credentials:
    """执行 token 刷新，瞬态错误自动重试

    错误判断逻辑：
    - OAuthTokenError.is_permanent=True → 永久错误，立即抛出 TokenRefreshError(is_permanent=True)
    - OAuthTokenError.is_permanent=False → 非永久 OAuth 错误，重试
    - 其他异常（网络/超时等）→ 重试

    Args:
        account: 账号对象
        credentials: 当前凭据（含 refresh_token）
        max_retries: 最大重试次数（不含首次尝试）

    Returns:
        新的凭据

    Raises:
        TokenRefreshError: 永久错误或重试耗尽后抛出
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            auth = ProviderFactory.get_auth(account.provider)
            new_creds = await auth.refresh_token(credentials)
            return new_creds
        except OAuthTokenError as e:
            last_error = e
            if e.is_permanent:
                # 永久错误（invalid_grant 等）：不重试，立即抛出
                logger.error("%s token 失效，需重新授权: %s", account.provider, account.email)
                raise TokenRefreshError(
                    f"{account.provider} token 已失效，请重新授权账号 {account.email}",
                    is_permanent=True,
                ) from e
            # 非永久 OAuth 错误：重试
        except Exception as e:
            last_error = e
        # 瞬态错误：重试
        if attempt < max_retries:
            # O11 修复：优先使用 Retry-After 头的建议等待时间（429/503 限流场景），
            # fallback 到固定退避（3秒、6秒递增）
            delay = 3 * (attempt + 1)
            if isinstance(last_error, OAuthTokenError) and last_error.retry_after > 0:
                delay = last_error.retry_after
            logger.warning("%s token 刷新瞬态失败（%d/%d），%ss 后重试: %s",
                           account.provider, attempt + 1, max_retries, delay, last_error)
            await asyncio.sleep(delay)
        else:
            logger.warning("%s token 刷新失败（重试耗尽）: %s, %s",
                           account.provider, account.email, last_error)

    # 重试耗尽，抛出瞬态错误（下次循环还会再试）
    raise TokenRefreshError(
        f"{account.provider} token 刷新暂时失败（网络或服务端问题），稍后自动重试",
        is_permanent=False,
    ) from last_error


async def ensure_token(account) -> Credentials:
    """确保 OAuth access_token 有效，过期则自动刷新并更新数据库

    Gmail 和 Microsoft access_token 通常有效期约 1 小时，过期后 IMAP/SMTP 连接会失败。
    此函数检查 token 是否即将过期（5 分钟内），如果是则用 refresh_token 换取新 token。
    QQ/网易/iCloud 不走 OAuth，直接返回原凭据。

    使用 per-account asyncio.Lock 防止多个协程同时刷新同一账号的 token，
    避免因并发刷新导致 refresh_token 被覆盖或数据库写入竞争。

    刷新失败时：
    - 瞬态错误（网络/5xx）→ 内部重试 2 次，仍失败则抛出 TokenRefreshError(is_permanent=False)
    - 永久错误（invalid_grant）→ 抛出 TokenRefreshError(is_permanent=True)
    """
    creds_data = json.loads(account.credentials_json)
    credentials = Credentials(
        provider_type=account.provider,
        access_token=creds_data.get("access_token", ""),
        refresh_token=creds_data.get("refresh_token", ""),
        expires_at=creds_data.get("expires_at", 0),
        extra=creds_data.get("extra", {}),
    )

    # 非 OAuth 账号直接返回（QQ/网易/iCloud 用授权码或应用专用密码，不会自动刷新）
    if account.provider not in ("gmail", "outlook"):
        return credentials

    # 检查 token 是否即将过期（5 分钟内）
    # D4 设计说明：锁外检查用的是函数参数 credentials（来自 account.credentials_json），
    # 可能不是数据库最新值。但 5 分钟缓冲足够长：
    # - 若 token 还有 >5 分钟有效期，直接返回（即使数据已被其他协程刷新，旧 token 仍有效）
    # - 若 token 即将过期，进入锁内 double-check，重新从数据库读取最新凭据
    # 这个设计避免了每次调用都加锁的性能开销，5 分钟窗口是合理的缓冲。
    if credentials.expires_at > time.time() + 300:
        return credentials

    # token 即将过期或已过期，需要刷新 —— 加锁防止并发刷新
    lock = _get_token_lock(account.id)
    async with lock:
        # 拿到锁后再次检查：可能其他协程已经刷新过了
        # O4 修复：用 get_account_by_id 按主键查询，避免 get_accounts("") 加载所有用户账号
        fresh_account = await get_account_by_id(account.id)
        # O12 修复：合并重复的 credentials_json 解析，只解析一次
        fresh_creds_data = None
        if fresh_account:
            fresh_creds_data = json.loads(fresh_account.credentials_json)
            fresh_expires_at = fresh_creds_data.get("expires_at", 0)
            if fresh_expires_at > time.time() + 300:
                # 其他协程已刷新成功，直接用新凭据
                account.credentials_json = fresh_account.credentials_json
                return Credentials(
                    provider_type=account.provider,
                    access_token=fresh_creds_data.get("access_token", ""),
                    refresh_token=fresh_creds_data.get("refresh_token", ""),
                    expires_at=fresh_expires_at,
                    extra=fresh_creds_data.get("extra", {}),
                )

        # D4 修复：锁内确认仍需刷新时，优先用锁内重新读取的最新凭据，
        # 避免函数参数 credentials 过时导致 refresh_token 为空的误判
        creds_to_refresh = credentials  # 默认用函数参数（fresh_account 不存在时的 fallback）
        if fresh_creds_data is not None:
            creds_to_refresh = Credentials(
                provider_type=account.provider,
                access_token=fresh_creds_data.get("access_token", ""),
                refresh_token=fresh_creds_data.get("refresh_token", ""),
                expires_at=fresh_creds_data.get("expires_at", 0),
                extra=fresh_creds_data.get("extra", {}),
            )
            # 同步更新 creds_data，后续写回数据库时用最新值
            creds_data = fresh_creds_data

        # 确认仍需刷新，用 refresh_token 刷新（内部已含重试逻辑）
        if not creds_to_refresh.refresh_token:
            logger.warning("%s token 过期且无 refresh_token: %s", account.provider, account.email)
            return creds_to_refresh

        try:
            new_creds = await _do_refresh(account, creds_to_refresh)
            # 更新数据库中的凭据；Microsoft 可能轮换 refresh_token，需要一并保存
            creds_data["access_token"] = new_creds.access_token
            creds_data["expires_at"] = new_creds.expires_at
            if new_creds.refresh_token:
                creds_data["refresh_token"] = new_creds.refresh_token
            new_json = json.dumps(creds_data)
            await update_account_credentials(account.id, new_json)
            # 同步更新内存中的 account 对象（避免下次还用过期值）
            account.credentials_json = new_json
            logger.info("%s token 已刷新: %s", account.provider, account.email)
            return new_creds
        except TokenRefreshError:
            raise  # 已经是自定义异常，直接抛出
        except Exception as e:
            # 未预期的异常，包装为瞬态错误
            logger.error("%s token 刷新未预期异常: %s, %s", account.provider, account.email, e)
            raise TokenRefreshError(
                f"{account.provider} token 刷新暂时失败，稍后自动重试",
                is_permanent=False,
            ) from e
