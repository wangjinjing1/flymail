"""Gmail OAuth2 认证提供者

实现 Google OAuth2 Authorization Code Grant 流程：
1. 生成授权 URL → 用户在 Google 页面授权
2. Google 回调携带 code → 后端用 code 换取 access_token
3. 用 access_token 获取用户邮箱信息

关键注意事项（来自 Google 官方文档）：
- redirect_uri 在授权请求和 token 交换时必须完全一致（Google 使用精确字符串比较）
- authorization code 只能使用一次，且约 10 分钟过期
- client_secret 为空会导致 Google 返回 invalid_client 错误
- redirect_uri 不匹配会导致 invalid_grant 或 redirect_uri_mismatch 错误
- token 交换必须使用 POST application/x-www-form-urlencoded 格式
"""
import time
import base64
import json as json_module
import httpx
from urllib.parse import urlencode, quote, unquote
from ..base import AuthProvider, Credentials, OAuthTokenError, parse_retry_after
from . import config as gmail_config
from utils.logger import get_logger

# 模块级日志
logger = get_logger("gmail.auth")


class GmailAuthProvider(AuthProvider):
    """Gmail OAuth2 认证提供者"""

    AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
    REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
    USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo"

    def get_auth_url(self, redirect_uri: str = "", state: str = "") -> str:
        """生成 Google OAuth2 授权 URL

        Args:
            redirect_uri: 回调地址，必须与 Google Console 配置完全一致
            state: 自定义状态参数（用于 CSRF 防护等）

        关键：redirect_uri 必须同时满足：
        1. 与 Google Console 中注册的 Authorized redirect URIs 完全一致
        2. 在后续 token 交换时使用完全相同的值
        """
        # 验证 client_id 已配置
        if not gmail_config.GMAIL_CLIENT_ID:
            raise ValueError(
                "Gmail OAuth2 客户端 ID 未配置。请先在飞邮设置页面填入客户端 ID。"
                "获取方式: https://console.cloud.google.com/apis/credentials"
            )

        # 验证 client_secret 已配置
        if not gmail_config.GMAIL_CLIENT_SECRET:
            raise ValueError(
                "Gmail 客户端密钥未配置。请先在飞邮设置页面填入客户端密钥，"
                "保存后再尝试授权登录。"
            )

        # 确定 redirect_uri：优先使用前端传入的值，其次使用配置值
        actual_redirect = redirect_uri or gmail_config.GMAIL_REDIRECT_URI
        if not actual_redirect:
            raise ValueError(
                "Gmail 回调地址(redirect_uri)未配置。请先在飞邮设置页面保存设置，"
                "系统会自动生成回调地址。"
            )

        # 将 redirect_uri 编码到 state 中，确保回调时能获取到相同的值
        # Google 要求 token exchange 时的 redirect_uri 必须与授权请求时完全一致
        state_data = {"provider": "gmail", "redirect_uri": actual_redirect, "ts": int(time.time())}
        if state:
            state_data["user_state"] = state
        encoded_state = base64.urlsafe_b64encode(
            json_module.dumps(state_data, separators=(",", ":")).encode()
        ).decode()

        # 构建授权 URL 参数
        # 参考: https://developers.google.com/identity/protocols/oauth2/web-server
        params = {
            "client_id": gmail_config.GMAIL_CLIENT_ID,
            "redirect_uri": actual_redirect,
            "response_type": "code",
            "scope": " ".join(gmail_config.GMAIL_SCOPES),
            "access_type": "offline",       # 获取 refresh_token
            "prompt": "consent",             # 每次都要求用户同意，确保获取新的 refresh_token
            "state": encoded_state,
        }

        auth_url = f"{self.AUTH_ENDPOINT}?{urlencode(params)}"
        logger.debug(
            "生成授权URL: redirect_uri=%s, client_id=%s...%s",
            actual_redirect,
            gmail_config.GMAIL_CLIENT_ID[:10] if gmail_config.GMAIL_CLIENT_ID else "空",
            gmail_config.GMAIL_CLIENT_ID[-6:] if len(gmail_config.GMAIL_CLIENT_ID or "") > 16 else "",
        )
        # 同时更新运行时配置中的 redirect_uri，确保后续回调时能获取到
        gmail_config.GMAIL_REDIRECT_URI = actual_redirect
        return auth_url

    def _extract_redirect_from_state(self, state: str) -> str | None:
        """从 state 参数中提取 redirect_uri

        处理 URL 传输中可能出现的 base64 编码问题：
        - `=` 填充符可能被 URL 编码为 `%3D`
        - `=` 填充符可能被剥离
        - `+` 可能被编码为 `%2B`（urlsafe_b64encode 不会产生+，但防御性处理）
        """
        if not state:
            return None

        try:
            # 先尝试 URL 解码（处理 %3D 等 URL 编码字符）
            decoded_state = unquote(state)

            # 修复 base64 填充：base64 编码长度必须是 4 的倍数
            # URL 传输中末尾的 = 可能被剥离
            padding_needed = len(decoded_state) % 4
            if padding_needed:
                decoded_state += "=" * (4 - padding_needed)

            data = json_module.loads(base64.urlsafe_b64decode(decoded_state))
            redirect = data.get("redirect_uri")
            if redirect:
                logger.debug("从 state 中提取 redirect_uri: %s", redirect)
                return redirect
            logger.warning("state 中没有 redirect_uri 字段")
            return None
        except Exception as e:
            logger.error("解码 state 失败: %s", e)
            return None

    async def handle_callback(self, code: str, redirect_uri: str = "", state: str = "") -> Credentials:
        """处理 OAuth2 回调，用授权码换取访问令牌

        关键：redirect_uri 必须与授权时使用的完全一致，
        否则 Google 会返回 invalid_grant 错误

        redirect_uri 确定优先级：
        1. 方法参数直接传入的 redirect_uri
        2. 从 state 参数中解码出来的 redirect_uri
        3. 运行时配置 gmail_config.GMAIL_REDIRECT_URI（由设置同步）
        """
        logger.debug("处理 OAuth 回调")

        # 确定实际使用的 redirect_uri（按优先级）
        # 关键：state 中解码的值是授权时实际使用的 redirect_uri，必须最高优先级！
        # Google 使用精确字符串比较，授权和换令牌时必须完全一致
        actual_redirect = None

        # 优先级1: 从 state 中解码（最可靠 - 这是授权时实际使用的值）
        if state:
            extracted = self._extract_redirect_from_state(state)
            if extracted:
                actual_redirect = extracted

        # 优先级2: 方法参数直接传入（来自 settings.json）
        if not actual_redirect and redirect_uri:
            actual_redirect = redirect_uri

        # 优先级3: 运行时配置（从 settings.json 同步的值）
        if not actual_redirect and gmail_config.GMAIL_REDIRECT_URI:
            actual_redirect = gmail_config.GMAIL_REDIRECT_URI
            logger.warning("使用运行时配置的 redirect_uri: %s（可能不匹配授权时的值）", actual_redirect)

        # 如果仍然无法确定 redirect_uri，报错
        if not actual_redirect:
            logger.error("无法确定 redirect_uri，所有来源均为空")
            raise ValueError(
                "OAuth 回调异常：无法确定 redirect_uri。"
                "请确保已在设置页面保存了 Gmail 配置，然后重新尝试授权登录。"
            )

        # 验证关键参数
        if not gmail_config.GMAIL_CLIENT_ID:
            raise ValueError("Gmail 客户端 ID 未配置，请先在设置页面配置。")
        if not gmail_config.GMAIL_CLIENT_SECRET:
            raise ValueError(
                "Gmail 客户端密钥未配置，请先在设置页面配置。"
                "如果已配置仍出现此错误，请检查密钥是否保存成功（刷新设置页面查看状态）。"
            )

        logger.debug("向 Google 发送 token 交换请求, redirect_uri=%s", actual_redirect)

        # 向 Google 发送 token 交换请求
        # 必须使用 application/x-www-form-urlencoded 格式（httpx 的 data 参数）
        token_data = {
            "code": code,
            "client_id": gmail_config.GMAIL_CLIENT_ID,
            "client_secret": gmail_config.GMAIL_CLIENT_SECRET,
            "redirect_uri": actual_redirect,
            "grant_type": "authorization_code",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.TOKEN_ENDPOINT,
                    data=token_data,
                )
        except httpx.TimeoutException:
            logger.error("请求 Google token 端点超时")
            raise ValueError("连接 Google 服务器超时，请检查网络连接后重试。")
        except httpx.ConnectError:
            logger.error("无法连接 Google token 端点")
            raise ValueError("无法连接 Google 服务器，请检查网络连接。")
        except Exception as e:
            logger.error("请求 Google token 端点异常: %s", type(e).__name__)
            raise ValueError(f"请求 Google 服务器失败: {e}")

        # Google 返回错误时，提取具体错误信息
        if response.status_code != 200:
            try:
                error_data = response.json()
            except Exception:
                error_data = {}

            error_type = error_data.get("error", "unknown")
            error_desc = error_data.get("error_description", response.text[:200])

            logger.error("Google 返回错误: error=%s, description=%s, redirect_uri=%s",
                         error_type, error_desc, actual_redirect)

            # 根据错误类型给出更友好的提示
            # O6 修复：错误信息中不包含 client_id、redirect_uri 等配置细节，
            # 避免通过 OAuth 回调页面泄露敏感配置（详细错误只记日志）
            if error_type == "invalid_client":
                raise ValueError(
                    "Google 返回 invalid_client 错误：客户端 ID 或密钥不正确。\n"
                    "请检查设置页面中的客户端 ID 和密钥是否与 Google Console 一致。"
                )
            elif error_type == "invalid_grant":
                raise ValueError(
                    "Google 返回 invalid_grant 错误：授权码无效或已过期。\n"
                    "可能原因：\n"
                    "1. 授权码已使用过（不能重复使用）\n"
                    "2. 授权码已过期（约10分钟有效期）\n"
                    "3. redirect_uri 不匹配\n"
                    "请重新尝试授权登录（不要刷新回调页面）。"
                )
            elif error_type == "redirect_uri_mismatch":
                raise ValueError(
                    "Google 返回 redirect_uri_mismatch 错误：回调地址不匹配。\n"
                    "请确保 Google Console 中配置了与设置页面完全相同的 redirect_uri"
                    "（包括协议、域名、端口、路径）。"
                )
            else:
                raise ValueError(
                    f"Google OAuth 错误: {error_type} - {error_desc}"
                )

        data = response.json()
        logger.debug("Token 交换成功")

        # 获取用户邮箱地址
        email = ""
        if data.get("access_token"):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    userinfo = await client.get(
                        self.USERINFO_ENDPOINT,
                        headers={"Authorization": f"Bearer {data['access_token']}"},
                    )
                    if userinfo.status_code == 200:
                        email = userinfo.json().get("email", "")
                    else:
                        logger.warning("获取用户邮箱失败: status=%d", userinfo.status_code)
            except Exception as e:
                logger.warning("获取用户邮箱异常: %s", e)

        return Credentials(
            provider_type="gmail",
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token", ""),
            expires_at=int(time.time()) + data.get("expires_in", 3600),
            extra={"email": email},
        )

    async def refresh_token(self, credentials: Credentials) -> Credentials:
        """刷新访问令牌"""
        if not credentials.refresh_token:
            raise ValueError("No refresh token available")

        logger.debug("刷新 access_token")

        # O10 修复：捕获网络异常，转换为 OAuthTokenError(is_permanent=False)，
        # 与 handle_callback 的异常处理风格保持一致
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.TOKEN_ENDPOINT,
                    data={
                        "client_id": gmail_config.GMAIL_CLIENT_ID,
                        "client_secret": gmail_config.GMAIL_CLIENT_SECRET,
                        "refresh_token": credentials.refresh_token,
                        "grant_type": "refresh_token",
                    },
                )
        except Exception as e:
            # 网络异常（超时、连接失败等）视为瞬态错误，上层会重试
            logger.warning("刷新 token 网络异常: %s", e)
            raise OAuthTokenError(
                f"Gmail 刷新 token 网络异常: {e}",
                error_code="network_error",
                http_status=0,
                provider="gmail",
            )

        if response.status_code != 200:
            error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            error_code = error_data.get("error", "unknown")
            # O11 修复：解析 Retry-After 头（429/503 限流场景）
            retry_after = parse_retry_after(response.headers.get("retry-after"))
            logger.error("刷新 token 失败: %s, retry_after=%s", error_code, retry_after)
            raise OAuthTokenError(
                f"Gmail OAuth 错误: {error_code}",
                error_code=error_code,
                http_status=response.status_code,
                provider="gmail",
                retry_after=retry_after,
            )
        data = response.json()

        return Credentials(
            provider_type="gmail",
            access_token=data.get("access_token", credentials.access_token),
            refresh_token=credentials.refresh_token,
            expires_at=int(time.time()) + data.get("expires_in", 3600),
            extra=credentials.extra,
        )

    async def revoke_token(self, credentials: Credentials) -> bool:
        """撤销访问令牌"""
        if not credentials.access_token:
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self.REVOKE_ENDPOINT,
                    params={"token": credentials.access_token},
                )
                return response.status_code == 200
        except Exception:
            return False

    async def get_email(self, access_token: str) -> str:
        """通过 access_token 获取邮箱地址"""
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                self.USERINFO_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if response.status_code == 200:
                return response.json().get("email", "")
        return ""
