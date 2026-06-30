"""
飞邮应用设置管理服务
将用户配置持久化到 JSON 文件中，支持运行时热更新

存储路径说明（飞牛OS环境）:
  - 生产环境: FLYMAIL_DATA_DIR 由 cmd/main 设置为 TRIM_PKGVAR
    例如: /var/apps/flymail/var/settings.json
  - 本地开发: dev.py 设置为 backend/data/
"""
import os
import json
import asyncio
from typing import Any, Dict, Optional

from data_paths import CONFIG_DIR, ensure_data_dirs
from utils.logger import get_logger

# 模块级日志
logger = get_logger("settings")

# 数据目录优先级: 环境变量 FLYMAIL_DATA_DIR > 默认 fallback
SETTINGS_FILE = str(CONFIG_DIR / "settings.json")

# 默认设置（redirect_uri 默认为空，不再硬编码 localhost）
# 修复 D1：用户级配置（unified_account_ids/signature_html/signature_enabled）
# 已迁移到 user_settings 表，按 user_uid 隔离，不再放在全局 settings.json 中
DEFAULT_SETTINGS: Dict[str, Any] = {
    "gmail_client_id": "",
    "gmail_client_secret": "",
    "gmail_redirect_uri": "",
    "outlook_client_id": "",
    "outlook_client_secret": "",
    "outlook_redirect_uri": "",
}


def _ensure_data_dir():
    """确保数据目录存在"""
    ensure_data_dirs()


def load_settings() -> Dict[str, Any]:
    """加载设置，如果文件不存在则返回默认值"""
    _ensure_data_dir()
    if not os.path.exists(SETTINGS_FILE):
        logger.debug("配置文件不存在，使用默认值: %s", SETTINGS_FILE)
        return dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        # 合并默认值，确保新增的配置项有默认值
        merged = dict(DEFAULT_SETTINGS)
        merged.update(saved)
        logger.debug("加载配置成功: client_id=%s, secret=%s, redirect_uri=%s",
                     "有" if merged.get("gmail_client_id") else "空",
                     "有" if merged.get("gmail_client_secret") else "空",
                     merged.get("gmail_redirect_uri", "空"))
        return merged
    except (json.JSONDecodeError, IOError) as e:
        logger.error("加载配置失败: %s, 使用默认值", e)
        return dict(DEFAULT_SETTINGS)


def save_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    """保存设置到文件

    安全策略：
    - client_secret 为空或包含星号（脱敏值）时不覆盖已有密钥
    - redirect_uri 为空时不覆盖已有值
    """
    _ensure_data_dir()
    # 只允许保存已知的配置项
    valid_keys = set(DEFAULT_SETTINGS.keys())
    filtered = {k: v for k, v in settings.items() if k in valid_keys}

    # 加载当前配置
    current = load_settings()

    # 安全检查：Gmail client_secret 为空或包含星号（脱敏值）时不覆盖已有密钥
    gmail_secret_val = filtered.get("gmail_client_secret", "")
    if not gmail_secret_val or "*" in str(gmail_secret_val):
        filtered["gmail_client_secret"] = current.get("gmail_client_secret", "")
        logger.debug("Gmail client_secret 为空或脱敏值，保留已有密钥")

    # 安全检查：Outlook client_secret 为空或包含星号（脱敏值）时不覆盖已有密钥
    outlook_secret_val = filtered.get("outlook_client_secret", "")
    if not outlook_secret_val or "*" in str(outlook_secret_val):
        filtered["outlook_client_secret"] = current.get("outlook_client_secret", "")
        logger.debug("Outlook client_secret 为空或脱敏值，保留已有密钥")

    # Gmail redirect_uri 为空时不覆盖已有值（避免清空用户配置的回调地址）
    gmail_redirect_val = filtered.get("gmail_redirect_uri", "")
    if not gmail_redirect_val:
        filtered["gmail_redirect_uri"] = current.get("gmail_redirect_uri", "")
        logger.debug("Gmail redirect_uri 为空，保留已有值")

    # Outlook redirect_uri 为空时不覆盖已有值（避免清空用户配置的回调地址）
    outlook_redirect_val = filtered.get("outlook_redirect_uri", "")
    if not outlook_redirect_val:
        filtered["outlook_redirect_uri"] = current.get("outlook_redirect_uri", "")
        logger.debug("Outlook redirect_uri 为空，保留已有值")

    # 合并并保存
    current.update(filtered)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)

    logger.debug("保存成功: client_id=%s, secret=%s, redirect_uri=%s",
                "有(" + str(len(current.get("gmail_client_id", ""))) + "字符)" if current.get("gmail_client_id") else "空",
                "有(" + str(len(current.get("gmail_client_secret", ""))) + "字符)" if current.get("gmail_client_secret") else "空",
                current.get("gmail_redirect_uri", "空"))
    return current


def get_setting(key: str, default: Any = None) -> Any:
    """获取单个设置项"""
    settings = load_settings()
    return settings.get(key, default)


# ==================== 异步包装（避免阻塞 asyncio 事件循环） ====================

async def async_load_settings() -> Dict[str, Any]:
    """异步加载设置，将同步文件 I/O 放到线程池执行，不阻塞事件循环"""
    return await asyncio.to_thread(load_settings)


async def async_save_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    """异步保存设置，将同步文件 I/O 放到线程池执行，不阻塞事件循环"""
    return await asyncio.to_thread(save_settings, settings)
