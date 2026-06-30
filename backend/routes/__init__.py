"""路由模块包

将 main.py 中的 API 端点按功能模块拆分为独立的路由文件。

模块划分：
- accounts.py: 账号管理（添加、删除、测试、更新）
- folders.py: 文件夹列表和计数
- messages.py: 邮件列表、详情、标记、删除、附件
- notifications.py: 通知管理
- compose.py: 写信、定时发送
- auth.py: OAuth 认证流程
- websocket.py: WebSocket 实时推送
"""
