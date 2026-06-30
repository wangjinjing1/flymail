"""飞邮版本号统一管理

从项目根目录的 VERSION 文件读取版本号，所有涉及版本信息的地方都从此模块导入。
修改版本号时只需修改根目录的 VERSION 文件即可。
"""

import os

# 从项目根目录的 VERSION 文件读取版本号
_VERSION_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION")

try:
    with open(_VERSION_FILE, "r", encoding="utf-8") as f:
        VERSION = f.read().strip()
except Exception:
    VERSION = "0.0.0"  # 读取失败时的回退值
