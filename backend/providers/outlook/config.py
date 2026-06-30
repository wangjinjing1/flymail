# Outlook OAuth2 配置
OUTLOOK_CLIENT_ID = ""
OUTLOOK_CLIENT_SECRET = ""
OUTLOOK_REDIRECT_URI = ""
OUTLOOK_AUTH_ENDPOINT = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
OUTLOOK_TOKEN_ENDPOINT = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
OUTLOOK_IMAP_HOST = "outlook.office365.com"
OUTLOOK_IMAP_PORT = 993
# SMTP 服务器：个人账户（outlook.com/hotmail.com）用 smtp-mail.outlook.com，
# 企业账户（Microsoft 365）用 smtp.office365.com。
# 实际使用中 smtp-mail.outlook.com 兼容性更好，两个地址均可用于个人和企业账户。
OUTLOOK_SMTP_HOST = "smtp-mail.outlook.com"
OUTLOOK_SMTP_PORT = 587
OUTLOOK_SCOPES = [
    "openid",
    "profile",
    "email",
    "offline_access",
    "https://outlook.office.com/IMAP.AccessAsUser.All",
    "https://outlook.office.com/SMTP.Send",
]
SUPPORTED_DOMAINS = ("@outlook.com", "@hotmail.com", "@live.com", "@msn.com")
