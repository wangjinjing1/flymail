"""飞邮统一日志管理模块

所有模块统一通过 get_logger("模块名") 获取 logger，不再各自调用 logging.getLogger。

功能:
  1. 集中管理所有 logger 的创建和命名
  2. 支持环境变量 FLYMAIL_LOG_LEVEL 设置初始级别
  3. 提供 setup_logging() 初始化根日志配置（文件轮转、格式等）
  4. 提供 set_level() / get_levels() 运行时动态调整

使用方式:
  from utils.logger import get_logger
  logger = get_logger("sync")       # 自动加 flymail. 前缀 → flymail.sync
  logger.info("同步完成")

模块名对照:
  "main"      → flymail           主应用
  "qq"        → flymail.qq        QQ邮箱
  "gmail"     → flymail.gmail     Gmail
  "gmail.auth"→ flymail.gmail.auth Gmail OAuth
  "netease"   → flymail.netease   网易邮箱
  "sync"      → flymail.sync      IDLE新邮件监听
  "cache"     → flymail.cache     邮件缓存同步
  "settings"  → flymail.settings  设置读写
  "dev"       → flymail.dev       开发调试
"""

import os
import sys
import logging
from logging.handlers import TimedRotatingFileHandler
from typing import Dict, Optional

# ==================== 模块名注册表 ====================
# 所有模块的短名 → 完整 logger 名，统一管理
_MODULE_NAMES: Dict[str, str] = {
    "main":       "flymail",
    "qq":         "flymail.qq",
    "gmail":      "flymail.gmail",
    "gmail.auth": "flymail.gmail.auth",
    "netease":    "flymail.netease",
    "sync":       "flymail.sync",
    "cache":      "flymail.cache",
    "settings":   "flymail.settings",
    "dev":        "flymail.dev",
}

# 日志格式
_LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ==================== 日志级别开关 ====================
# 改这里就行：DEBUG / INFO / WARNING / ERROR
# DEBUG  = 输出所有调试日志（排查问题时用）
# INFO   = 正常运行日志（默认）
LOG_LEVEL = logging.INFO

# 是否已初始化
_initialized = False


def setup_logging(data_dir: Optional[str] = None):
    """初始化日志系统（应用启动时调用一次）

    做三件事:
      1. 配置根日志（格式、输出到 stdout）
      2. 按天轮转的文件日志（写入 data_dir/flymail.log）
      3. 读取环境变量 FLYMAIL_LOG_LEVEL 设置初始级别
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    # 1. 根日志配置
    # 根日志设为 WARNING，避免第三方库（aiosqlite、httpx 等）刷 DEBUG 日志
    # flymail 自身的日志级别由各模块 logger 单独控制
    logging.basicConfig(
        level=logging.WARNING,
        format=_LOG_FORMAT,
        datefmt=_LOG_DATE_FORMAT,
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    # flymail 根 logger 使用硬编码的 LOG_LEVEL
    logging.getLogger("flymail").setLevel(LOG_LEVEL)

    # 2. 文件日志（按天轮转，只保留当天）
    if data_dir:
        try:
            os.makedirs(data_dir, exist_ok=True)
            log_file = os.path.join(data_dir, "flymail.log")
            file_handler = TimedRotatingFileHandler(
                log_file, when="midnight", interval=1, backupCount=1, encoding="utf-8"
            )
            file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _LOG_DATE_FORMAT))
            logging.getLogger().addHandler(file_handler)
        except Exception as e:
            logging.getLogger("flymail").warning("无法创建日志文件: %s", e)


def get_logger(module: str) -> logging.Logger:
    """获取模块的 logger（统一入口）

    参数:
      module: 模块短名，如 "sync"、"qq"、"cache"
              也可以传完整名如 "flymail.sync"，会自动识别

    返回:
      logging.Logger 实例

    示例:
      logger = get_logger("sync")       # → flymail.sync
      logger = get_logger("flymail.qq") # → flymail.qq（兼容完整名）
    """
    # 先查注册表，找不到就直接用
    full_name = _MODULE_NAMES.get(module, module)
    return logging.getLogger(full_name)


def set_level(level: str, module: Optional[str] = None):
    """运行时动态调整日志级别

    参数:
      level: DEBUG / INFO / WARNING / ERROR
      module: 模块短名，不填则调整所有模块

    示例:
      set_level("DEBUG")                    # 全部开 DEBUG
      set_level("DEBUG", "sync")            # 只开 sync 的 DEBUG
      set_level("INFO")                     # 全部恢复 INFO
    """
    level = level.upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise ValueError(f"无效的日志级别: {level}，必须是 DEBUG/INFO/WARNING/ERROR")

    level_val = getattr(logging, level)

    if module:
        full_name = _MODULE_NAMES.get(module, module)
        logging.getLogger(full_name).setLevel(level_val)
    else:
        # 调整所有注册的模块
        for full_name in _MODULE_NAMES.values():
            logging.getLogger(full_name).setLevel(level_val)


def get_levels() -> Dict[str, str]:
    """获取所有模块的当前日志级别"""
    result = {}
    for short, full in _MODULE_NAMES.items():
        lg = logging.getLogger(full)
        result[short] = logging.getLevelName(lg.getEffectiveLevel())
    return result
