"""IDLE 连接管理器 - 使用 aioimaplib 管理 IMAP IDLE 长连接

每个账号一个 IDLE 连接，独立于 imaplib 操作连接。
使用 aioimaplib 的原生异步 IDLE API（idle_start/wait_server_push/idle_done），
避免手写 socket 命令导致的 timed out object 等问题。

IPv4 强制：子类化 aioimaplib.IMAP4_SSL，覆盖 create_client 传入 family=AF_INET。
"""

import asyncio
import socket
import ssl
from typing import Optional, Callable

import aioimaplib

from utils.logger import get_logger

logger = get_logger("idle")

# ==================== 超时/轮询常量 ====================
# IMAP 连接超时（秒）：TCP + SSL 握手 + 服务端 greeting
IMAP_CONNECT_TIMEOUT = 30
# IDLE 等待超时（秒）：单次 IDLE 的最大等待时间，超时后正常重新 IDLE
# 设为 120s 主动续期，避免 NAT/防火墙清理空闲连接（一般 2-5 分钟无流量即断开）
IDLE_WAIT_TIMEOUT = 120
# Poll 轮询间隔（秒）：STATUS 命令检查邮件数量变化的频率
POLL_INTERVAL = 60
# Poll 等待超时（秒）：单次轮询的最大等待时间
POLL_TIMEOUT = 300


class IPv4IMAP4_SSL(aioimaplib.IMAP4_SSL):
    """强制使用 IPv4 连接的 aioimaplib IMAP4_SSL 子类

    覆盖 create_client 方法，在 asyncio.create_connection 中传入 family=socket.AF_INET，
    强制使用 IPv4 地址连接。保留原始主机名用于 SSL 证书验证。

    TCP keepalive 不在这里设置（预创建 socket 在 Windows 上会导致 WinError 10057），
    而是在连接建立后通过 transport.get_extra_info('socket') 获取底层 socket 设置。
    见 IdleConnection.connect() 中的 _apply_tcp_keepalive 调用。
    """

    def create_client(self, host: str, port: int, loop: asyncio.AbstractEventLoop,
                      conn_lost_cb: Callable = None, ssl_context: ssl.SSLContext = None) -> None:
        if ssl_context is None:
            ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        local_loop = loop if loop is not None else asyncio.get_running_loop()
        self.protocol = aioimaplib.IMAP4ClientProtocol(local_loop, conn_lost_cb)
        self._client_task = local_loop.create_task(
            local_loop.create_connection(
                lambda: self.protocol, host, port,
                ssl=ssl_context,
                family=socket.AF_INET,  # 强制 IPv4
            )
        )
        # 关键修复：给 _client_task 添加 done callback，retrieve 异常
        # SSL 握手失败（如 ConnectionResetError）时 Task 会带异常完成，
        # 若不 retrieve，Task 被 GC 回收时 asyncio 会报 "Task exception was never retrieved"
        def _on_client_task_done(task: asyncio.Task):
            if task.cancelled():
                return
            exc = task.exception()
            if exc:
                # 记录连接建立失败的异常，避免 asyncio "Task exception was never retrieved" 警告
                logger.debug("IMAP 连接建立任务结束（异常已记录）: %s", exc)
        self._client_task.add_done_callback(_on_client_task_done)


def _apply_tcp_keepalive(sock: socket.socket) -> None:
    """为已连接的 socket 开启 TCP keepalive，防止 NAT/防火墙清理空闲连接

    必须在连接建立后调用（通过 transport.get_extra_info('socket') 获取），
    不能在连接前调用（Windows 上预创建 socket 会导致 WinError 10057）。

    参数选择（保守值，兼容 Windows/Linux/macOS）：
    - SO_KEEPALIVE=1：开启 keepalive
    - TCP_KEEPIDLE=60：连接空闲 60 秒后开始探测（Linux/macOS）
    - TCP_KEEPINTVL=30：每 30 秒探测一次
    - TCP_KEEPCNT=3：3 次探测失败才认为断开

    Windows 不支持 TCP_KEEPIDLE/TCP_KEEPINTVL/TCP_KEEPCNT 常量，
    但支持 SO_KEEPALIVE（使用系统默认参数），所以用 try-except 兼容。
    """
    if sock is None:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except (OSError, AttributeError):
        return
    # 以下三个选项是平台相关的，Windows 不支持，用 getattr 兜底
    if hasattr(socket, "TCP_KEEPIDLE"):
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
        except (OSError, AttributeError) as e:
            logger.debug("设置 TCP_KEEPIDLE 失败: %s", e)
    if hasattr(socket, "TCP_KEEPINTVL"):
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 30)
        except (OSError, AttributeError) as e:
            logger.debug("设置 TCP_KEEPINTVL 失败: %s", e)
    if hasattr(socket, "TCP_KEEPCNT"):
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        except (OSError, AttributeError) as e:
            logger.debug("设置 TCP_KEEPCNT 失败: %s", e)


class IdleConnection:
    """单个账号的 IDLE 连接

    封装 aioimaplib 的 IDLE 连接，支持：
    - IPv4 强制（子类化 IMAP4_SSL，传入 family=AF_INET）
    - 自定义 SSL context（Outlook 需要 TLS 1.2 + OP_IGNORE_UNEXPECTED_EOF）
    - XOAUTH2 认证（Gmail、Outlook）和 LOGIN 认证（QQ）
    - 统一重连保护（catch Exception + 自动重连）
    """

    def __init__(self, account_id: str, host: str, port: int, email: str,
                 auth_type: str, auth_credential: str,
                 ssl_context: Optional[ssl.SSLContext] = None):
        self.account_id = account_id
        self.host = host
        self.port = port
        self.email = email
        self.auth_type = auth_type
        self.auth_credential = auth_credential
        self.ssl_context = ssl_context
        self.client: Optional[aioimaplib.IMAP4_SSL] = None
        self.connected = False
        self._idle_active = False  # 标记当前是否处于 IDLE 状态，防止并发操作
        # 记录 aioimaplib 内部创建的 IMAP4.idle() 任务引用
        # 用于 disconnect 时精准取消，避免 asyncio.all_tasks() 全局扫描误杀其他连接的任务
        self._idle_tasks: set = set()

    async def connect(self):
        """建立 IMAP 连接并登录"""
        ssl_ctx = self.ssl_context or ssl.create_default_context(ssl.Purpose.SERVER_AUTH)

        # 使用 IPv4 强制子类，保留主机名用于 SSL 证书验证
        self.client = IPv4IMAP4_SSL(
            host=self.host,
            port=self.port,
            timeout=IMAP_CONNECT_TIMEOUT,
            ssl_context=ssl_ctx,
        )

        await self.client.wait_hello_from_server()

        if self.auth_type == "xoauth2":
            await self.client.xoauth2(self.email, self.auth_credential)
        else:
            await self.client.login(self.email, self.auth_credential)

        # 连接建立后开启 TCP keepalive，防止 NAT/防火墙清理空闲连接
        # 通过 transport 获取底层 socket（不能预创建 socket，Windows 会报 10057）
        try:
            transport = getattr(self.client, 'transport', None)
            if transport is None:
                # aioimaplib 的 protocol 可能有 transport 属性
                protocol = getattr(self.client, 'protocol', None)
                if protocol:
                    transport = getattr(protocol, 'transport', None)
            if transport:
                sock = transport.get_extra_info('socket')
                _apply_tcp_keepalive(sock)
        except Exception as e:
            logger.debug("设置 TCP keepalive 失败（不影响功能）: %s", e)

        self.connected = True
        logger.info("IDLE 连接已建立: %s", self.email)

    async def disconnect(self):
        """断开连接

        关键修复：
        1. 不再用 asyncio.all_tasks() 全局扫描（会误杀其他账号的 IDLE 任务）
        2. 只取消 self._idle_tasks 中记录的本连接任务
        3. cancel 后用 gather 等待任务真正完成，避免 "Task was destroyed but it is pending!" 警告
        4. 先取消任务再 await idle_done，确保 CancelledError 不会跳过任务清理
        """
        if self.client:
            try:
                # 1. 先取消本连接记录的 idle 任务（不需要 await，不会被 CancelledError 中断）
                #    aioimaplib 的 idle_done() 不会取消内部 IMAP4.idle() 任务，必须显式取消
                tasks_to_cancel = [t for t in self._idle_tasks if not t.done()]
                for task in tasks_to_cancel:
                    task.cancel()
                # 2. 尝试优雅退出 IDLE（发送 DONE 给 IMAP 服务器）
                if self._idle_active:
                    try:
                        await self.client.idle_done()
                    except asyncio.CancelledError:
                        pass  # 收到取消信号，继续清理
                    except Exception as e:
                        logger.debug("发送 IDLE DONE 失败: %s", e)
                    self._idle_active = False
                # 3. 等待所有被取消的任务真正完成，确保不会留下孤儿 task
                if tasks_to_cancel:
                    try:
                        await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
                    except asyncio.CancelledError:
                        pass  # 收到取消信号，继续清理
                self._idle_tasks.clear()
                # 4. 取消连接建立任务
                internal_task = getattr(self.client, '_client_task', None)
                if internal_task and not internal_task.done():
                    internal_task.cancel()
                    try:
                        await internal_task
                    except asyncio.CancelledError:
                        pass  # 预期的取消信号
                    except Exception as e:
                        logger.debug("清理内部任务时忽略异常: %s", e)
                # 5. 登出
                try:
                    await self.client.logout()
                except asyncio.CancelledError:
                    pass  # 收到取消信号，跳过登出
                except Exception as e:
                    logger.debug("logout 失败: %s", e)
            except Exception as e:
                logger.debug("IDLE 连接清理失败: %s", e)
            self.client = None
            self.connected = False

    async def idle_wait(self, folder: str, timeout: int = IDLE_WAIT_TIMEOUT) -> str:
        """在指定文件夹上等待邮件变化

        使用 aioimaplib 的 idle_start/wait_server_push/idle_done 三步 API。
        内置超时处理，不会出现 timed out object。

        关键修复：记录 aioimaplib 内部创建的 IMAP4.idle() 任务到 self._idle_tasks，
        供 disconnect 时精准取消（不再用 asyncio.all_tasks() 全局扫描）。

        返回值：
        - "new_mail": 检测到新邮件（EXISTS 事件）
        - "expunge": 检测到邮件删除（EXPUNGE 事件）
        - "fetch": 检测到邮件状态变化（FETCH 事件，如标记已读）
        - "timeout": 超时，无事件
        """
        if not self.connected or not self.client:
            raise ConnectionError("IDLE 连接未建立")

        # 选择文件夹，文件夹名包含空格时需要加引号
        quoted_folder = f'"{folder}"' if ' ' in folder else folder
        try:
            select_result = await self.client.select(quoted_folder)
        except Exception as e:
            # 连接状态异常（如 NONAUTH），标记为断开
            self.connected = False
            raise ConnectionError(f"IDLE 连接已断开: {e}")

        if select_result.result != 'OK':
            # 连接状态异常，标记为断开
            self.connected = False
            raise ConnectionError(f"选择文件夹 {folder} 失败: {select_result}")

        # 记录 idle_start 前的任务集，用于对比找出 aioimaplib 内部新建的 idle 任务
        # aioimaplib 的 idle_start() 通过 ensure_future() 创建 IMAP4.idle() 任务但不保存外部引用，
        # 我们通过前后对比 all_tasks() 来捕获它，保存到 self._idle_tasks 供 disconnect 精准取消
        tasks_before = set(asyncio.all_tasks())
        await self.client.idle_start()
        tasks_after = set(asyncio.all_tasks())
        # 新增的任务就是 aioimaplib 内部创建的 IMAP4.idle() 任务
        new_tasks = tasks_after - tasks_before
        self._idle_tasks.update(new_tasks)
        self._idle_active = True

        try:
            response = await self.client.wait_server_push(timeout=timeout)
            if response:
                # response 是 list[bytes]，每个元素是一行 IMAP 响应
                for line in response:
                    line_str = line.decode('utf-8', errors='ignore') if isinstance(line, bytes) else str(line)
                    if 'EXISTS' in line_str:
                        logger.info("IDLE 检测到新邮件: folder=%s, response=%s", folder, line_str)
                        return "new_mail"
                    if 'EXPUNGE' in line_str:
                        logger.info("IDLE 检测到邮件删除: folder=%s, response=%s", folder, line_str)
                        return "expunge"
                    if 'FETCH' in line_str and 'FLAGS' in line_str:
                        return "fetch"
            return "timeout"
        except asyncio.TimeoutError:
            # IDLE 超时是正常行为，不是错误
            return "timeout"
        finally:
            self._idle_active = False
            # 关键修复：先取消所有 IDLE 任务（不需要 await，不会被 CancelledError 中断）
            # aioimaplib 的 idle_done() 只是发送 DONE 命令，不会取消内部的 IMAP4.idle() 任务
            # 这些任务需要被显式取消，否则会一直 pending，在 GC 回收时触发大量警告
            pending = [t for t in self._idle_tasks if not t.done()]
            for task in pending:
                task.cancel()
            # 然后尝试发送 idle_done（可能失败，不影响任务清理）
            try:
                await self.client.idle_done()
            except asyncio.CancelledError:
                # 收到取消信号，继续执行清理（不重新抛出，确保清理逻辑完整执行）
                pass
            except Exception as e:
                logger.debug("IDLE done 失败（连接可能已断）: %s", e)
            # 等待被取消的任务真正完成，避免孤儿任务
            if pending:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*pending, return_exceptions=True),
                        timeout=5.0
                    )
                except asyncio.CancelledError:
                    pass  # 收到取消信号，继续清理
                except asyncio.TimeoutError:
                    logger.debug("等待 idle 任务完成超时，强制清理（%d 个任务）", len(pending))
            self._idle_tasks.clear()

    async def get_folder_count(self, folder: str) -> int:
        """复用已有 IDLE 连接获取文件夹的邮件数量

        使用 IMAP STATUS 命令查询 MESSAGES 数量。
        如果当前正在 IDLE 状态，返回 -1 跳过本次校验（避免 IMAP 协议错误）。

        返回邮件数量，连接异常或正在 IDLE 时返回 -1。
        """
        import re as _re
        try:
            if not self.connected or not self.client:
                return -1
            # 正在 IDLE 状态时不能执行其他命令，跳过本次校验
            if self._idle_active:
                return -1
            quoted_folder = f'"{folder}"' if ' ' in folder else folder
            response = await self.client.status(quoted_folder, '(MESSAGES)')
            if response and response.lines:
                for line in response.lines:
                    line_str = line.decode('utf-8', errors='ignore') if isinstance(line, bytes) else str(line)
                    match = _re.search(r'MESSAGES\s+(\d+)', line_str)
                    if match:
                        return int(match.group(1))
            return -1
        except Exception as e:
            logger.debug("IDLE STATUS 查询失败: folder=%s, error=%s", folder, e)
            return -1

    async def reconnect(self):
        """断开并重新连接"""
        await self.disconnect()
        await self.connect()


class IdleManager:
    """IDLE 连接管理器 - 管理所有账号的 IDLE 连接

    支持每个账号多个连接（每个文件夹一个 IDLE 连接），
    5 个并行 IDLE 连接，所有文件夹实时检测。
    """

    def __init__(self):
        # key 为 (account_id, folder)，支持每个账号多个文件夹的 IDLE 连接
        self.connections: dict[tuple[str, str], IdleConnection] = {}

    def get_connection(self, account_id: str, folder: str = None) -> Optional[IdleConnection]:
        """获取已有的 IDLE 连接（不创建新的）

        如果指定 folder，返回该文件夹的连接；
        如果不指定 folder，返回该账号任意一个已连接的连接（用于 STATUS 查询）。
        """
        if folder:
            conn = self.connections.get((account_id, folder))
            return conn if conn and conn.connected else None
        # 返回该账号任意一个已连接
        for (aid, _), conn in self.connections.items():
            if aid == account_id and conn.connected:
                return conn
        return None

    def get_account_connections(self, account_id: str) -> list[tuple[str, IdleConnection]]:
        """获取账号的所有已连接的 IDLE 连接"""
        return [
            (folder, conn)
            for (aid, folder), conn in self.connections.items()
            if aid == account_id and conn.connected
        ]

    async def get_or_create(self, account_id: str, host: str, port: int,
                           email: str, auth_type: str, auth_credential: str,
                           ssl_context: Optional[ssl.SSLContext] = None,
                           folder: str = "INBOX") -> IdleConnection:
        """获取或创建指定文件夹的 IDLE 连接

        如果已有连接且仍存活则复用；如果已断开则先清理再创建新连接。
        """
        key = (account_id, folder)
        if key in self.connections:
            conn = self.connections[key]
            if conn.connected:
                return conn
            # 旧连接已断开，清理后重新创建
            del self.connections[key]

        conn = IdleConnection(
            account_id, host, port, email,
            auth_type, auth_credential, ssl_context,
        )
        await conn.connect()
        self.connections[key] = conn
        return conn

    async def remove(self, account_id: str):
        """移除并断开账号的所有 IDLE 连接"""
        keys_to_remove = [k for k in self.connections if k[0] == account_id]
        for key in keys_to_remove:
            conn = self.connections.pop(key, None)
            if conn:
                await conn.disconnect()

    async def close_all(self):
        """关闭所有 IDLE 连接"""
        for key in list(self.connections.keys()):
            conn = self.connections.pop(key, None)
            if conn:
                await conn.disconnect()


class PollConnection:
    """NOOP/STATUS 轮询连接（网易/iCloud 使用）

    使用 aioimaplib 的原生异步 STATUS 命令检测邮件数量变化，
    不阻塞线程池（替代 imaplib + asyncio.to_thread + time.sleep）。
    """

    def __init__(self, account_id: str, host: str, port: int, email: str,
                 auth_type: str, auth_credential: str,
                 ssl_context: Optional[ssl.SSLContext] = None):
        self.account_id = account_id
        self.host = host
        self.port = port
        self.email = email
        self.auth_type = auth_type
        self.auth_credential = auth_credential
        self.ssl_context = ssl_context
        self.client: Optional[aioimaplib.IMAP4_SSL] = None
        self.connected = False

    async def connect(self):
        """建立 IMAP 连接并登录"""
        ssl_ctx = self.ssl_context or ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        self.client = IPv4IMAP4_SSL(
            host=self.host, port=self.port, timeout=IMAP_CONNECT_TIMEOUT, ssl_context=ssl_ctx,
        )
        await self.client.wait_hello_from_server()
        if self.auth_type == "xoauth2":
            await self.client.xoauth2(self.email, self.auth_credential)
        else:
            await self.client.login(self.email, self.auth_credential)

        # 连接建立后开启 TCP keepalive（与 IdleConnection 一致）
        try:
            transport = getattr(self.client, 'transport', None)
            if transport is None:
                protocol = getattr(self.client, 'protocol', None)
                if protocol:
                    transport = getattr(protocol, 'transport', None)
            if transport:
                sock = transport.get_extra_info('socket')
                _apply_tcp_keepalive(sock)
        except Exception as e:
            logger.debug("设置 TCP keepalive 失败（不影响功能）: %s", e)

        self.connected = True
        logger.info("轮询连接已建立: %s", self.email)

    async def disconnect(self):
        """断开连接

        与 IdleConnection.disconnect 保持一致：
        cancel 后用 await 等待任务真正完成，避免 "Task was destroyed but it is pending!" 警告
        正确处理 CancelledError，确保清理逻辑完整执行
        """
        if self.client:
            try:
                # 取消连接建立任务并等待完成，避免 GC 警告
                internal_task = getattr(self.client, '_client_task', None)
                if internal_task and not internal_task.done():
                    internal_task.cancel()
                    try:
                        await internal_task
                    except asyncio.CancelledError:
                        pass  # 预期的取消信号
                    except Exception as e:
                        logger.debug("清理内部任务时忽略异常: %s", e)
                try:
                    await self.client.logout()
                except asyncio.CancelledError:
                    pass  # 收到取消信号，跳过登出
                except Exception as e:
                    logger.debug("logout 失败: %s", e)
            except Exception as e:
                logger.debug("Poll 连接清理失败: %s", e)
            self.client = None
            self.connected = False

    async def poll_wait(self, folder: str, interval: int = POLL_INTERVAL, timeout: int = POLL_TIMEOUT) -> str:
        """异步轮询检测邮件数量变化，不阻塞线程池

        每 interval 秒用 STATUS 命令检查邮件数量变化。
        返回值：
        - "new_mail": 数量增加（新邮件到达）
        - "expunge": 数量减少（邮件被删除/移动）
        - "timeout": 超时无变化
        """
        if not self.connected or not self.client:
            raise ConnectionError("轮询连接未建立")

        initial_count = await self._get_folder_count(folder)
        if initial_count < 0:
            raise ConnectionError("轮询连接已断开")

        elapsed = 0
        while elapsed < timeout:
            await asyncio.sleep(interval)
            elapsed += interval
            try:
                current_count = await self._get_folder_count(folder)
                if current_count < 0:
                    raise ConnectionError("轮询连接已断开")
                if current_count > initial_count:
                    logger.info("轮询检测到新邮件: folder=%s, %d -> %d", folder, initial_count, current_count)
                    return "new_mail"
                if current_count < initial_count:
                    logger.info("轮询检测到邮件减少: folder=%s, %d -> %d", folder, initial_count, current_count)
                    return "expunge"
                # 保持连接活跃
                await self.client.noop()
            except ConnectionError:
                raise
            except Exception as e:
                logger.warning("轮询异常: %s", e)
                raise ConnectionError(f"轮询异常: {e}") from e

        return "timeout"

    async def _get_folder_count(self, folder: str) -> int:
        """使用 aioimaplib 的 STATUS 命令获取邮件数量"""
        import re as _re
        # 快速失败：连接已断开时直接返回 -1，不再尝试 STATUS 命令
        # 关键修复：iCloud 有 5 个文件夹并行 Poll，当第一个文件夹发现连接断开并设置
        # self.connected=False 后，其他文件夹的并发任务下次调用本方法时若不检查 self.connected，
        # 会继续尝试 self.client.status()，导致连续 5 次 "STATUS 命令失败" 警告日志
        if not self.connected or not self.client:
            return -1
        try:
            # 文件夹名包含空格时需要加引号（如 "Sent Messages"）
            quoted_folder = f'"{folder}"' if ' ' in folder else folder
            response = await self.client.status(quoted_folder, '(MESSAGES)')
            logger.debug("STATUS 响应: folder=%s, result=%s, lines=%s", folder,
                        response.result if response else None,
                        [l.decode('utf-8', errors='ignore') if isinstance(l, bytes) else str(l) for l in (response.lines or [])])
            if response and response.lines:
                for line in response.lines:
                    line_str = line.decode('utf-8', errors='ignore') if isinstance(line, bytes) else str(line)
                    match = _re.search(r'MESSAGES\s+(\d+)', line_str)
                    if match:
                        return int(match.group(1))
            logger.warning("STATUS 未解析到 MESSAGES: folder=%s, result=%s", folder, response.result if response else None)
            return -1
        except Exception as e:
            logger.warning("STATUS 命令失败: folder=%s, error=%r", folder, e)
            self.connected = False
            return -1


class PollManager:
    """轮询连接管理器 - 管理所有 Poll provider 的轮询连接

    支持每个账号多个连接（每个文件夹一个 Poll 连接），
    实现并行轮询，所有文件夹同时检测。
    """

    def __init__(self):
        # key 为 (account_id, folder)，支持每个账号多个文件夹的 Poll 连接
        self.connections: dict[tuple[str, str], PollConnection] = {}

    def get_connection(self, account_id: str, folder: str = None) -> Optional[PollConnection]:
        """获取已有的轮询连接（不创建新的）

        如果指定 folder，返回该文件夹的连接；
        如果不指定 folder，返回该账号任意一个已连接的连接。
        """
        if folder:
            conn = self.connections.get((account_id, folder))
            return conn if conn and conn.connected else None
        # 返回该账号任意一个已连接
        for (aid, _), conn in self.connections.items():
            if aid == account_id and conn.connected:
                return conn
        return None

    async def get_or_create(self, account_id: str, host: str, port: int,
                           email: str, auth_type: str, auth_credential: str,
                           ssl_context: Optional[ssl.SSLContext] = None,
                           folder: str = "INBOX") -> PollConnection:
        """获取或创建指定文件夹的轮询连接

        如果已有连接且仍存活则复用；如果已断开则先清理再创建新连接。
        """
        key = (account_id, folder)
        if key in self.connections:
            conn = self.connections[key]
            if conn.connected:
                return conn
            # 旧连接已断开，清理后重新创建
            del self.connections[key]

        conn = PollConnection(
            account_id, host, port, email,
            auth_type, auth_credential, ssl_context,
        )
        await conn.connect()
        self.connections[key] = conn
        return conn

    async def remove(self, account_id: str):
        """移除并断开账号的所有轮询连接"""
        keys_to_remove = [k for k in self.connections if k[0] == account_id]
        for key in keys_to_remove:
            conn = self.connections.pop(key, None)
            if conn:
                await conn.disconnect()

    async def close_all(self):
        """关闭所有轮询连接"""
        for key in list(self.connections.keys()):
            conn = self.connections.pop(key, None)
            if conn:
                await conn.disconnect()


# 全局 IDLE 管理器实例
idle_manager = IdleManager()
# 全局轮询管理器实例
poll_manager = PollManager()
