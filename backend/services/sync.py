"""邮件实时同步服务 - 监听新邮件，通过 WebSocket 推送到前端

架构:
  前端 ←→ WebSocket ←→ MailSyncService ←→ IMAP IDLE/STATUS ←→ 邮箱服务器

工作流程:
  1. 应用启动时即为所有邮箱账号启动新邮件监听（后台常驻）
  2. Gmail/QQ/Outlook 使用 aioimaplib IDLE；iCloud 使用 aioimaplib STATUS 轮询；网易使用 imaplib STATUS 轮询
  3. 检测到新邮件后，持久化通知到数据库，并通过 WebSocket 推送给已连接的前端
  4. 前端关闭不影响后台监听，重新打开后从数据库恢复未读通知
"""

import asyncio
import json
import uuid
import time
from typing import Dict, Optional

from utils.logger import get_logger

logger = get_logger("sync")

from fastapi import WebSocket

from db import create_notification
from db import get_accounts
from models import Notification
from providers.base import Credentials
from providers.factory import ProviderFactory


class MailSyncService:
    """邮件实时同步服务

    生命周期:
      - 启动: start_all() 为所有账号启动新邮件监听
      - 运行: IDLE/STATUS 监听新邮件，断开后自动重连
      - 重连策略: 前 3 次快速重试（5秒间隔），之后指数退避（10→20→40→80→160→300秒），上限5分钟
      - 协作: 检测到新邮件后调用 mail_cache 增量同步，同步结果通过 WebSocket 推送前端
    """

    IDLE_REISSUE_INTERVAL = 5 * 60  # NOOP provider 每 5 分钟检查一次邮件数量变化
    INITIAL_RECONNECT_DELAY = 5  # 首次连接失败时的快速重试延迟 5 秒
    MAX_RECONNECT_DELAY = 300    # 最大重连延迟 5 分钟（指数退避上限）
    INITIAL_FAST_RETRIES = 3     # 首次连接失败时快速重试次数

    def __init__(self):
        # WebSocket 客户端集合：ws → 用户 uid 映射，推送时按用户过滤
        self.ws_clients: Dict[WebSocket, str] = {}
        # 每个账号的新邮件监听任务: account_id -> asyncio.Task
        self.idle_tasks: Dict[str, asyncio.Task] = {}
        self._running = False
        # 需要重新授权的账号 ID 集合（前端加载时可查询）
        self.reauth_account_ids: set = set()

    # ==================== WebSocket 客户端管理 ====================

    async def add_client(self, ws: WebSocket, uid: str = ""):
        """添加 WebSocket 客户端，绑定用户 uid"""
        self.ws_clients[ws] = uid

    async def remove_client(self, ws: WebSocket):
        """移除 WebSocket 客户端（前端关闭不影响后台监听）"""
        self.ws_clients.pop(ws, None)

    async def _broadcast(self, message: str, user_uid: str = ""):
        """向 WebSocket 客户端广播消息

        按 user_uid 过滤：有 uid 时只推给匹配的用户，无 uid 时推给所有。
        发送失败的客户端自动移除。

        安全修复 S7：旧代码 `if user_uid and uid and uid != user_uid` 在 uid 为空时
        条件为 False，导致未鉴权客户端收到所有用户定向消息。
        修复：改为 `if user_uid and uid != user_uid`，uid 为空时也跳过。

        修复 P7：旧代码串行 `await ws.send_text()`，慢客户端阻塞全部。
        修复：改用 asyncio.gather 并发发送。
        """
        # 筛选目标客户端
        targets = [
            (ws, uid) for ws, uid in self.ws_clients.items()
            if not user_uid or uid == user_uid
        ]
        if not targets:
            return

        # 并发发送，慢客户端不阻塞其他客户端
        async def _send_safe(ws, uid):
            try:
                await ws.send_text(message)
            except Exception:
                self.ws_clients.pop(ws, None)

        await asyncio.gather(*[_send_safe(ws, uid) for ws, uid in targets], return_exceptions=True)

    async def refresh_clients(self, account_id: str, folder: str = "INBOX", user_uid: str = "", folder_counts: dict = None):
        """仅推送 WebSocket 刷新信号（不创建通知记录）

        用于缓存同步场景：前端收到后刷新列表即可，不需要弹出"新邮件"通知。
        只推送给该账号所属用户的客户端。
        """
        payload = {
            "type": "cache_updated",
            "account_id": account_id,
            "folder": folder,
        }
        if folder_counts is not None:
            payload["folder_counts"] = folder_counts
        message = json.dumps(payload)
        await self._broadcast(message, user_uid)

    async def notify_connection_status(self, account_id: str, status: str, user_uid: str = "", error: str = ""):
        """推送账号连接状态变化（断连/恢复/错误）"""
        payload = {
            "type": "connection_status",
            "account_id": account_id,
            "status": status,  # "connected" | "disconnected" | "error"
        }
        if error:
            payload["error"] = error
        await self._broadcast(json.dumps(payload), user_uid)

    async def notify_sync_progress(self, account_id: str, current_folder: str, completed: int, total: int, user_uid: str = ""):
        """推送同步进度（每完成一个文件夹调用一次）"""
        await self._broadcast(json.dumps({
            "type": "sync_progress",
            "account_id": account_id,
            "current_folder": current_folder,
            "completed": completed,
            "total": total,
        }), user_uid)

    async def notify_message_state_changed(self, account_id: str, action: str, uids: list, folder: str = "INBOX", user_uid: str = ""):
        """推送邮件状态变化（标记已读/删除/移动等），用于跨标签页同步"""
        await self._broadcast(json.dumps({
            "type": "message_state_changed",
            "account_id": account_id,
            "action": action,  # "mark_read" | "mark_unread" | "delete" | "move"
            "uids": uids,
            "folder": folder,
        }), user_uid)

    async def notify_clients(self, account_id: str, folder: str = "INBOX",
                                provider: str = "", email: str = "", user_uid: str = ""):
        """通知 WebSocket 客户端有新邮件，同时将通知持久化到数据库

        仅在 IDLE 检测到真正的新邮件时调用，缓存同步场景请用 refresh_clients。
        只推送给该账号所属用户的客户端。
        """
        # 先持久化通知到数据库（获取数据库中的通知ID）
        notification_id = str(uuid.uuid4())
        try:
            notification = Notification(
                id=notification_id,
                user_uid=user_uid or "default",
                account_id=account_id,
                provider=provider,
                email=email,
                folder=folder,
                is_read=False,
                created_at=time.time(),
            )
            await create_notification(notification)
        except Exception as e:
            logger.warning("通知持久化失败: %s", e)
            # 持久化失败时仍用UUID作为ID，前端可正常显示，只是刷新后丢失

        # 广播消息（带上数据库中的通知ID，前端标记已读时需要）
        message = json.dumps({
            "type": "new_mail",
            "notification_id": notification_id,
            "account_id": account_id,
            "folder": folder,
            "provider": provider,
            "email": email,
        })
        await self._broadcast(message, user_uid)

    async def notify_schedule_result(
        self,
        user_uid: str,
        account_id: str,
        provider: str,
        email: str,
        subject: str,
        success: bool,
        error_msg: str = "",
    ):
        """定时发送结果通知：推送 WebSocket + 持久化到数据库

        success=True 时推送 schedule_success 类型，
        success=False 时推送 schedule_failed 类型。
        """
        notif_type = "schedule_success" if success else "schedule_failed"
        if success:
            message_text = f"定时邮件发送成功：{subject or '(无主题)'}"
        else:
            message_text = f"定时邮件发送失败：{subject or '(无主题)'}"
            if error_msg:
                message_text += f"（{error_msg}）"

        # 持久化通知到数据库
        notification_id = str(uuid.uuid4())
        try:
            from models import Notification
            notification = Notification(
                id=notification_id,
                user_uid=user_uid or "default",
                account_id=account_id,
                provider=provider,
                email=email,
                folder="",
                is_read=False,
                created_at=time.time(),
                type=notif_type,
                message=message_text,
            )
            await create_notification(notification)
        except Exception as e:
            logger.warning("定时发送通知持久化失败: %s", e)

        # 广播 WebSocket 消息
        message = json.dumps({
            "type": notif_type,
            "notification_id": notification_id,
            "account_id": account_id,
            "provider": provider,
            "email": email,
            "subject": subject,
            "message": message_text,
        })
        logger.info("推送定时发送通知: %s, %s", notif_type, subject)
        await self._broadcast(message, user_uid)

    # ==================== IDLE 监听管理 ====================

    async def _start_all_idle(self):
        """为所有邮箱账号启动新邮件监听（遍历所有用户）"""
        self._running = True
        try:
            # 启动时获取所有用户的账号（空字符串表示查所有用户）
            all_accounts = await get_accounts("")
            for account in all_accounts:
                if account.id not in self.idle_tasks:
                    task = asyncio.create_task(self._idle_loop(account))
                    self.idle_tasks[account.id] = task
        except Exception as e:
            logger.error("启动新邮件监听失败: %s", e)

    async def _stop_all_idle(self):
        """停止所有新邮件监听"""
        self._running = False
        tasks = list(self.idle_tasks.values())
        for task in tasks:
            task.cancel()
        # 等待所有任务清理完成，避免 "Task was destroyed but it is pending" 错误
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.idle_tasks.clear()

    async def add_account(self, account_id: str):
        """新增账号或重新授权后启动新邮件监听

        如果旧任务仍在运行（如重新授权场景），先取消旧任务再创建新任务，
        确保新任务使用最新的 credentials（重新授权后数据库已更新）。
        如果旧任务已结束（如 token 永久失效后 return），直接清理后创建新任务。
        """
        # 清理旧任务：如果仍在运行则取消，如果已结束则直接移除引用
        old_task = self.idle_tasks.get(account_id)
        if old_task is not None:
            if old_task.done():
                # 旧任务已结束（token 失效等），直接移除
                del self.idle_tasks[account_id]
            else:
                # 旧任务仍在运行（重新授权场景），取消后等待清理
                old_task.cancel()
                try:
                    await old_task
                except asyncio.CancelledError:
                    pass  # 预期的取消信号
                except Exception as e:
                    logger.debug("清理 IDLE 任务时忽略异常: %s", e)
                self.idle_tasks.pop(account_id, None)
                # 清理该账号的 IDLE 连接
                from services.idle_manager import idle_manager
                await idle_manager.remove(account_id)

        try:
            # 查找所有用户中匹配的账号
            all_accounts = await get_accounts("")
            for account in all_accounts:
                if account.id == account_id:
                    task = asyncio.create_task(self._idle_loop(account))
                    self.idle_tasks[account.id] = task
                    break
        except Exception as e:
            logger.error("为新账号启动新邮件监听失败: %s", e)

    async def remove_account(self, account_id: str):
        """删除账号时停止新邮件监听"""
        task = self.idle_tasks.pop(account_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # ==================== IDLE 监听循环 ====================

    # IDLE provider 列表（使用 aioimaplib 的原生异步 IDLE）
    IDLE_PROVIDERS = ("gmail", "qq", "outlook")
    # 轮询 provider 列表（使用 aioimaplib 的异步 STATUS 轮询，不阻塞线程池）
    POLL_PROVIDERS = ("icloud",)

    def _get_idle_config(self, account, credentials) -> dict:
        """获取 IDLE/轮询连接配置"""
        from providers.gmail.config import GMAIL_IMAP_HOST, GMAIL_IMAP_PORT
        from providers.outlook.config import OUTLOOK_IMAP_HOST, OUTLOOK_IMAP_PORT
        from providers.outlook.receiver import _create_outlook_ssl_context

        if account.provider == "gmail":
            return {
                "host": GMAIL_IMAP_HOST, "port": GMAIL_IMAP_PORT,
                "email": credentials.extra.get("email", ""),
                "auth_type": "xoauth2", "auth_credential": credentials.access_token,
            }
        elif account.provider == "qq":
            return {
                "host": "imap.qq.com", "port": 993,
                "email": account.email,
                "auth_type": "login", "auth_credential": credentials.access_token,
            }
        elif account.provider == "outlook":
            return {
                "host": OUTLOOK_IMAP_HOST, "port": OUTLOOK_IMAP_PORT,
                "email": credentials.extra.get("email", ""),
                "auth_type": "xoauth2", "auth_credential": credentials.access_token,
                "ssl_context": _create_outlook_ssl_context(),
            }
        elif account.provider == "icloud":
            return {
                "host": "imap.mail.me.com", "port": 993,
                "email": account.email,
                "auth_type": "login", "auth_credential": credentials.access_token,
            }
        else:
            raise ValueError(f"不支持的 provider: {account.provider}")

    async def _handle_new_mail(self, account, folder: str):
        """处理新邮件：同步缓存 + 推送通知 + 刷新列表"""
        logger.info("账号 %s 文件夹 %s 检测到新邮件", account.email, folder)
        try:
            from services.mail_cache import sync_folder_to_cache
            new_count = await sync_folder_to_cache(account, folder)
        except Exception as e:
            logger.warning("缓存同步失败: %s", e)
            new_count = 0

        logger.info("账号 %s 文件夹 %s 缓存同步完成, new_count=%d, is_inbox=%s",
                    account.email, folder, new_count, folder.upper() == "INBOX")

        if folder.upper() == "INBOX" and new_count > 0:
            logger.info("账号 %s 发送新邮件通知: new_count=%d", account.email, new_count)
            await self.notify_clients(
                account.id, folder,
                provider=account.provider, email=account.email,
                user_uid=account.user_uid,
            )

        await self.refresh_clients(account.id, folder, user_uid=account.user_uid)

    async def _idle_loop(self, account):
        """单个账号的新邮件监听循环

        根据 provider 类型选择监听方式：
        - Gmail/QQ/Outlook：使用 aioimaplib 的原生异步 IDLE（idle_manager）
        - iCloud：使用 aioimaplib 的异步 STATUS 轮询（poll_manager），不阻塞线程池
        - 网易：使用 imaplib 的 NOOP/STATUS 轮询（需要 IMAP ID 命令）
        """
        use_idle = account.provider in self.IDLE_PROVIDERS
        use_poll = account.provider in self.POLL_PROVIDERS
        consecutive_failures = 0
        receiver = None  # 网易使用（需要 IMAP ID 命令）
        poll_conn = None  # Poll provider 使用

        while self._running:
            try:
                fresh_accounts = await get_accounts(account.user_uid)
                account = next((a for a in fresh_accounts if a.id == account.id), account)

                from services.token import ensure_token
                credentials = await ensure_token(account)

                if use_idle:
                    # IDLE provider：为每个文件夹创建独立的 IDLE 连接（方案 A：并行 IDLE）
                    from services.idle_manager import idle_manager, IDLE_WAIT_TIMEOUT
                    idle_config = self._get_idle_config(account, credentials)

                    # 动态获取文件夹列表（与网易一致，兼容 Outlook 个人/Hotmail 不同文件夹名）
                    receiver = ProviderFactory.get_receiver(account.provider)
                    await receiver.connect(credentials)
                    try:
                        idle_folders = await self._get_idle_folders(receiver, account)
                    finally:
                        await receiver.disconnect()

                    # idle_conns 在外层 while 循环间保持，重连时只补建断开的连接
                    # 避免每次重连都重建所有 5 个连接，减少对邮件服务器的连接压力
                    idle_conns: dict = {}

                    async def _ensure_idle_connections():
                        """补全断开的 IDLE 连接（只重建需要的，复用存活的）

                        原逻辑：每次重连都重建所有连接，对 Outlook 产生 5 倍连接压力
                        新逻辑：只重建 idle_conns 中缺失或 connected=False 的连接
                        关键：重建前先刷新 token，避免用过期 token 认证导致 AUTHENTICATIONFAILED
                        """
                        # 重建连接前先刷新 token（token 可能在 IDLE 期间过期）
                        nonlocal idle_config
                        try:
                            from services.token import ensure_token
                            fresh_creds = await ensure_token(account)
                            idle_config = self._get_idle_config(account, fresh_creds)
                        except Exception as e:
                            logger.warning("账号 %s token 刷新失败: %s", account.email, e)

                        for folder in idle_folders:
                            # 跳过已存在且仍存活的连接
                            existing = idle_conns.get(folder)
                            if existing and existing.connected:
                                continue
                            # 旧连接已断开，先清理
                            if existing is not None:
                                idle_conns.pop(folder, None)
                            # 创建新连接（用刷新后的 token）
                            try:
                                conn = await idle_manager.get_or_create(
                                    account.id, **idle_config, folder=folder
                                )
                                idle_conns[folder] = conn
                            except Exception as e:
                                logger.warning("账号 %s 文件夹 %s IDLE 连接失败: %s", account.email, folder, e)

                    # 首次建立所有连接
                    await _ensure_idle_connections()
                    if idle_conns:
                        # 首次启动用 INFO（重要事件），重连后用 DEBUG（避免日志刷屏）
                        if consecutive_failures == 0:
                            logger.info("账号 %s 新邮件监听已启动 (IDLE x%d): %s",
                                       account.email, len(idle_conns), list(idle_conns.keys()))
                        else:
                            logger.debug("账号 %s IDLE 连接已就绪 (x%d)", account.email, len(idle_conns))
                        consecutive_failures = 0
                        await self.notify_connection_status(account.id, "connected", account.user_uid)

                    # 单个文件夹的 IDLE 循环（连接断开时抛异常，由外层负责重建）
                    async def _idle_folder(folder: str, conn):
                        """单个文件夹的 IDLE 循环

                        正常运行时在 while 循环中持续等待事件。
                        连接断开时抛出异常，由外层循环负责清理和重建连接。
                        """
                        while self._running:
                            event = await conn.idle_wait(folder, timeout=IDLE_WAIT_TIMEOUT)
                            if event == "new_mail":
                                await self._handle_new_mail(account, folder)
                            elif event in ("expunge", "fetch"):
                                # 邮件删除或状态变化，触发缓存同步
                                try:
                                    from services.mail_cache import sync_folder_to_cache
                                    await sync_folder_to_cache(account, folder)
                                    await self.refresh_clients(account.id, folder, user_uid=account.user_uid)
                                except Exception as e:
                                    logger.debug("文件夹 %s 缓存同步失败: %s", folder, e)

                    # 内层 while 循环：任一连接断开后，只重建断开的连接，不退出到外层
                    # 只有当所有连接都断开且重建失败时，才退出到外层（触发 token 刷新等）
                    while self._running:
                        if not idle_conns:
                            # 所有连接都断开，退出到外层重连（会重新 ensure_token）
                            break

                        tasks = {
                            folder: asyncio.create_task(_idle_folder(folder, conn))
                            for folder, conn in idle_conns.items()
                        }
                        # 等待任意一个任务结束（通常是连接断开）
                        await asyncio.wait(tasks.values(), return_when=asyncio.FIRST_COMPLETED)
                        # 取消其余任务并等待清理完成，避免 "Task was destroyed but it is pending" 错误
                        for task in tasks.values():
                            if not task.done():
                                task.cancel()
                        # gather + return_exceptions 确保所有任务异常被 retrieve，避免 asyncio 警告
                        await asyncio.gather(*tasks.values(), return_exceptions=True)

                        # 处理异常：文件夹不存在的永久移除，其他的标记断开
                        for folder, task in tasks.items():
                            if task.done() and not task.cancelled():
                                exc = task.exception()
                                if exc is None:
                                    continue
                                error_msg = str(exc)
                                if "doesn't exist" in error_msg or "不存在" in error_msg:
                                    logger.debug("账号 %s 文件夹 %s 不存在，移除监听", account.email, folder)
                                    idle_conns.pop(folder, None)
                                elif isinstance(exc, ConnectionError):
                                    # IDLE 连接断开或未建立，标记连接状态
                                    if folder in idle_conns:
                                        idle_conns[folder].connected = False
                                else:
                                    # 其他异常也标记为断开（如 SSL EOF）
                                    if folder in idle_conns:
                                        idle_conns[folder].connected = False

                        # 清理已断开的连接
                        for folder in list(idle_conns.keys()):
                            if not idle_conns[folder].connected:
                                idle_conns.pop(folder, None)

                        # 只重建断开的连接，复用存活的连接（核心优化点）
                        if self._running and idle_conns is not None:
                            await _ensure_idle_connections()

                        # 如果所有连接都断了且重建也失败，退出到外层
                        if not idle_conns:
                            break

                        # 短暂等待避免频繁重连
                        await asyncio.sleep(2)

                elif use_poll:
                    # Poll provider（iCloud）：通过 poll_manager 异步轮询，不阻塞线程池
                    if poll_conn is None or not poll_conn.connected:
                        from services.idle_manager import poll_manager
                        poll_config = self._get_idle_config(account, credentials)
                        poll_conn = await poll_manager.get_or_create(account.id, **poll_config)
                        consecutive_failures = 0
                        await self.notify_connection_status(account.id, "connected", account.user_uid)

                    # 动态获取文件夹列表
                    receiver = ProviderFactory.get_receiver(account.provider)
                    await receiver.connect(credentials)
                    try:
                        idle_folders = await self._get_idle_folders(receiver, account)
                    finally:
                        await receiver.disconnect()

                    # 为每个文件夹创建独立的 Poll 连接（并行 Poll，与 IDLE 方案 A 一致）
                    poll_conns = {}
                    for folder in idle_folders:
                        try:
                            conn = await poll_manager.get_or_create(account.id, **poll_config, folder=folder)
                            poll_conns[folder] = conn
                        except Exception as e:
                            logger.warning("账号 %s 文件夹 %s Poll 连接失败: %s", account.email, folder, e)

                    if poll_conns:
                        # 首次启动用 INFO，重连后用 DEBUG（与 IDLE 模式一致）
                        if consecutive_failures == 0:
                            logger.info("账号 %s 新邮件监听已启动 (Poll x%d): %s",
                                       account.email, len(poll_conns), list(poll_conns.keys()))
                        else:
                            logger.debug("账号 %s Poll 连接已就绪 (x%d)", account.email, len(poll_conns))
                        consecutive_failures = 0
                        await self.notify_connection_status(account.id, "connected", account.user_uid)

                    # 并行轮询所有文件夹
                    async def _poll_folder(folder: str, conn):
                        """单个文件夹的 Poll 循环

                        正常运行时在 while 循环中持续轮询。
                        连接断开时直接抛出异常，由外层循环负责清理和重建连接。
                        """
                        while self._running:
                            event = await conn.poll_wait(folder, interval=10, timeout=300)
                            if event == "new_mail":
                                await self._handle_new_mail(account, folder)
                            elif event == "expunge":
                                # 邮件数量减少，触发缓存清理 + 前端刷新
                                try:
                                    from services.mail_cache import sync_folder_to_cache
                                    await sync_folder_to_cache(account, folder)
                                    await self.refresh_clients(account.id, folder, user_uid=account.user_uid)
                                except Exception as e:
                                    logger.debug("文件夹 %s 缓存同步失败: %s", folder, e)

                    while self._running:
                        tasks = {
                            folder: asyncio.create_task(_poll_folder(folder, conn))
                            for folder, conn in poll_conns.items()
                        }
                        done, _ = await asyncio.wait(tasks.values(), return_when=asyncio.FIRST_COMPLETED)
                        # 取消其余任务并等待清理完成，避免 "Task was destroyed but it is pending" 错误
                        # （与 IDLE 模式保持一致，旧代码缺少 gather 导致被 cancel 的任务未被 retrieve）
                        for task in tasks.values():
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(*tasks.values(), return_exceptions=True)
                        # 处理异常：文件夹不存在的永久移除，其他的标记断开
                        # 关键：必须检查 not task.cancelled()，否则被 cancel 的任务调用
                        # task.exception() 会抛出 CancelledError（继承 BaseException，
                        # 不会被外层 except Exception 捕获），导致整个 _idle_loop 退出，
                        # 触发指数退避重连，iCloud 恢复缓慢（日志显示曾间隔 1 小时 20 分钟）
                        for folder, task in tasks.items():
                            if task.done() and not task.cancelled():
                                exc = task.exception()
                                if exc is None:
                                    continue
                                error_msg = str(exc)
                                if "doesn't exist" in error_msg or "不存在" in error_msg:
                                    logger.debug("账号 %s 文件夹 %s 不存在，移除监听", account.email, folder)
                                    del poll_conns[folder]
                                elif isinstance(exc, ConnectionError):
                                    # Poll 连接断开，标记连接状态
                                    if folder in poll_conns:
                                        poll_conns[folder].connected = False
                                else:
                                    # 其他异常也标记为断开
                                    if folder in poll_conns:
                                        poll_conns[folder].connected = False
                        # 清理断开的连接
                        for folder in list(poll_conns.keys()):
                            if not poll_conns[folder].connected:
                                del poll_conns[folder]
                        if not poll_conns:
                            break
                        # 有部分连接存活时短暂等待后重建断开的连接
                        await asyncio.sleep(5)

                else:
                    # 网易：使用 imaplib 的 NOOP/STATUS 轮询（需要 IMAP ID 命令）
                    # 网易使用同步 imaplib，单连接不能并发操作，因此保持串行但缩短轮询周期
                    if receiver is None:
                        receiver = ProviderFactory.get_receiver(account.provider)
                        await receiver.connect(credentials)
                        consecutive_failures = 0
                        await self.notify_connection_status(account.id, "connected", account.user_uid)

                    idle_folders = await self._get_idle_folders(receiver, account)
                    # 首次启动用 INFO，重连后用 DEBUG（与 IDLE/Poll 模式一致）
                    if consecutive_failures == 0:
                        logger.info("账号 %s 新邮件监听已启动 (NOOP x%d): %s",
                                   account.email, len(idle_folders), idle_folders)
                    else:
                        logger.debug("账号 %s NOOP 连接已就绪 (x%d)", account.email, len(idle_folders))

                    while self._running:
                        for folder in idle_folders:
                            if not self._running:
                                break
                            # 网易单连接串行轮询，使用短超时（10秒）快速轮转所有文件夹
                            event = await receiver.idle_wait(folder, 10)
                            if event == "new_mail":
                                await self._handle_new_mail(account, folder)
                            elif event == "expunge":
                                # 邮件数量减少，触发缓存清理 + 前端刷新
                                try:
                                    from services.mail_cache import sync_folder_to_cache
                                    await sync_folder_to_cache(account, folder)
                                    await self.refresh_clients(account.id, folder, user_uid=account.user_uid)
                                except Exception as e:
                                    logger.debug("文件夹 %s 缓存同步失败: %s", folder, e)
                            else:
                                await self._check_receiver_alive(receiver)

            except asyncio.CancelledError:
                await self.notify_connection_status(account.id, "disconnected", account.user_uid)
                break
            except Exception as e:
                # 连接异常，清理连接对象，下次循环重建
                logger.error("账号 %s 监听异常: %s", account.email, e)
                if use_idle:
                    # 清理该账号的所有 IDLE 连接
                    from services.idle_manager import idle_manager
                    await idle_manager.remove(account.id)
                elif use_poll:
                    # 清理该账号的所有 Poll 连接（与 IDLE 模式保持一致）
                    # 旧代码只清理 poll_conn，poll_conns 中的其他连接会泄漏
                    from services.idle_manager import poll_manager
                    await poll_manager.remove(account.id)
                    poll_conn = None
                else:
                    if receiver:
                        try:
                            await receiver.disconnect()
                        except Exception as ex:
                            logger.debug("断开 receiver 连接失败: %s", ex)
                    receiver = None
                    consecutive_failures += 1
                await self.notify_connection_status(account.id, "error", account.user_uid, error=str(e))

                # 连续失败超过 20 次（约 2 小时），进入长休眠 30 分钟
                # 说明不是瞬态问题（如 Token 完全失效、账号被锁定等），避免无意义重试
                if consecutive_failures >= 20:
                    long_sleep = 1800  # 30 分钟
                    logger.warning(
                        "账号 %s 连续失败 %d 次，进入长休眠 %d 分钟（可能是账号被锁定或需要重新授权）",
                        account.email, consecutive_failures, long_sleep // 60
                    )
                    try:
                        await asyncio.sleep(long_sleep)
                    except asyncio.CancelledError:
                        break
                    consecutive_failures = 0  # 重置计数，给一次机会
                    continue

                # 判断是否为永久 token 错误（ensure_token 失败时直接抛出 TokenRefreshError）
                # 或 IMAP 认证失败（XOAUTH2 被拒通常意味着 access_token 无效）
                from services.token import TokenRefreshError
                error_lower = str(e).lower()
                is_permanent_token_error = isinstance(e, TokenRefreshError) and e.is_permanent
                is_imap_auth_failed = "authenticationfailed" in error_lower or "invalid credentials" in error_lower

                if is_permanent_token_error or is_imap_auth_failed:
                    if is_imap_auth_failed and not is_permanent_token_error:
                        logger.error("账号 %s IMAP 认证失败（token 可能已失效），需重新授权", account.email)
                    else:
                        logger.error("账号 %s token 失效，需重新授权", account.email)
                    self.reauth_account_ids.add(account.id)
                    await self.notify_connection_status(
                        account.id, "reauth_needed", account.user_uid,
                        error=str(e),
                    )
                    # 必须在 return 前清理 idle_tasks，否则 add_account 会认为旧任务还在运行
                    self.idle_tasks.pop(account.id, None)
                    return  # 停止重试

                # 判断是否为 "authenticated but not connected" 状态错乱
                # 这种情况下必须强制断开重连，简单重试无效
                is_auth_not_connected = "authenticated but not connected" in str(e).lower()

                if is_auth_not_connected:
                    # 强制断开重连：不等待，直接进入下一次循环重建连接
                    logger.warning("账号 %s 状态错乱（authenticated but not connected），强制重连", account.email)
                else:
                    # 已知的瞬态错误（连接断开、超时）视为正常重连
                    is_transient = any(kw in str(e).lower() for kw in [
                        "eof", "timed out", "socket error", "连接已断开",
                        "broken pipe", "connection reset", "errno"
                    ])
                    # 空异常消息也视为瞬态（连接被静默关闭）
                    if not str(e).strip():
                        is_transient = True

                    if consecutive_failures <= 3:
                        # 前 3 次：瞬态错误用 INFO（正常重连），非瞬态用 ERROR
                        if is_transient:
                            logger.info("账号 %s 重连中（第 %d 次）: %s", account.email, consecutive_failures, e or "连接断开")
                        else:
                            logger.error("账号 %s 监听异常（连续第 %d 次）: %s", account.email, consecutive_failures, e)
                    else:
                        # 第 4 次起：只打印一次，避免日志刷屏
                        if consecutive_failures == 4:
                            logger.info("账号 %s 持续重连中，后续日志将抑制（每 10 次打印一次）", account.email)
                        elif consecutive_failures % 10 == 0:
                            logger.info("账号 %s 重连中（连续第 %d 次）", account.email, consecutive_failures)

                    # 计算指数退避延迟：前3次快速重试5秒，之后指数退避，上限5分钟
                    if consecutive_failures <= self.INITIAL_FAST_RETRIES:
                        delay = self.INITIAL_RECONNECT_DELAY
                    else:
                        # 指数退避：5 * 2^(n-1)，上限 MAX_RECONNECT_DELAY
                        # 第4次=10s, 第5次=20s, 第6次=40s, 第7次=80s, 第8次=160s, 第9次+=300s
                        delay = min(
                            self.INITIAL_RECONNECT_DELAY * (2 ** (consecutive_failures - 1)),
                            self.MAX_RECONNECT_DELAY
                        )

                    # "authenticated but not connected" 状态错乱：强制断开旧连接
                    if is_auth_not_connected and not use_idle and receiver:
                        logger.warning("账号 %s 检测到认证状态错乱，强制断开旧连接", account.email)
                        try:
                            await receiver.disconnect()
                        except Exception as e:
                            logger.debug("强制断开旧连接失败: %s", e)
                        receiver = None

                    try:
                        await asyncio.sleep(delay)
                    except asyncio.CancelledError:
                        break
            finally:
                # NOOP provider（网易）：断开 receiver
                if not use_idle and not use_poll and receiver:
                    try:
                        await receiver.disconnect()
                    except Exception as e:
                        logger.debug("断开 receiver 连接失败: %s", e)

        # 清理任务记录
        self.idle_tasks.pop(account.id, None)
        logger.debug("账号 %s 新邮件监听已停止", account.email)

    async def _get_idle_folders(self, receiver, account) -> list:
        """获取需要 IDLE 监听的文件夹列表

        优先监听 INBOX，然后是已发送、草稿箱、垃圾邮件、已删除等核心文件夹。
        不同邮箱平台的文件夹 IMAP 路径不同，通过 fetch_folders 获取实际路径。
        网易邮箱的路径是 Modified UTF-7 编码（如 &XfJT0ZAB-），无法通过英文关键词匹配，
        因此同时匹配 f.name（中文显示名）。
        """
        try:
            folders = await receiver.fetch_folders()
            # 英文路径关键词（匹配 IMAP path）
            sent_path_kw = {"sent", "sent messages", "sent items"}
            draft_path_kw = {"drafts", "draft"}
            junk_path_kw = {"junk", "spam", "junk email"}
            trash_path_kw = {"trash", "deleted", "deleted items", "deleted messages"}
            # 中文显示名（匹配 Folder.name，解决网易 Modified UTF-7 路径问题）
            sent_name_kw = {"已发送"}
            draft_name_kw = {"草稿箱"}
            junk_name_kw = {"垃圾邮件"}
            trash_name_kw = {"已删除"}

            idle_folders = ["INBOX"]  # INBOX 始终监听

            for f in folders:
                path_lower = f.path.lower().strip()
                name = f.name or ""
                if path_lower == "inbox":
                    continue  # 已添加
                # 匹配英文路径关键词或中文显示名
                path_match = any(kw in path_lower for kw in sent_path_kw | draft_path_kw | junk_path_kw | trash_path_kw)
                name_match = name in (sent_name_kw | draft_name_kw | junk_name_kw | trash_name_kw)
                if path_match or name_match:
                    idle_folders.append(f.path)

            return idle_folders
        except Exception as e:
            logger.warning("获取 IDLE 文件夹列表失败，仅监听 INBOX: %s", e)
            return ["INBOX"]

    async def _get_idle_folders_from_config(self, account) -> list:
        """获取监听文件夹列表（不需要 receiver 连接）

        各 Provider 的标准文件夹路径：
        - Gmail: 使用标签系统，IMAP 路径为 [Gmail]/xxx
        - Outlook: Sent Items, Drafts, Junk Email, Deleted Items
        - QQ: Sent Messages, Drafts, Junk, Deleted Messages
        - iCloud: Sent Messages, Drafts, Junk, Deleted Messages
        """
        folder_map = {
            "gmail": ["INBOX", "[Gmail]/Sent Mail", "[Gmail]/Drafts", "[Gmail]/Spam", "[Gmail]/Trash"],
            "outlook": ["INBOX", "Sent", "Sent Items", "Drafts", "Junk", "Junk Email", "Deleted", "Deleted Items"],
            "qq": ["INBOX", "Sent Messages", "Drafts", "Junk", "Deleted Messages"],
            "icloud": ["INBOX", "Sent Messages", "Drafts", "Junk", "Deleted Messages"],
        }
        return folder_map.get(account.provider, ["INBOX"])

    async def _check_receiver_alive(self, receiver):
        """检查 IMAP 连接是否仍可用；不可用时抛异常，让外层循环重连。

        仅用于网易（需要 IMAP ID 命令，使用 imaplib receiver）。
        """
        await receiver.idle_wait("INBOX", 1)


# 全局同步服务实例
sync_service = MailSyncService()
