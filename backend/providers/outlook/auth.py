"""Outlook OAuth2 认证提供者"""
import base64
import json as json_module
import time
from urllib.parse import urlencode, unquote

import httpx

from ..base import AuthProvider, Credentials, OAuthTokenError, parse_retry_after
from . import config as outlook_config
from utils.logger import get_logger

logger = get_logger("outlook.auth")


class OutlookAuthProvider(AuthProvider):
    """Microsoft/Outlook OAuth2 认证提供者"""

    AUTH_ENDPOINT = outlook_config.OUTLOOK_AUTH_ENDPOINT
    TOKEN_ENDPOINT = outlook_config.OUTLOOK_TOKEN_ENDPOINT
    GRAPH_ME_ENDPOINT = "https://graph.microsoft.com/v1.0/me"

    def get_auth_url(self, redirect_uri: str = "", state: str = "") -> str:
        """生成 Microsoft OAuth2 授权 URL"""
        if not outlook_config.OUTLOOK_CLIENT_ID:
            raise ValueError("Outlook OAuth2 客户端 ID 未配置，请先在设置页面填入客户端 ID。")
        if not outlook_config.OUTLOOK_CLIENT_SECRET:
            raise ValueError("Outlook 客户端密钥未配置，请先在设置页面填入客户端密钥。")

        actual_redirect = redirect_uri or outlook_config.OUTLOOK_REDIRECT_URI
        if not actual_redirect:
            raise ValueError("Outlook 回调地址(redirect_uri)未配置，请先在设置页面保存设置。")

        # 将授权时使用的回调地址放入 state，回调换 token 时必须使用同一个值。
        state_data = {
            "provider": "outlook",
            "redirect_uri": actual_redirect,
            "ts": int(time.time()),
        }
        if state:
            state_data["user_state"] = state
        encoded_state = base64.urlsafe_b64encode(
            json_module.dumps(state_data, separators=(",", ":")).encode()
        ).decode()

        params = {
            "client_id": outlook_config.OUTLOOK_CLIENT_ID,
            "redirect_uri": actual_redirect,
            "response_type": "code",
            "response_mode": "query",
            "prompt": "select_account",
            "scope": " ".join(outlook_config.OUTLOOK_SCOPES),
            "state": encoded_state,
        }
        outlook_config.OUTLOOK_REDIRECT_URI = actual_redirect
        return f"{self.AUTH_ENDPOINT}?{urlencode(params)}"

    def _extract_redirect_from_state(self, state: str) -> str | None:
        """从 state 中提取授权时的 redirect_uri"""
        if not state:
            return None
        try:
            decoded_state = unquote(state)
            padding_needed = len(decoded_state) % 4
            if padding_needed:
                decoded_state += "=" * (4 - padding_needed)
            data = json_module.loads(base64.urlsafe_b64decode(decoded_state))
            if data.get("provider") and data.get("provider") != "outlook":
                logger.warning("state provider 不匹配: %s", data.get("provider"))
            return data.get("redirect_uri")
        except Exception as e:
            logger.error("解码 Outlook state 失败: %s", e)
            return None

    @staticmethod
    def _decode_id_token_email(id_token: str) -> str:
        """从 id_token payload 中读取邮箱，优先 preferred_username。"""
        if not id_token:
            return ""
        try:
            parts = id_token.split(".")
            if len(parts) < 2:
                return ""
            payload = parts[1]
            padding_needed = len(payload) % 4
            if padding_needed:
                payload += "=" * (4 - padding_needed)
            data = json_module.loads(base64.urlsafe_b64decode(payload.encode()))
            return data.get("preferred_username") or data.get("email") or ""
        except Exception as e:
            logger.warning("解析 Outlook id_token 失败: %s", e)
            return ""

    @staticmethod
    def _validate_email(email: str) -> None:
        """校验 Outlook 账号邮箱后缀。"""
        if not email:
            raise ValueError("无法获取 Outlook 邮箱地址，请确认授权范围包含邮箱信息。")
        if not email.lower().endswith(outlook_config.SUPPORTED_DOMAINS):
            raise ValueError("当前邮箱不是支持的 Outlook/Hotmail/Live/MSN 邮箱。")

    async def _fetch_email_from_graph(self, access_token: str) -> str:
        """通过 Microsoft Graph API 获取用户邮箱（id_token 缺少邮箱时的兜底方案）

        某些 Microsoft 账号的 id_token 中可能不包含邮箱信息，
        此时通过 Graph API 的 /me 端点获取用户的邮箱地址。
        """
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    self.GRAPH_ME_ENDPOINT,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("mail") or data.get("userPrincipalName") or ""
        except Exception as e:
            logger.warning("Graph API 获取邮箱失败: %s", e)
        return ""

    async def handle_callback(self, code: str, redirect_uri: str = "", state: str = "") -> Credentials:
        """处理 OAuth2 回调，用授权码换取访问令牌。"""
        actual_redirect = self._extract_redirect_from_state(state) if state else None
        actual_redirect = actual_redirect or redirect_uri or outlook_config.OUTLOOK_REDIRECT_URI
        # 清理调试日志：删除 redirect_uri 来源追踪、token exchange 请求/响应、token 解析结果等调试日志
        if not actual_redirect:
            raise ValueError("OAuth 回调异常：无法确定 Outlook redirect_uri。")
        if not outlook_config.OUTLOOK_CLIENT_ID:
            raise ValueError("Outlook 客户端 ID 未配置，请先在设置页面配置。")
        if not outlook_config.OUTLOOK_CLIENT_SECRET:
            raise ValueError("Outlook 客户端密钥未配置，请先在设置页面配置。")

        token_data = {
            "code": code,
            "client_id": outlook_config.OUTLOOK_CLIENT_ID,
            "client_secret": outlook_config.OUTLOOK_CLIENT_SECRET,
            "redirect_uri": actual_redirect,
            "grant_type": "authorization_code",
            "scope": " ".join(outlook_config.OUTLOOK_SCOPES),
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(self.TOKEN_ENDPOINT, data=token_data)
        except httpx.TimeoutException:
            raise ValueError("连接 Microsoft 服务器超时，请检查网络连接后重试。")
        except httpx.ConnectError:
            raise ValueError("无法连接 Microsoft 服务器，请检查网络连接。")
        except Exception as e:
            raise ValueError(f"请求 Microsoft 服务器失败: {e}")

        if response.status_code != 200:
            try:
                error_data = response.json()
            except Exception:
                error_data = {}
            error_type = error_data.get("error", "unknown")
            error_desc = error_data.get("error_description", response.text[:200])
            logger.error("Microsoft 返回错误: error=%s, description=%s", error_type, error_desc)
            raise ValueError(f"Outlook OAuth 错误: {error_type} - {error_desc}")

        data = response.json()
        access_token = data.get("access_token", "")
        refresh_token = data.get("refresh_token", "")
        email = self._decode_id_token_email(data.get("id_token", ""))
        if not email and access_token:
            email = await self._fetch_email_from_graph(access_token)
        self._validate_email(email)
        logger.info("Outlook OAuth token 获取成功: email=%s", email)

        return Credentials(
            provider_type="outlook",
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=int(time.time()) + data.get("expires_in", 3600),
            extra={"email": email},
        )

    async def refresh_token(self, credentials: Credentials) -> Credentials:
        """刷新访问令牌，并保留 Microsoft 未返回的新 refresh_token。"""
        if not credentials.refresh_token:
            raise ValueError("No refresh token available")

        # O10 修复：捕获网络异常，转换为 OAuthTokenError(is_permanent=False)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.TOKEN_ENDPOINT,
                    data={
                        "client_id": outlook_config.OUTLOOK_CLIENT_ID,
                        "client_secret": outlook_config.OUTLOOK_CLIENT_SECRET,
                        "refresh_token": credentials.refresh_token,
                        "grant_type": "refresh_token",
                        "scope": " ".join(outlook_config.OUTLOOK_SCOPES),
                    },
                )
        except Exception as e:
            logger.warning("刷新 Outlook token 网络异常: %s", e)
            raise OAuthTokenError(
                f"Outlook 刷新 token 网络异常: {e}",
                error_code="network_error",
                http_status=0,
                provider="outlook",
            )

        if response.status_code != 200:
            # 尝试解析错误详情，抛出结构化异常
            error_data = None
            try:
                error_data = response.json()
            except Exception as e:
                logger.debug("解析错误响应 JSON 失败: %s", e)
            if error_data and error_data.get("error"):
                error_code = error_data["error"]
                # 提取 AADSTS 码（如有），不打印完整 description
                error_codes = error_data.get("error_codes", [])
                aadsts = f" AADSTS{error_codes[0]}" if error_codes else ""
                # O11 修复：解析 Retry-After 头（429/503 限流场景）
                retry_after = parse_retry_after(response.headers.get("retry-after"))
                logger.error("刷新 Outlook token 失败: %s%s, retry_after=%s", error_code, aadsts, retry_after)
                raise OAuthTokenError(
                    f"Outlook OAuth 错误: {error_code}",
                    error_code=error_code,
                    http_status=response.status_code,
                    provider="outlook",
                    retry_after=retry_after,
                )
            logger.error("刷新 Outlook token 失败: HTTP %d", response.status_code)
            response.raise_for_status()
        data = response.json()

        return Credentials(
            provider_type="outlook",
            access_token=data.get("access_token", credentials.access_token),
            refresh_token=data.get("refresh_token") or credentials.refresh_token,
            expires_at=int(time.time()) + data.get("expires_in", 3600),
            extra=credentials.extra,
        )

    async def revoke_token(self, credentials: Credentials) -> bool:
        """Microsoft 邮箱授权暂不在本地撤销，调用方可直接视为成功。"""
        return True
