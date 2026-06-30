import asyncio
import imaplib
import re
from typing import List, Optional, Dict
from ..base import Credentials, Folder, Message, MessageList
from ..base_imap import BaseIMAPReceiver
from ..ipv4 import IPv4IMAP4_SSL
from .config import (
    IMAP_163_HOST, IMAP_163_PORT, IMAP_126_HOST, IMAP_126_PORT,
    IMAP_188_HOST, IMAP_188_PORT, IMAP_YEAH_HOST, IMAP_YEAH_PORT,
)
from utils.logger import get_logger

logger = get_logger("netease")


class NeteaseReceiver(BaseIMAPReceiver):
    """网易邮箱IMAP接收器（支持163和126邮箱）"""

    TIMEOUT = 30  # 单个 socket 操作超时30秒

    def __init__(self):
        self.conn: Optional[IPv4IMAP4_SSL] = None
        self.email_addr: str = ""
        self._auth_code: str = ""  # 保存授权码，用于自动重连
        self._folder_counts: dict = {}  # 每个文件夹的上次已知邮件数，用于跨轮询周期检测新邮件

    # ---- _conn 属性别名，使基类方法兼容 self.conn 命名 ----

    @property
    def _conn(self):
        """基类使用 self._conn 访问 IMAP 连接，网易使用 self.conn，通过属性别名统一"""
        return self.conn

    @_conn.setter
    def _conn(self, value):
        self.conn = value

    def _get_imap_host(self, email_addr: str) -> str:
        """根据邮箱后缀返回对应的IMAP服务器地址"""
        suffix = email_addr.split("@")[-1].lower() if "@" in email_addr else ""
        host_map = {
            "126.com": IMAP_126_HOST,
            "188.com": IMAP_188_HOST,
            "yeah.net": IMAP_YEAH_HOST,
        }
        return host_map.get(suffix, IMAP_163_HOST)

    def _get_imap_port(self, email_addr: str) -> int:
        """根据邮箱后缀返回对应的IMAP端口"""
        suffix = email_addr.split("@")[-1].lower() if "@" in email_addr else ""
        port_map = {
            "126.com": IMAP_126_PORT,
            "188.com": IMAP_188_PORT,
            "yeah.net": IMAP_YEAH_PORT,
        }
        return port_map.get(suffix, IMAP_163_PORT)

    async def connect(self, credentials: Credentials) -> None:
        """连接到网易邮箱IMAP服务器"""
        self.email_addr = credentials.extra.get("email", "")
        self._auth_code = credentials.access_token  # 网易邮箱使用授权码

        try:
            self._conn = await asyncio.to_thread(self._connect_imap, self.email_addr, self._auth_code)
        except Exception as e:
            self._conn = None
            raise ConnectionError(f"网易邮箱连接失败: {str(e)}")

    def _connect_imap(self, email_addr: str, auth_code: str) -> IPv4IMAP4_SSL:
        """同步建立 IMAP 连接（在线程池中运行，使用 IPv4 强制子类）

        关键步骤：login 成功后必须发送 IMAP ID 命令（RFC 2971），
        否则网易服务器会拒绝所有 SELECT/EXAMINE 操作，返回 "Unsafe Login" 错误。
        """
        # 注册 IMAP ID 命令（RFC 2971）
        # 网易邮箱要求客户端在 login 后必须发送 ID 命令告知身份信息，
        # 否则任何 SELECT/EXAMINE 操作都会返回 "Unsafe Login" 错误。
        # Python 标准库 imaplib 不内置 ID 命令，需要手动注册。
        # 放在 _connect_imap 中而非模块级别，避免影响其他 provider 的全局状态
        imaplib.Commands['ID'] = ('AUTH',)

        host = self._get_imap_host(email_addr)
        port = self._get_imap_port(email_addr)
        conn = IPv4IMAP4_SSL(host, port, timeout=self.TIMEOUT)
        # 修复 P5: 登录或 ID 命令失败时关闭连接，防止 socket 泄漏
        try:
            conn.login(email_addr, auth_code)
            # 发送 IMAP ID 命令，告知网易服务器客户端身份信息
            # 这是网易邮箱的强制要求，不发送则所有文件夹操作都会被拒绝
            self._send_imap_id(conn, email_addr)
            return conn
        except Exception:
            # 登录或 ID 命令失败，关闭连接防止 socket 泄漏
            try:
                conn.logout()
            except Exception as e:
                logger.debug("登录失败后关闭连接失败: %s", e)
            raise

    @staticmethod
    def _send_imap_id(conn: imaplib.IMAP4_SSL, email_addr: str) -> None:
        """发送 IMAP ID 命令（RFC 2971）

        网易邮箱要求第三方客户端在 login 后发送 ID 命令，
        告知客户端名称、版本、联系方式等信息。
        如果不发送，服务器会返回 "Unsafe Login" 错误，
        拒绝所有 SELECT/EXAMINE/IDLE 操作。

        ID 命令格式: ID ("name" "FlyMail" "contact" "xxx@163.com" "version" "x.x.x" "vendor" "FlyMail")
        """
        from version import VERSION
        id_args = (
            "name", "FlyMail",
            "contact", email_addr,
            "version", VERSION,
            "vendor", "FlyMail",
        )
        # 构造 IMAP ID 命令参数: ("name" "FlyMail" "contact" "..." "version" "x.x.x" "vendor" "FlyMail")
        id_param = '("' + '" "'.join(id_args) + '")'
        try:
            status, response = conn._simple_command('ID', id_param)
        except Exception as e:
            # ID 命令失败是严重错误，网易邮箱会拒绝后续所有操作
            logger.error("ID 命令发送失败: %s", e)
            raise Exception(f"网易邮箱 ID 命令失败，后续操作可能被拒绝: {e}")

    async def fetch_folders(self) -> List[Folder]:
        """获取邮箱文件夹列表"""
        if not self._conn:
            raise Exception("未连接到邮箱服务器")

        try:
            return await asyncio.to_thread(self._fetch_folders_sync)
        except Exception as e:
            raise Exception(f"获取文件夹失败: {str(e)}")

    def _fetch_folders_sync(self) -> List[Folder]:
        """同步获取文件夹列表

        通过解析 IMAP LIST 返回的文件夹属性（\\Sent, \\Drafts, \\Trash, \\Junk）
        来识别核心文件夹，不再硬编码 Modified UTF-7 路径（不同账号的编码可能不同）。
        """
        status, folders = self._conn.list()
        if status != "OK":
            return []

        # IMAP 属性 → 显示名（按显示顺序）
        attr_display = {
            "\\Sent": "已发送",
            "\\Drafts": "草稿箱",
            "\\Junk": "垃圾邮件",
            "\\Trash": "已删除",
        }

        # 按顺序收集：INBOX + 属性匹配的文件夹（只保留5个核心分类）
        inbox = None
        attr_folders = {}  # attr -> Folder

        for folder_data in folders:
            if isinstance(folder_data, bytes):
                folder_str = folder_data.decode("utf-8", errors="ignore")
            else:
                folder_str = str(folder_data)

            # 解析属性和文件夹名
            # 格式: (\HasNoChildren \Sent) "/" "&XfJT0ZAB-"
            attrs = set()
            attr_match = re.match(r'\(([^)]*)\)', folder_str)
            if attr_match:
                attrs = set(attr_match.group(1).split())

            # 解析文件夹路径（最后一个引号内的内容）
            folder_name = None
            name_match = re.search(r'"([^"]+)"$', folder_str)
            if name_match:
                folder_name = name_match.group(1)
            else:
                parts = folder_str.split()
                if parts:
                    folder_name = parts[-1].strip('"')

            if not folder_name:
                continue

            # 识别文件夹类型
            if folder_name.upper() == "INBOX":
                inbox = Folder(name="收件箱", path=folder_name, unread_count=0, total_count=0)
                continue

            # 通过 IMAP 属性匹配核心文件夹
            matched_attr = None
            for attr in attr_display:
                if attr in attrs:
                    matched_attr = attr
                    break

            if matched_attr:
                attr_folders[matched_attr] = Folder(
                    name=attr_display[matched_attr],
                    path=folder_name,
                    unread_count=0,
                    total_count=0,
                )
            # 非核心文件夹（如"病毒邮件"等）直接忽略，与QQ/Gmail统一只显示5个核心分类

        # 按固定顺序组装结果：收件箱 → 已发送 → 草稿箱 → 垃圾邮件 → 已删除
        # 只返回5个核心文件夹，与QQ/Gmail统一，不显示"病毒邮件"等非核心分类
        # 不在 fetch_folders 中查询计数，改由 list_messages API 返回后更新
        result = []
        if inbox:
            result.append(inbox)
        for attr in ["\\Sent", "\\Drafts", "\\Junk", "\\Trash"]:
            if attr in attr_folders:
                result.append(attr_folders[attr])

        return result

    async def fetch_messages(self, folder: str = "INBOX", page: int = 1, page_size: int = 20) -> MessageList:
        """分页获取邮件列表"""
        if not self._conn:
            raise Exception("未连接到邮箱服务器")

        try:
            return await asyncio.to_thread(self._fetch_messages_sync, folder, page, page_size)
        except Exception as e:
            raise Exception(f"获取邮件失败: {str(e)}")

    def _fetch_messages_sync(self, folder: str, page: int, page_size: int) -> MessageList:
        """同步获取邮件列表（使用批量 UID FETCH，替代逐封 FETCH 提升性能）

        核心改动：
        1. 用 UID SEARCH 替代序列号 SEARCH，获取所有邮件的 UID 列表
        2. 用批量 UID FETCH 一次获取一页邮件的 FLAGS + 头部，替代逐封 FETCH
        3. 通过 _parse_batch_fetch_response 统一解析批量返回数据

        网易邮箱已读/未读状态判断：
        - BODY.PEEK 不会隐式设置 \\Seen 标志
        - FLAGS 中包含 \\Seen 表示已读，不包含表示未读
        """
        # SELECT 必须用 readonly=True，避免意外修改邮件状态
        status, data = self._conn.select(self._quote_mailbox(folder), readonly=True)
        if status != "OK":
            raise Exception(f"无法选择文件夹 {folder}: {data}")

        # 使用 UID SEARCH 替代序列号 SEARCH，获取所有邮件的 UID
        status, data = self._conn.uid('SEARCH', None, 'ALL')
        if status != "OK" or not data[0]:
            return MessageList(messages=[], total=0, unread_total=0, page=page, page_size=page_size)

        # 解析 UID 列表（UID SEARCH 返回空格分隔的 UID 字节串）
        all_uids = [int(uid) for uid in data[0].split()]
        total = len(all_uids)

        # 获取未读邮件总数（用于侧边栏计数）
        unread_total = 0
        try:
            s, u_data = self._conn.search(None, "UNSEEN")
            if s == "OK" and u_data[0]:
                unread_total = len(u_data[0].split())
        except Exception as e:
            logger.debug("获取未读邮件总数失败: %s", e)

        # 分页：取最新的一页（倒序，最新的在前面）
        # 注意：UID 通常递增，所以倒序排列后取分页范围
        start = max(0, total - page * page_size)
        end = max(0, total - (page - 1) * page_size)
        page_uids = list(reversed(all_uids[start:end]))

        if not page_uids:
            return MessageList(messages=[], total=total, unread_total=unread_total, page=page, page_size=page_size)

        # 批量 UID FETCH：用逗号拼接 UID 集合，一次请求获取整页邮件
        uid_set = ",".join(str(u) for u in page_uids)
        status, msg_data = self._conn.uid(
            'FETCH', uid_set,
            '(FLAGS BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE)])'
        )
        if status != "OK":
            return MessageList(messages=[], total=total, unread_total=unread_total, page=page, page_size=page_size)

        # 使用统一方法解析批量 FETCH 返回数据
        messages = self._parse_batch_fetch_response(msg_data, folder)
        # 批量 FETCH 不保证返回顺序，必须按 UID 降序排列（最新的在前）
        messages.sort(key=lambda m: m.uid, reverse=True)

        return MessageList(messages=messages, total=total, unread_total=unread_total, page=page, page_size=page_size)

    async def idle_wait(self, folder: str = "INBOX", timeout_seconds: int = 1740) -> str:
        """网易邮箱使用 NOOP/STATUS 检测邮件数量变化

        网易个人邮箱的 IDLE 能力存在套餐/服务端限制，不作为默认同步方式。
        NOOP 模式下使用 60 秒短超时，让 _idle_loop 快速轮转文件夹。

        使用 _folder_counts 维护每个文件夹的上次已知邮件数，
        避免新邮件在两次 idle_wait 调用之间到达时被错过。

        返回值：
        - "new_mail": 数量增加
        - "expunge": 数量减少
        - "timeout": 超时无变化
        """
        if not self._conn:
            raise Exception("未连接到邮箱服务器")
        actual_timeout = min(timeout_seconds, 60)
        last_count = self._folder_counts.get(folder, -1)
        return await asyncio.to_thread(self._noop_poll_with_persist, folder, actual_timeout, last_count)

    def _noop_poll_with_persist(self, folder: str, timeout_seconds: int, last_count: int) -> str:
        """带持久计数的 NOOP 轮询，解决跨周期新邮件检测问题

        返回值："new_mail" / "expunge" / "timeout"
        """
        import time as _time
        poll_interval = 10
        start_time = _time.time()

        # 首次调用时，记录初始邮件数
        if last_count < 0:
            last_count = self._get_folder_message_count(folder)
            if last_count < 0:
                raise ConnectionError("NOOP 轮询启动失败，IMAP 连接不可用")
            self._folder_counts[folder] = last_count

        while _time.time() - start_time < timeout_seconds:
            _time.sleep(poll_interval)
            try:
                current_count = self._get_folder_message_count(folder)
                if current_count < 0:
                    raise ConnectionError("IMAP 连接已断开")
                if current_count > last_count:
                    logger.info("检测到新邮件: folder=%s, %d -> %d", folder, last_count, current_count)
                    self._folder_counts[folder] = current_count
                    return "new_mail"
                if current_count < last_count:
                    logger.info("检测到邮件减少: folder=%s, %d -> %d", folder, last_count, current_count)
                    self._folder_counts[folder] = current_count
                    return "expunge"
                # 更新已知计数
                self._folder_counts[folder] = current_count
                # NOOP 保持连接活跃
                if self._conn:
                    try:
                        self._conn.noop()
                    except Exception:
                        self._conn = None
                        raise ConnectionError("IMAP 连接已断开")
            except Exception as e:
                if "IMAP 连接已断开" in str(e):
                    raise
                logger.warning("NOOP 轮询异常: %s", e)
                break

        return "timeout"

    async def _reconnect(self) -> None:
        """重连网易邮箱 IMAP"""
        self._conn = await asyncio.to_thread(self._connect_imap, self.email_addr, self._auth_code)

    async def disconnect(self) -> None:
        """断开连接"""
        if self._conn:
            try:
                await asyncio.to_thread(self._conn.logout)
            except Exception as e:
                logger.debug("断开连接失败: %s", e)
            self._conn = None
