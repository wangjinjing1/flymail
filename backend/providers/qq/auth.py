import time
from typing import Dict, Any
from ..base import AuthProvider, Credentials


class QQAuthProvider(AuthProvider):
    """QQ邮箱认证提供者（使用授权码认证，不需要OAuth）"""

    def get_auth_url(self, redirect_uri: str = "", state: str = "") -> str:
        """QQ邮箱不需要OAuth授权URL，返回空字符串"""
        return ""

    async def handle_callback(self, code: str, redirect_uri: str = "") -> Credentials:
        """QQ邮箱不需要OAuth回调，返回空凭据"""
        return Credentials(provider_type="qq")

    async def refresh_token(self, credentials: Credentials) -> Credentials:
        """QQ邮箱不需要刷新令牌"""
        return credentials

    async def revoke_token(self, credentials: Credentials) -> bool:
        """QQ邮箱不需要撤销令牌"""
        return True

    @staticmethod
    def create_credentials(email: str, auth_code: str) -> Credentials:
        """创建QQ邮箱凭据（使用授权码）"""
        return Credentials(
            provider_type="qq",
            access_token=auth_code,  # QQ邮箱使用授权码作为访问令牌
            refresh_token="",
            expires_at=0,  # 授权码不过期
            extra={"email": email}
        )
