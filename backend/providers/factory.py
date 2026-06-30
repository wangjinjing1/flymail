from typing import Dict, Type
from .base import AuthProvider, MailReceiver, MailSender


class ProviderFactory:
    _auth_providers: Dict[str, Type[AuthProvider]] = {}
    _receiver_providers: Dict[str, Type[MailReceiver]] = {}
    _sender_providers: Dict[str, Type[MailSender]] = {}

    @classmethod
    def register(cls, provider_type: str, auth: Type[AuthProvider], 
                 receiver: Type[MailReceiver], sender: Type[MailSender]):
        cls._auth_providers[provider_type] = auth
        cls._receiver_providers[provider_type] = receiver
        cls._sender_providers[provider_type] = sender

    @classmethod
    def get_auth(cls, provider_type: str) -> AuthProvider:
        if provider_type not in cls._auth_providers:
            raise ValueError(f"Unknown provider: {provider_type}")
        return cls._auth_providers[provider_type]()

    @classmethod
    def get_receiver(cls, provider_type: str) -> MailReceiver:
        if provider_type not in cls._receiver_providers:
            raise ValueError(f"Unknown provider: {provider_type}")
        return cls._receiver_providers[provider_type]()

    @classmethod
    def get_sender(cls, provider_type: str) -> MailSender:
        if provider_type not in cls._sender_providers:
            raise ValueError(f"Unknown provider: {provider_type}")
        return cls._sender_providers[provider_type]()

    @classmethod
    def get_supported_providers(cls) -> list:
        return list(cls._auth_providers.keys())


# 注册 Gmail Provider
from .gmail import GmailAuthProvider, GmailReceiver, GmailSender
ProviderFactory.register("gmail", GmailAuthProvider, GmailReceiver, GmailSender)

# 注册 QQ 邮箱 Provider
from .qq import QQAuthProvider, QQReceiver, QQSender
ProviderFactory.register("qq", QQAuthProvider, QQReceiver, QQSender)

# 注册网易邮箱 Provider（163/126邮箱）
from .netease import NeteaseAuthProvider, NeteaseReceiver, NeteaseSender
ProviderFactory.register("netease", NeteaseAuthProvider, NeteaseReceiver, NeteaseSender)

# 注册 iCloud 邮箱 Provider
from .icloud import ICloudAuthProvider, ICloudReceiver, ICloudSender
ProviderFactory.register("icloud", ICloudAuthProvider, ICloudReceiver, ICloudSender)

# 注册 Microsoft/Outlook 邮箱 Provider
from .outlook import OutlookAuthProvider, OutlookReceiver, OutlookSender
ProviderFactory.register("outlook", OutlookAuthProvider, OutlookReceiver, OutlookSender)
