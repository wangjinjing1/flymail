import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import api from '../utils/api';
import { useUIStore } from './ui';

/** 5个核心文件夹的固定定义（显示名 → 默认路径）
 *
 * 切换邮箱时，文件夹名和顺序永远不变，只是 path 随 provider 不同而更新。
 * 这样侧边栏不会因为切换账号而闪烁。
 */
const CORE_FOLDERS = [
  { name: '收件箱', defaultPath: 'INBOX', aliases: ['INBOX', 'Inbox'] },
  { name: '已发送', defaultPath: 'Sent Messages', aliases: ['Sent Messages', 'Sent Items', 'Sent'] },
  { name: '草稿箱', defaultPath: 'Drafts', aliases: ['Drafts'] },
  { name: '垃圾邮件', defaultPath: 'Junk', aliases: ['Junk', 'Junk Email', 'Spam'] },
  { name: '已删除', defaultPath: 'Trash', aliases: ['Trash', 'Deleted Items', 'Deleted'] },
];

export const useMailStore = defineStore('mail', () => {
  const uiStore = useUIStore();
  const user = ref<any>(null);
  const loading = ref(false);

  // 文件夹和账号共享状态
  // 从 sessionStorage 恢复上次浏览的文件夹，刷新后不会回到默认收件箱
  const currentFolder = ref(sessionStorage.getItem('flymail_folder') || 'INBOX');
  const currentAccountId = ref('');
  // 从 sessionStorage 恢复账号列表，避免刷新后白屏等待
  const accounts = ref<any[]>(JSON.parse(sessionStorage.getItem('flymail_accounts') || '[]'));
  // 需要重新授权的账号 ID 集合（从 API 响应中同步）
  const reauthAccountIds = ref<Set<string>>(new Set());

  // 文件夹数量映射：name → { unread_count, total_count }
  // 从 sessionStorage 恢复，刷新后侧边栏立即有数据
  const folderCounts = ref<Record<string, { unread_count: number; total_count: number }>>(
    JSON.parse(sessionStorage.getItem('flymail_folder_counts') || '{}')
  );

  // ==================== 聚合收件箱状态 ====================
  const unifiedAccountIds = ref<string[]>([]);  // 聚合的账号ID列表，空=未选择聚合邮箱

  // ==================== 通知系统 ====================

  /** 通知条目 */
  interface MailNotification {
    id: string;           // 唯一ID
    provider: string;     // 邮箱平台：qq / gmail / netease
    email: string;        // 邮箱地址
    folder: string;       // 文件夹
    time: number;         // 通知时间戳（毫秒）
    read: boolean;        // 是否已读
    type: string;         // 通知类型：new_mail / schedule_success / schedule_failed
    message: string;      // 通知描述文本
  }

  const notifications = ref<MailNotification[]>([]);

  /** 未读通知数量 */
  const unreadNotificationCount = computed(() =>
    notifications.value.filter(n => !n.read).length
  );

  /** 从后端数据库加载通知列表（页面初始化时调用） */
  async function loadNotifications() {
    try {
      const data = await api.get('/notifications') as any;
      if (data.notifications) {
        notifications.value = data.notifications.map((n: any) => ({
          id: n.id,
          provider: n.provider,
          email: n.email,
          folder: n.folder,
          time: n.time,          // 后端返回毫秒时间戳
          read: n.is_read,
          type: n.type || 'new_mail',
          message: n.message || '',
        }));
      }
    } catch (e) {
      console.error('加载通知失败:', e);
      uiStore.error('加载通知失败');
    }
  }

  /** 添加通知（WebSocket 推送时调用，使用后端生成的通知ID） */
  function addNotification(provider: string, email: string, folder: string, notificationId?: string, type: string = 'new_mail', message: string = '') {
    const id = notificationId || (Date.now().toString(36) + Math.random().toString(36).slice(2, 6));
    notifications.value.unshift({
      id,
      provider,
      email,
      folder,
      time: Date.now(),
      read: false,
      type,
      message,
    });
    // 最多保留50条通知
    if (notifications.value.length > 50) {
      notifications.value = notifications.value.slice(0, 50);
    }
  }

  /** 标记单条通知为已读（同步到后端数据库） */
  async function markNotificationRead(id: string) {
    const idx = notifications.value.findIndex(n => n.id === id);
    if (idx !== -1 && !notifications.value[idx].read) {
      notifications.value[idx] = { ...notifications.value[idx], read: true };
      // 同步到后端（仅未读→已读时才调用）
      try {
        await api.post(`/notifications/${id}/read`);
      } catch (e) {
        console.error('标记通知已读失败:', e);
        uiStore.error('标记已读失败');
      }
    }
  }

  /** 标记所有通知为已读（同步到后端数据库） */
  async function markAllNotificationsRead() {
    notifications.value = notifications.value.map(n => ({ ...n, read: true }));
    // 同步到后端
    try {
      await api.post('/notifications/read-all');
    } catch (e) {
      console.error('标记全部已读失败:', e);
      uiStore.error('标记已读失败');
    }
  }

  /** 清空所有通知（同步到后端数据库） */
  async function clearNotifications() {
    notifications.value = [];
    try {
      await api.delete('/notifications');
    } catch (e) {
      console.error('清空通知失败:', e);
      uiStore.error('操作失败');
    }
  }

  // 文件夹路径映射：name → path（从后端动态获取，切换账号时静默更新）
  const folderPaths = ref<Record<string, string>>({
    '收件箱': 'INBOX',
    '已发送': 'Sent Messages',
    '草稿箱': 'Drafts',
    '垃圾邮件': 'Junk',
    '已删除': 'Trash',
  });

  // 固定的5个核心文件夹列表（永远不变，不会闪烁）
  const folders = computed(() =>
    CORE_FOLDERS.map(f => ({
      name: f.name,
      path: folderPaths.value[f.name] || f.defaultPath,
      unread_count: folderCounts.value[f.name]?.unread_count || 0,
      total_count: folderCounts.value[f.name]?.total_count || 0,
    }))
  );

  // 当前文件夹显示名
  const currentFolderName = computed(() => {
    const f = folders.value.find((item: any) => item.path === currentFolder.value);
    return f ? f.name : folderDisplayName(currentFolder.value);
  });

  /** 文件夹名称 → 中文显示名
   *
   * 后端各provider已统一返回中文显示名（收件箱、已发送、草稿箱、垃圾邮件、已删除），
   * 这里只做英文名兼容映射，不再硬编码Modified UTF-7路径。
   */
  function folderDisplayName(name: string): string {
    const map: Record<string, string> = {
      // 英文名兼容映射
      'INBOX': '收件箱',
      'Sent': '已发送',
      'Drafts': '草稿箱',
      'Trash': '已删除',
      'Spam': '垃圾邮件',
      'Junk': '垃圾邮件',
      'Sent Messages': '已发送',
      'Sent Items': '已发送',
      'Deleted Messages': '已删除',
      'Deleted Items': '已删除',
      'Junk Email': '垃圾邮件',
      '[Gmail]/Sent Mail': '已发送',
      '[Gmail]/Drafts': '草稿箱',
      '[Gmail]/Trash': '已删除',
      '[Gmail]/Spam': '垃圾邮件',
      '[Gmail]/Starred': '已加星标',
      '[Gmail]/Important': '重要邮件',
      '[Gmail]/All Mail': '所有邮件',
      'Starred': '已加星标',
      'Important': '重要邮件',
      'All Mail': '所有邮件',
    };
    return map[name] || name;
  }

  async function fetchUser() {
    loading.value = true;
    try {
      user.value = await api.get('/auth/me');
    } catch (e) {
      console.error('Failed to fetch user:', e);
      uiStore.error('加载用户信息失败');
    } finally {
      loading.value = false;
    }
  }

  /** 加载账号列表 */
  async function loadAccounts() {
    try {
      const data = await api.get('/accounts') as any;
      accounts.value = data.accounts || [];
      // 从 API 响应中同步 reauth 状态
      const newReauthIds = new Set<string>();
      for (const acc of accounts.value) {
        if (acc.reauth_needed) {
          newReauthIds.add(acc.id);
        }
      }
      reauthAccountIds.value = newReauthIds;
      // 缓存到 sessionStorage，刷新后立即恢复
      sessionStorage.setItem('flymail_accounts', JSON.stringify(accounts.value));
      if (accounts.value.length === 0) {
        currentAccountId.value = '';
        return;
      }

      // 如果当前账号不存在（例如重新授权后账号 ID 改变），自动切换到第一个可用账号，避免继续请求旧账号。
      const exists = accounts.value.some((account: any) => account.id === currentAccountId.value);
      if (!currentAccountId.value || !exists) {
        currentAccountId.value = accounts.value[0].id;
        currentFolder.value = 'INBOX';
        folderCounts.value = {};
      }
    } catch (e) {
      console.error('加载账号失败:', e);
      uiStore.error('加载账号失败');
    }
  }

  /** 加载聚合收件箱设置（从后端读取用户选择的账号列表） */
  async function loadUnifiedSettings() {
    try {
      const data = await api.get('/settings/unified') as any;
      unifiedAccountIds.value = data.account_ids || [];
    } catch (e) {
      console.error('加载聚合设置失败:', e);
      uiStore.error('加载设置失败');
    }
  }

  /** 保存聚合收件箱设置（用户选择要聚合的账号列表） */
  async function saveUnifiedSettings(ids: string[]) {
    try {
      await api.put('/settings/unified', { account_ids: ids });
      unifiedAccountIds.value = ids;
    } catch (e) {
      console.error('保存聚合设置失败:', e);
      uiStore.error('保存设置失败');
    }
  }

  /** 加载文件夹列表（只更新路径映射，不替换文件夹列表）
   *
   * 后端返回的每个文件夹包含 name、path，
   * 我们用返回的数据更新路径映射，folders computed 会自动重新计算。
   * 因为 CORE_FOLDERS 固定不变，侧边栏不会闪烁。
   * 同时调用 /api/folder-counts 获取所有文件夹的计数。
   */
  async function loadFolders() {
    try {
      const params: Record<string, string> = {};
      if (currentAccountId.value) params.account_id = currentAccountId.value;
      const data = await api.get('/folders', { params }) as any;
      // Outlook 连接异常时，后端返回 reconnecting: true，展示友好提示并自动重试
      if (data.reconnecting) {
        uiStore.error('邮箱连接异常，正在重新连接...');
        // 不再用 setTimeout 轮询，改为等待后端 WebSocket connection_status 消息
        return;
      }
      const newPaths: Record<string, string> = {};
      for (const f of (data.folders || [])) {
        newPaths[f.name] = f.path;
      }
      folderPaths.value = newPaths;

      // 获取所有文件夹的计数（IMAP STATUS，最准确的计数来源）
      // 非阻塞：先显示缓存数据，后台加载计数后自动更新侧边栏
      loadFolderCounts();
    } catch (e) {
      console.error('加载文件夹失败:', e);
      uiStore.error('加载文件夹失败');
    }
  }

  /** 根据实际路径或常见别名查找文件夹计数
   *
   * Outlook/Hotmail 不同账号返回的核心文件夹路径可能不同，
   * 例如已发送可能是 Sent、Sent Items，垃圾邮件可能是 Junk、Junk Email。
   * 前端转换计数时需要同时匹配实际路径和别名，避免侧边栏数字为空。
   */
  function findFolderCount(counts: Record<string, any>, folderName: string, folderPath: string) {
    const core = CORE_FOLDERS.find(f => f.name === folderName);
    const candidates = [folderPath, ...(core?.aliases || [])];
    for (const path of candidates) {
      if (counts[path]) return counts[path];
    }
    return null;
  }

  /** 加载所有文件夹的计数（后台调用，不阻塞 UI）
   *
   * 使用合并更新（...spread）而非整体替换，避免覆盖 updateFolderCounts 已设置的正确值。
   * 场景：loadMessages 先完成 → updateFolderCounts 设置了收件箱计数 →
   *       loadFolderCounts 后完成 → 如果整体替换会覆盖掉之前的值。
   */
  async function loadFolderCounts() {
    try {
      const params: Record<string, string> = {};
      if (currentAccountId.value) params.account_id = currentAccountId.value;
      const data = await api.get('/folder-counts', { params }) as any;
      // Outlook 连接异常时，后端返回 reconnecting: true，静默返回（不弹重复提示）
      if (data.reconnecting) return;
      const counts = data.counts || {};
      // counts 格式: { "INBOX": {"total": 100, "unread": 5}, "Sent Messages": {"total": 20, "unread": 0} }
      // 需要转换为 name → { unread_count, total_count }
      const newCounts: Record<string, { unread_count: number; total_count: number }> = {};
      for (const [folderName, folderPath] of Object.entries(folderPaths.value)) {
        const c = findFolderCount(counts, folderName, folderPath);
        if (c) {
          newCounts[folderName] = { unread_count: c.unread || 0, total_count: c.total || 0 };
        }
      }
      // 合并更新，不覆盖已有的计数（保留 updateFolderCounts 设置的值）
      folderCounts.value = { ...folderCounts.value, ...newCounts };
      // 缓存到 sessionStorage，刷新后侧边栏立即有数据
      sessionStorage.setItem('flymail_folder_counts', JSON.stringify(folderCounts.value));
    } catch (e) {
      console.error('加载文件夹计数失败:', e);
      uiStore.error('加载文件夹计数失败');
    }
  }

  /** 切换文件夹 */
  function setFolder(path: string) {
    currentFolder.value = path;
    // 保存当前文件夹到 sessionStorage，刷新后可恢复
    sessionStorage.setItem('flymail_folder', path);
  }

  /** 切换账号
   *
   * 切换时重置到收件箱，但不清空 folders（因为 folders 是固定的5个核心文件夹）。
   * loadFolders 会静默更新 folderPaths，侧边栏不会闪烁。
   */
  function setAccount(id: string) {
    currentAccountId.value = id;
    currentFolder.value = 'INBOX';
    // 切换账号时清空计数，等 loadMessages 后再更新
    folderCounts.value = {};
  }

  /** 从 list_messages API 的返回值更新当前文件夹的计数
   *
   * 只更新 total_count，不覆盖 unread_count。
   * 原因：QQ邮箱的 select(readonly=True) + search(UNSEEN) 会返回0（QQ IMAP的bug），
   * 而 loadFolderCounts 使用的 IMAP STATUS 命令不受此影响，返回准确的未读数。
   * 如果用 updateFolderCounts 的 unread_total 覆盖，会导致"显示后消失"的问题。
   */
  function updateFolderCounts(folderPath: string, total: number, unreadTotal: number) {
    // 通过路径找到对应的文件夹名
    const entry = Object.entries(folderPaths.value).find(([_, path]) => path === folderPath);
    if (entry) {
      const folderName = entry[0];
      const existing = folderCounts.value[folderName];
      folderCounts.value = {
        ...folderCounts.value,
        [folderName]: {
          // unread_count: 优先保留 IMAP STATUS 的值（loadFolderCounts 设置的），
          // 如果还没有则使用 loadMessages 的值作为初始值
          unread_count: existing ? existing.unread_count : unreadTotal,
          total_count: total,
        },
      };
      sessionStorage.setItem('flymail_folder_counts', JSON.stringify(folderCounts.value));
    }
  }

  /** 标记已读后减少侧边栏未读数
   *
   * 用户点击未读邮件后，IMAP STORE +FLAGS \Seen 成功，
   * 侧边栏对应文件夹的 unread_count 减1。
   */
  function decrementUnreadCount(folderPath: string) {
    const entry = Object.entries(folderPaths.value).find(([_, path]) => path === folderPath);
    if (entry) {
      const folderName = entry[0];
      const existing = folderCounts.value[folderName];
      if (existing && existing.unread_count > 0) {
        folderCounts.value = {
          ...folderCounts.value,
          [folderName]: {
            ...existing,
            unread_count: existing.unread_count - 1,
          },
        };
      }
    }
  }

  /** 清空当前账号和文件夹状态（删除账号后调用，避免继续请求旧账号/旧文件夹） */
  function clearCurrentAccountState() {
    currentAccountId.value = '';
    currentFolder.value = 'INBOX';
    folderCounts.value = {};
    sessionStorage.removeItem('flymail_folder');
  }

  // 写邮件草稿数据（回复/转发时由 MailList 写入，ComposeEmail 读取）
  const composeDraft = ref<any>(null);

  /** 设置写邮件草稿（回复/转发时调用） */
  function setComposeDraft(draft: { to?: string[]; cc?: string[]; subject?: string; body_html?: string; in_reply_to?: string; account_id?: string }) {
    composeDraft.value = draft;
  }

  /** 消费写邮件草稿（ComposeEmail 读取后清空） */
  function consumeComposeDraft() {
    const draft = composeDraft.value;
    composeDraft.value = null;
    return draft;
  }

  return {
    user, loading, fetchUser,
    currentFolder, currentAccountId, accounts, reauthAccountIds, folders, currentFolderName,
    loadAccounts, loadFolders, loadFolderCounts, setFolder, setAccount, clearCurrentAccountState, folderDisplayName, updateFolderCounts, decrementUnreadCount,
    notifications, unreadNotificationCount, addNotification, markNotificationRead, markAllNotificationsRead, clearNotifications, loadNotifications,
    unifiedAccountIds, loadUnifiedSettings, saveUnifiedSettings,
    composeDraft, setComposeDraft, consumeComposeDraft,
  };
});
