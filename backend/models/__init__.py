from pydantic import BaseModel


class User(BaseModel):
    id: str
    username: str
    password_hash: str
    role: str = "user"
    status: str = "active"
    created_at: float = 0.0
    updated_at: float = 0.0


class Account(BaseModel):
    model_config = {"validate_assignment": True}

    id: str
    user_uid: str
    email: str
    provider: str
    credentials_json: str = ""
    status: str = "disconnected"
    remark: str = ""
    group_name: str = ""
    hide_email: bool = False
    poll_interval_seconds: int = 10
    created_at: float = 0.0
    updated_at: float = 0.0


class CachedMessage(BaseModel):
    id: str
    account_id: str
    user_uid: str
    uid: int
    folder: str
    subject: str
    from_addr: str
    to_addr: str
    date: str
    is_read: bool = False
    is_starred: bool = False
    has_attachments: bool = False
    body_text: str = ""
    body_html: str = ""
    storage_path: str = ""
    cached_at: float = 0.0


class CachedAttachment(BaseModel):
    account_id: str
    user_uid: str
    uid: int
    folder: str
    part_number: int
    filename: str = ""
    content_type: str = ""
    size: int = 0
    content_id: str = ""
    is_inline: bool = False
    local_path: str = ""
    cached_at: float = 0.0


class Notification(BaseModel):
    id: str
    user_uid: str
    account_id: str
    provider: str
    email: str
    folder: str
    is_read: bool = False
    created_at: float = 0.0
    type: str = "new_mail"
    message: str = ""


class Signature(BaseModel):
    id: int = 0
    name: str = ""
    content_html: str = ""
    is_default: int = 0
    account_id: str = ""
    user_uid: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
