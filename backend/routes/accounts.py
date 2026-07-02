"""账号管理路由

处理邮箱账号的添加（QQ/iCloud/网易/OAuth）、删除、更新、测试连接、
重建同步等操作。同时包含 OAuth 授权流程所需的辅助函数。
"""
import json
import ssl
import time
import uuid

from fastapi import APIRouter, Request, Body, Path as FastAPIPath

from errors import AppError

from db import (
    activate_account,
    get_accounts,
    create_account,
    deactivate_account,
    list_history_sync_jobs,
    update_account_info,
)
from deps import get_uid
from models import Account
from providers.base import Credentials
from providers.factory import ProviderFactory
from services.history_sync import schedule_history_sync, start_clear_cache, start_delete_account
from services.mail_cache import initial_sync
from services.settings import async_load_settings, async_save_settings
from services.sync import sync_service
from services.token import ensure_token as _ensure_gmail_token
from utils.logger import get_logger
from utils.tasks import create_background_task
from schemas import (
    AccountAddResponse,
    AccountListResponse,
    AccountTestResponse,
    AccountUpdateRequest,
    AuthCodeAccountRequest,
    AuthUrlRequest,
    AuthUrlResponse,
    DeleteResponse,
    MessageResponse,
)

logger = get_logger("routes.accounts")

router = APIRouter(prefix="/api/accounts", tags=["账号"])

# ==================== 常量定义 ====================

# 网关前缀（与 main.py 中的 StripPrefixMiddleware 对应）
from config import GATEWAY_PREFIX

# 授权码类邮箱的邮箱后缀验证规则
_AUTH_CODE_EMAIL_SUFFIXES = {
    "icloud": ("@icloud.com", "@me.com", "@mac.com"),
    "netease": ("@163.com", "@126.com", "@188.com", "@yeah.net"),
}

# 授权码类邮箱的后缀验证错误提示
_AUTH_CODE_SUFFIX_ERRORS = {
    "icloud": "请输入icloud.com、me.com或mac.com邮箱地址",
    "netease": "请输入163、126、188或yeah.net邮箱地址",
}

# ==================== 内部辅助函数 ====================
# 从 _helpers 复用共享辅助函数，避免与其他 routes 模块重复定义
from routes._helpers import _find_account_or_error, _safe_disconnect


def _sync_gmail_config(settings: dict):
    """将设置同步到 Gmail Provider 的运行时配置"""
    from providers.gmail import config as gmail_config
    gmail_config.GMAIL_CLIENT_ID = settings.get("gmail_client_id", "")
    gmail_config.GMAIL_CLIENT_SECRET = settings.get("gmail_client_secret", "")
    # 只有 settings 中有 redirect_uri 时才更新（避免用空值覆盖）
    redirect_uri = settings.get("gmail_redirect_uri", "")
    if redirect_uri:
        gmail_config.GMAIL_REDIRECT_URI = redirect_uri

def _sync_outlook_config(settings: dict):
    """将设置同步到 Microsoft/Outlook Provider 的运行时配置"""
    from providers.outlook import config as outlook_config
    outlook_config.OUTLOOK_CLIENT_ID = settings.get("outlook_client_id", "")
    outlook_config.OUTLOOK_CLIENT_SECRET = settings.get("outlook_client_secret", "")
    # 只有 settings 中有 redirect_uri 时才更新（避免用空值覆盖）
    redirect_uri = settings.get("outlook_redirect_uri", "")
    if redirect_uri:
        outlook_config.OUTLOOK_REDIRECT_URI = redirect_uri


async def _add_auth_code_account(request: Request, provider: str, body: AuthCodeAccountRequest = None):
    """授权码类邮箱的统一添加逻辑（QQ、网易、iCloud）"""
    uid = await get_uid(request)

    if body is None:
        raise AppError(400, "邮箱地址和授权码不能为空")
    email_addr = body.email.strip()
    auth_code = body.auth_code.strip()
    raw_payload = await request.json()
    fetch_history = bool(raw_payload.get("fetch_history"))

    if not email_addr or not auth_code:
        raise AppError(400, "邮箱地址和授权码不能为空")

    # 邮箱后缀验证（QQ 无限制，iCloud 和网易有特定后缀要求）
    valid_suffixes = _AUTH_CODE_EMAIL_SUFFIXES.get(provider)
    if valid_suffixes and not email_addr.endswith(valid_suffixes):
        raise AppError(400, _AUTH_CODE_SUFFIX_ERRORS.get(provider, "邮箱地址格式不正确"))

    try:
        # 根据平台创建凭据
        if provider == "qq":
            from providers.qq.auth import QQAuthProvider
            credentials = QQAuthProvider.create_credentials(email_addr, auth_code)
        elif provider == "icloud":
            from providers.icloud.auth import ICloudAuthProvider
            credentials = ICloudAuthProvider.create_credentials(email_addr, auth_code)
        elif provider == "netease":
            from providers.netease.auth import NeteaseAuthProvider
            credentials = NeteaseAuthProvider.create_credentials(email_addr, auth_code)
        else:
            raise AppError(400, f"不支持的平台: {provider}")

        receiver = ProviderFactory.get_receiver(provider)
        await receiver.connect(credentials)
        await _safe_disconnect(receiver)
        creds_json = json.dumps({
            "access_token": auth_code,
            "refresh_token": "",
            "expires_at": 0,
            "extra": {"email": email_addr},
        })
        existing = next((item for item in await get_accounts(uid) if item.email == email_addr and item.provider == provider), None)
        if existing:
            await activate_account(existing.id, creds_json, status="connected")
            create_background_task(sync_service.add_account(existing.id), name="reconnect_account_imap")
            create_background_task(initial_sync(existing.id), name="reconnect_initial_sync")
            if fetch_history:
                create_background_task(schedule_history_sync(existing.id), name="schedule_history_sync")
            return {
                "success": True,
                "account": {
                    "id": existing.id,
                    "email": existing.email,
                    "provider": existing.provider,
                    "status": "connected",
                    "remark": existing.remark,
                    "group_name": existing.group_name,
                    "hide_email": existing.hide_email,
                    "poll_interval_seconds": existing.poll_interval_seconds,
                    "created_at": existing.created_at,
                }
            }

        account = Account(
            id=str(uuid.uuid4()),
            user_uid=uid,
            email=email_addr,
            provider=provider,
            credentials_json=json.dumps({
                # 授权码存入 access_token 字段（复用 OAuth 字段结构），expires_at=0 和 refresh_token="" 为占位值
                "access_token": auth_code,
                "refresh_token": "",
                "expires_at": 0,
                "extra": {"email": email_addr},
            }),
            status="connected",
            created_at=time.time(),
            updated_at=time.time(),
        )

        await create_account(account)
        create_background_task(sync_service.add_account(account.id), name="add_account_imap")
        # 后台全量同步收件箱（首次添加账号时缓存为空，需要拉取邮件摘要）
        create_background_task(initial_sync(account.id), name="initial_sync")
        if fetch_history:
            create_background_task(schedule_history_sync(account.id), name="schedule_history_sync")

        return {
            "success": True,
            "account": {
                "id": account.id,
                "email": account.email,
                "provider": account.provider,
                "status": account.status,
                "remark": "",
                "group_name": "",
                "hide_email": False,
                "poll_interval_seconds": account.poll_interval_seconds,
                "created_at": account.created_at,
            }
        }
    except AppError:
        raise  # 保留原始的 AppError（如 400 验证错误），不要覆盖为 500
    except Exception as e:
        raise AppError(500, str(e))


def _build_oauth_frontend_url(request: Request) -> str:
    """根据当前网关请求构造 OAuth 完成后的前端回跳地址。"""
    scheme = request.headers.get("X-Forwarded-Proto") or request.url.scheme
    host = request.headers.get("X-Forwarded-Host") or request.headers.get("host", "")
    return f"{scheme}://{host}{GATEWAY_PREFIX}/"


# OAuth state 解析辅助函数：从 auth.py 导入（OAuth 回调和账号添加共用）
from routes.auth import (
    _build_oauth_result_html,
    _extract_oauth_frontend_url_from_state,
    _extract_oauth_provider_from_state,
    _extract_oauth_state_data,
    _extract_oauth_uid_from_state,
)


# ==================== 账号管理接口 ====================


@router.get("", response_model=AccountListResponse, summary="获取所有邮箱账号")
async def list_accounts(request: Request):
    """获取当前飞牛用户下所有已绑定的邮箱账号列表"""
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    safe_accounts = []
    for acc in accounts:
        safe_accounts.append({
            "id": acc.id,
            "email": acc.email,
            "provider": acc.provider,
            "status": acc.status,
            "remark": acc.remark,
            "group_name": acc.group_name,
            "hide_email": acc.hide_email,
            "poll_interval_seconds": acc.poll_interval_seconds,
            "created_at": acc.created_at,
            "reauth_needed": acc.id in sync_service.reauth_account_ids,
        })
    return {"accounts": safe_accounts}


@router.get("/delete-jobs", summary="获取账号删除任务")
async def list_account_delete_jobs(request: Request):
    uid = await get_uid(request)
    jobs = await list_history_sync_jobs(uid, job_type="account_delete")
    active_jobs = [
        job for job in jobs
        if job.get("status") in {"pending", "running", "failed"}
    ]
    return {"jobs": active_jobs}


@router.post("/auth-url", response_model=AuthUrlResponse, summary="获取 OAuth 授权URL")
async def get_auth_url(request: Request, body: AuthUrlRequest = Body(description="OAuth 授权参数")):
    """获取第三方邮箱的 OAuth2 授权跳转地址。

    前端拿到此 URL 后重定向到 Google 等平台的登录页面。
    同时将 redirect_uri 保存到设置中，确保回调时能正确获取。
    """
    provider_type = body.provider
    redirect_uri = body.redirect_uri
    fetch_history = bool((await request.json()).get("fetch_history"))
    # 从网关请求头获取当前用户 uid，编码到 OAuth state 中，回调时恢复
    uid = await get_uid(request)
    # 记录当前前端入口，OAuth 回调成功后跳回这个绝对地址，避免落到 51010 专用服务
    frontend_url = _build_oauth_frontend_url(request)
    # 安全修复 S5 + O7：state 加入 HMAC 签名和时间戳，防止篡改 uid 冒充其他用户
    # O7 修复：redirect_uri 也纳入签名，防止攻击者篡改 state 中的 redirect_uri
    from routes.auth import _sign_oauth_state
    state_payload = {
        "uid": uid,
        "frontend_url": frontend_url,
        "redirect_uri": redirect_uri or "",
        "fetch_history": fetch_history,
        "_ts": int(time.time()),
    }
    state_payload["_sig"] = _sign_oauth_state(state_payload)
    oauth_state = json.dumps(state_payload, separators=(",", ":"))

    try:
        auth = ProviderFactory.get_auth(provider_type)
        url = auth.get_auth_url(redirect_uri=redirect_uri, state=oauth_state)

        if redirect_uri and provider_type == "gmail":
            current = await async_load_settings()
            if current.get("gmail_redirect_uri") != redirect_uri:
                saved = await async_save_settings({"gmail_redirect_uri": redirect_uri})
                _sync_gmail_config(saved)

        if redirect_uri and provider_type == "outlook":
            current = await async_load_settings()
            if current.get("outlook_redirect_uri") != redirect_uri:
                saved = await async_save_settings({"outlook_redirect_uri": redirect_uri})
                _sync_outlook_config(saved)

        return {"auth_url": url, "provider": provider_type}
    except ValueError as e:
        logger.error("生成授权URL失败: %s", e)
        raise AppError(400, str(e))


@router.post("/add-qq", response_model=AccountAddResponse, summary="添加QQ邮箱账号")
async def add_qq_account(request: Request, body: AuthCodeAccountRequest = Body(description="QQ邮箱授权码账号信息")):
    """使用授权码直接添加QQ邮箱账号"""
    return await _add_auth_code_account(request, "qq", body)


@router.post("/add-icloud", response_model=AccountAddResponse, summary="添加iCloud邮箱账号")
async def add_icloud_account(request: Request, body: AuthCodeAccountRequest = Body(description="iCloud应用专用密码账号信息")):
    """使用应用专用密码添加iCloud邮箱账号"""
    return await _add_auth_code_account(request, "icloud", body)


@router.post("/add-netease", response_model=AccountAddResponse, summary="添加网易邮箱账号")
async def add_netease_account(request: Request, body: AuthCodeAccountRequest = Body(description="网易邮箱授权码账号信息")):
    """使用授权码添加网易邮箱账号"""
    return await _add_auth_code_account(request, "netease", body)


@router.delete("/{account_id}", response_model=DeleteResponse, summary="删除邮箱账号")
async def remove_account(
    account_id: str = FastAPIPath(description="账号唯一ID"),
    request: Request = None,
):
    """后台删除邮箱账号、本地邮件缓存和本地附件文件。"""
    uid = await get_uid(request)

    accounts = await get_accounts(uid)
    target = None
    for acc in accounts:
        if acc.id == account_id:
            target = acc
            break

    if not target:
        # 幂等删除：账号不存在也算成功（避免前端重复请求时误报错误）
        return {"success": True, "message": "账号不存在或已删除"}

    started = await start_delete_account(account_id)
    if not started:
        raise AppError(409, "账号正在删除中")
    return {"success": True, "message": "已开始后台删除"}


@router.post("/{account_id}/disable", response_model=MessageResponse, summary="禁用邮箱账号")
async def disable_account(
    account_id: str = FastAPIPath(description="账号唯一ID"),
    request: Request = None,
):
    """禁用邮箱账号，保留授权、本地邮件缓存和附件文件。"""
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    if not any(acc.id == account_id for acc in accounts):
        raise AppError(404, "Account not found")

    await sync_service.remove_account(account_id)
    from services.mail_cache import remove_sync_lock
    remove_sync_lock(account_id)
    from services.token import remove_token_lock
    remove_token_lock(account_id)
    disabled = await deactivate_account(account_id, uid, status="offline", clear_credentials=False)
    if disabled:
        return {"success": True, "message": "账号已禁用"}
    raise AppError(500, "Failed to disable account")


@router.post("/{account_id}/rebuild-sync", response_model=MessageResponse, summary="重建同步")
async def rebuild_sync(
    account_id: str = FastAPIPath(description="账号ID"),
    request: Request = None,
):
    """清空账号缓存并在后台重新同步。"""
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    account = next((a for a in accounts if a.id == account_id), None)
    if not account:
        raise AppError(404, "Account not found")

    try:
        async def _rebuild_and_notify():
            try:
                await initial_sync(account_id, force_full=True)
                msg = json.dumps({
                    "type": "rebuild_done",
                    "account_id": account_id,
                    "message": "同步完成",
                })
                await sync_service._broadcast(msg, uid)
                logger.info("rebuild sync finished: %s", account.email)
            except Exception as e:
                logger.error("rebuild sync background failed: %s", e)
                msg = json.dumps({
                    "type": "rebuild_done",
                    "account_id": account_id,
                    "error": str(e),
                })
                await sync_service._broadcast(msg, uid)

        create_background_task(_rebuild_and_notify(), name="rebuild_and_notify")

        return {"success": True, "message": "已开始重建同步，请稍后查看进度"}
    except Exception as e:
        logger.error("rebuild sync failed: %s", e)
        raise AppError(500, str(e))


@router.post("/{account_id}/clear-cache", response_model=MessageResponse, summary="清空账号缓存")
async def clear_cache(
    account_id: str = FastAPIPath(description="账号ID"),
    request: Request = None,
):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    account = next((a for a in accounts if a.id == account_id), None)
    if not account:
        raise AppError(404, "Account not found")

    started = await start_clear_cache(account_id)
    if not started:
        raise AppError(409, "已有清空缓存任务正在执行")
    return {"success": True, "message": "已开始清空缓存"}


@router.post("/{account_id}/test", response_model=AccountTestResponse, summary="测试账号连接")
async def test_account(
    account_id: str = FastAPIPath(description="账号唯一ID"),
    request: Request = None,
):
    """测试邮箱账号的 IMAP 连接是否正常，同时刷新 OAuth 令牌"""
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    target = None
    for acc in accounts:
        if acc.id == account_id:
            target = acc
            break

    if not target:
        raise AppError(404, "Account not found")

    try:
        credentials = await _ensure_gmail_token(target)

        receiver = ProviderFactory.get_receiver(target.provider)
        try:
            await receiver.connect(credentials)
        except (ssl.SSLError, ConnectionError) as e:
            # SSL 瞬时错误（如 EOF），等待后重试一次，避免误报连接失败
            if "eof" in str(e).lower():
                await asyncio.sleep(3)
                await receiver.connect(credentials)
            else:
                raise
        try:
            if target.provider == "outlook":
                folders = await receiver.fetch_folders()
        finally:
            await _safe_disconnect(receiver)

        return {"success": True, "status": "connected"}
    except Exception as e:
        # token 永久失效时通知前端显示重新授权按钮
        from services.token import TokenRefreshError
        from providers.base import OAuthTokenError
        is_permanent = (isinstance(e, TokenRefreshError) and e.is_permanent) or (isinstance(e, OAuthTokenError) and e.is_permanent)
        if is_permanent:
            logger.error("测试连接失败，token 失效需重新授权: %s", target.email)
            sync_service.reauth_account_ids.add(target.id)
            try:
                await sync_service.notify_connection_status(
                    target.id, "reauth_needed", uid, error=str(e),
                )
            except Exception as notify_err:
                logger.debug("通知 reauth_needed 失败: %s", notify_err)
        else:
            logger.error("测试连接失败: %s, %s", target.email, e)
        return {"success": False, "status": "error", "error": str(e)}


# 更新账号信息返回 success + message，复用 MessageResponse（含 success 和 message 字段）
@router.put("/{account_id}", response_model=MessageResponse, summary="更新账号信息")
async def update_account(
    account_id: str = FastAPIPath(description="账号唯一ID"),
    request: Request = None,
    body: AccountUpdateRequest = Body(description="要更新的账号字段"),
):
    """更新账号的备注名、分组和邮箱隐藏设置"""
    uid = await get_uid(request)
    updated = await update_account_info(
        account_id,
        uid,
        body.remark,
        body.group_name,
        body.hide_email,
        body.poll_interval_seconds,
    )
    if updated:
        create_background_task(sync_service.add_account(account_id), name="restart_account_imap")
        return {"success": True, "message": "账号信息已更新"}
    raise AppError(404, "Account not found")
