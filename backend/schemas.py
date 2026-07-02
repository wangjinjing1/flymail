from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(description="服务状态")
    app: str = Field(description="应用名称")
    version: str = Field(description="版本号")


class UserResponse(BaseModel):
    uid: str = Field(description="飞牛OS 用户ID")
    username: str = Field(description="飞牛OS 用户名")


class SettingsResponse(BaseModel):
    uploads_cleanup_weekday: int = Field(default=0, ge=0, le=6, description="Upload cleanup weekday, 0=Monday")
    uploads_cleanup_time: str = Field(default="02:00", description="Upload cleanup time, HH:MM")
    gmail_client_id: str = Field(description="Gmail OAuth 客户端ID（完整）")
    gmail_client_secret: str = Field(description="Gmail OAuth 客户端密钥（脱敏，仅显示首尾4位）")
    gmail_redirect_uri: str = Field(description="Gmail OAuth 回调地址")
    has_credentials: bool = Field(description="是否已配置完整的 Gmail 凭据")
    outlook_client_id: str = Field(default="", description="Microsoft OAuth 客户端ID（完整）")
    outlook_client_secret: str = Field(default="", description="Microsoft OAuth 客户端密钥（脱敏，仅显示首尾4位）")
    outlook_redirect_uri: str = Field(default="", description="Microsoft OAuth 回调地址")
    has_outlook_credentials: bool = Field(default=False, description="是否已配置完整的 Microsoft 凭据")


class SettingsUpdateResponse(BaseModel):
    success: bool = Field(description="是否保存成功")
    message: str = Field(description="结果消息")


class SettingsUpdateRequest(BaseModel):
    """更新应用设置请求模型，所有字段可选。"""

    uploads_cleanup_weekday: Optional[int] = Field(default=None, ge=0, le=6, description="Upload cleanup weekday, 0=Monday")
    uploads_cleanup_time: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$", description="Upload cleanup time, HH:MM")
    gmail_client_id: Optional[str] = Field(default=None, max_length=500, description="Gmail OAuth 客户端ID")
    gmail_client_secret: Optional[str] = Field(default=None, max_length=500, description="Gmail OAuth 客户端密钥")
    gmail_redirect_uri: Optional[str] = Field(default=None, max_length=500, description="Gmail OAuth 回调地址")
    outlook_client_id: Optional[str] = Field(default=None, max_length=500, description="Microsoft OAuth 客户端ID")
    outlook_client_secret: Optional[str] = Field(default=None, max_length=500, description="Microsoft OAuth 客户端密钥")
    outlook_redirect_uri: Optional[str] = Field(default=None, max_length=500, description="Microsoft OAuth 回调地址")


class AuthUrlResponse(BaseModel):
    auth_url: str = Field(description="第三方授权页面 URL")
    provider: str = Field(description="邮箱平台类型")


class AuthUrlRequest(BaseModel):
    provider: str = Field(default="gmail", description="邮箱平台类型：gmail / outlook")
    redirect_uri: str = Field(default="", description="OAuth 回调地址")


class AuthCodeAccountRequest(BaseModel):
    email: str = Field(description="邮箱地址")
    auth_code: str = Field(description="邮箱授权码或应用专用密码")


class AccountInfo(BaseModel):
    id: str = Field(description="账号唯一ID")
    email: str = Field(description="邮箱地址")
    provider: str = Field(description="邮箱平台")
    status: str = Field(description="连接状态")
    remark: str = Field(description="备注名")
    group_name: str = Field(description="分组名称")
    hide_email: bool = Field(description="是否隐藏邮箱地址")
    poll_interval_seconds: int = Field(default=10, description="新邮件后台轮询间隔（秒）")
    created_at: float = Field(description="创建时间戳")


class AccountListResponse(BaseModel):
    accounts: List[AccountInfo] = Field(description="账号列表")


class AccountAddResponse(BaseModel):
    success: bool = Field(description="是否添加成功")
    account: AccountInfo = Field(description="新创建的账号信息")


class AccountTestResponse(BaseModel):
    success: bool = Field(description="连接是否成功")
    status: str = Field(description="连接状态")
    error: str = Field(default="", description="错误信息（连接失败时）")


class AccountUpdateRequest(BaseModel):
    remark: str = Field(default="", description="备注名")
    group_name: str = Field(default="", description="分组名称")
    hide_email: bool = Field(default=False, description="是否隐藏邮箱地址")
    poll_interval_seconds: int = Field(default=10, ge=5, le=3600, description="新邮件后台轮询间隔（秒）")


class StatusResponse(BaseModel):
    success: bool = Field(default=True, description="是否成功")


class DeleteResponse(BaseModel):
    success: bool = Field(description="是否删除成功")


class MessageResponse(BaseModel):
    success: bool = Field(description="是否成功")
    message: str = Field(default="", description="结果消息")


class OAuthDiagnosticResponse(BaseModel):
    status: str = Field(description="诊断状态")
    issues: List[str] = Field(description="发现的问题列表")
    runtime: Dict[str, str] = Field(description="运行时 OAuth 配置（已脱敏）")
    stored: Dict[str, str] = Field(description="持久化 OAuth 配置（已脱敏）")
    log_dir: str = Field(description="日志目录")
    tip: str = Field(description="排查建议")


class FolderItem(BaseModel):
    name: str = Field(description="文件夹显示名")
    path: str = Field(description="IMAP 文件夹路径")
    unread_count: int = Field(default=0, description="未读邮件数")
    total_count: int = Field(default=0, description="邮件总数")


class FolderCountItem(BaseModel):
    total: int = Field(description="邮件总数")
    unread: int = Field(description="未读邮件数")


class FolderResponse(BaseModel):
    folders: List[FolderItem] = Field(description="文件夹列表")
    account_id: str = Field(default="", description="账号ID")
    error: str = Field(default="", description="错误信息")
    reconnecting: bool = Field(default=False, description="邮箱连接异常时是否正在重连")


class FolderCountsResponse(BaseModel):
    counts: Dict[str, FolderCountItem] = Field(description="文件夹计数，key 为文件夹路径")
    account_id: str = Field(default="", description="账号ID")
    error: str = Field(default="", description="错误信息")
    reconnecting: bool = Field(default=False, description="邮箱连接异常时是否正在重连")


class AttachmentItem(BaseModel):
    filename: str = Field(default="", description="附件文件名")
    content_type: str = Field(default="", description="MIME 类型")
    size: int = Field(default=0, description="文件大小（字节）")
    part_number: int = Field(default=0, description="IMAP part 编号")
    content_id: str = Field(default="", description="Content-ID")
    is_inline: bool = Field(default=False, description="是否为内嵌附件")


class MessageItem(BaseModel):
    id: str = Field(description="邮件ID")
    uid: int = Field(description="IMAP UID")
    subject: str = Field(default="", description="邮件主题")
    from_addr: str = Field(default="", description="发件人")
    to_addr: str = Field(default="", description="收件人")
    date: str = Field(default="", description="邮件日期")
    is_read: bool = Field(default=False, description="是否已读")
    is_starred: bool = Field(default=False, description="是否星标")
    folder: str = Field(default="INBOX", description="文件夹路径")
    body_text: str = Field(default="", description="纯文本正文")
    body_html: str = Field(default="", description="HTML 正文")
    attachments: List[AttachmentItem] = Field(default=[], description="附件列表")
    has_attachments: bool = Field(default=False, description="是否包含附件")
    account_id: str = Field(default="", description="账号ID")


class MessageListResponse(BaseModel):
    messages: List[MessageItem] = Field(description="邮件列表")
    total: int = Field(description="邮件总数")
    unread_total: int = Field(default=0, description="未读邮件总数")
    page: int = Field(description="当前页码")
    page_size: int = Field(description="每页数量")
    account_id: str = Field(default="", description="账号ID")
    error: str = Field(default="", description="错误信息")
    reconnecting: bool = Field(default=False, description="邮箱连接异常时是否正在重连")
    filter_counts: dict = Field(default={}, description="筛选计数")


class PrefetchMessagesRequest(BaseModel):
    message_ids: List[str] = Field(default=[], max_length=50, description="需要预取正文的邮件ID列表")
    account_id: str = Field(default="", description="账号ID")
    folder: str = Field(default="INBOX", description="文件夹路径")


class PrefetchMessagesResponse(BaseModel):
    success: bool = Field(default=True, description="是否成功")
    queued: int = Field(default=0, description="已加入后台预取队列的邮件数量")
    prefetched: int = Field(default=0, description="已预取数量")


class MarkReadRequest(BaseModel):
    message_id: str = Field(description="邮件ID")
    folder: str = Field(default="INBOX", description="文件夹路径")
    account_id: str = Field(default="", description="账号ID")


class BatchMarkReadRequest(BaseModel):
    message_ids: List[str] = Field(description="邮件ID列表")
    folder: str = Field(default="INBOX", description="文件夹路径")
    account_id: str = Field(default="", description="账号ID")


class BatchMarkReadResponse(BaseModel):
    success: bool = Field(description="是否成功")
    marked: int = Field(description="成功标记数量")


class BatchDeleteRequest(BaseModel):
    message_ids: List[str] = Field(description="邮件ID列表")
    account_id: str = Field(default="", description="账号ID")
    folder: str = Field(default="INBOX", description="文件夹路径")


class BatchDeleteResponse(BaseModel):
    success: bool = Field(description="是否成功")
    deleted: int = Field(description="成功删除数量")


class SendMessageRequest(BaseModel):
    to: str = Field(description="收件人邮箱地址")
    subject: str = Field(description="邮件主题")
    content: str = Field(description="邮件正文")
    html: bool = Field(default=False, description="是否为 HTML 格式")


class SendMessageResponse(BaseModel):
    success: bool = Field(default=True, description="是否成功")
    message: str = Field(description="结果消息")


class ComposeMessageRequest(BaseModel):
    account_id: str = Field(default="", description="发件账号ID")
    to: list[str] = Field(default=[], max_length=50, description="收件人列表")
    cc: list[str] = Field(default=[], max_length=50, description="抄送列表")
    bcc: list[str] = Field(default=[], max_length=50, description="密送列表")
    subject: str = Field(default="", max_length=500, description="邮件主题")
    body_html: str = Field(default="", description="HTML 格式正文")
    attachments: list[str] = Field(default=[], max_length=20, description="附件文件路径列表")
    action: str = Field(default="send", description="操作类型")
    schedule_time: str | None = Field(default=None, description="ISO8601 定时发送时间")
    in_reply_to: str | None = Field(default=None, description="回复的邮件 Message-ID")
    forward_from: str | None = Field(default=None, description="转发的邮件 Message-ID")


class ComposeMessageResponse(BaseModel):
    success: bool = Field(default=True, description="是否成功")
    message: str = Field(description="结果消息")
    job_id: str = Field(default="", description="定时发送任务ID")
    sent_folder: str = Field(default="", description="已发送文件夹路径")


class UploadAttachmentResponse(BaseModel):
    filename: str = Field(description="原始文件名")
    size: int = Field(description="文件大小（字节）")
    path: str = Field(description="服务端临时附件路径")


class SignatureSettingsRequest(BaseModel):
    signature_html: str = Field(default="", description="签名 HTML 内容")
    signature_enabled: bool = Field(default=False, description="是否启用签名")


class SignatureSettingsResponse(BaseModel):
    signature_html: str = Field(default="", description="签名 HTML 内容")
    signature_enabled: int = Field(default=0, description="是否启用签名")


class SignatureTemplateRequest(BaseModel):
    name: str = Field(default="", description="签名模板名称")
    content_html: str = Field(default="", description="签名 HTML 内容")
    is_default: bool = Field(default=False, description="是否默认签名")
    account_id: str = Field(default="", description="关联账号ID")


class SignatureTemplateUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, description="签名模板名称")
    content_html: Optional[str] = Field(default=None, description="签名 HTML 内容")
    is_default: Optional[bool] = Field(default=None, description="是否默认签名")
    account_id: Optional[str] = Field(default=None, description="关联账号ID")


class SignatureTemplateItem(BaseModel):
    id: int = Field(description="签名模板ID")
    name: str = Field(description="签名模板名称")
    content_html: str = Field(default="", description="签名 HTML 内容")
    is_default: bool = Field(default=False, description="是否默认签名")
    account_id: str = Field(default="", description="关联账号ID")


class SignatureListResponse(BaseModel):
    signatures: List[SignatureTemplateItem] = Field(description="签名模板列表")


class ScheduledMessagesResponse(BaseModel):
    jobs: List[Dict[str, Any]] = Field(description="待执行的定时发送任务列表")


class NotificationItem(BaseModel):
    id: str = Field(description="通知ID")
    account_id: str = Field(description="账号ID")
    provider: str = Field(description="邮箱平台")
    email: str = Field(description="邮箱地址")
    folder: str = Field(description="文件夹")
    is_read: bool = Field(description="是否已读")
    time: float = Field(description="通知时间")
    type: str = Field(default="new_mail", description="通知类型")
    message: str = Field(default="", description="通知描述文本")


class NotificationListResponse(BaseModel):
    notifications: List[NotificationItem] = Field(description="通知列表")


class NotificationReadResponse(BaseModel):
    success: bool = Field(description="是否成功")


class NotificationReadAllResponse(BaseModel):
    success: bool = Field(description="是否成功")
    updated: int = Field(description="更新的通知数量")


class NotificationClearResponse(BaseModel):
    success: bool = Field(description="是否成功")
    deleted: int = Field(description="删除的通知数量")


class ErrorResponse(BaseModel):
    error: str = Field(description="错误信息")
