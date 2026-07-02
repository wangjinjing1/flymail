import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import api from '../utils/api';
import { useUIStore } from './ui';

const CORE_FOLDERS = [
  { name: '收件箱', defaultPath: 'INBOX', aliases: ['INBOX', 'Inbox'] },
  { name: '已发送', defaultPath: 'Sent Messages', aliases: ['Sent Messages', 'Sent Items', 'Sent', 'Sent Mail', '[Gmail]/Sent Mail', '[Google Mail]/Sent Mail', '已发送'] },
  { name: '草稿箱', defaultPath: 'Drafts', aliases: ['Drafts', '[Gmail]/Drafts', '[Google Mail]/Drafts', '草稿箱'] },
  { name: '垃圾邮件', defaultPath: 'Junk', aliases: ['Junk', 'Junk Email', 'Spam', '[Gmail]/Spam', '[Google Mail]/Spam', '垃圾邮件'] },
  { name: '已删除', defaultPath: 'Trash', aliases: ['Trash', 'Deleted Items', 'Deleted', 'Deleted Messages', '[Gmail]/Trash', '[Google Mail]/Trash', '已删除'] },
];

interface MailNotification {
  id: string;
  provider: string;
  email: string;
  folder: string;
  time: number;
  read: boolean;
  type: string;
  message: string;
}

type FolderCount = { unread_count: number; total_count: number };
type AccountFolderCounts = Record<string, FolderCount>;

export const useMailStore = defineStore('mail', () => {
  const uiStore = useUIStore();
  const user = ref<any>(null);
  const loading = ref(false);
  const currentFolder = ref(sessionStorage.getItem('flymail_folder') || 'INBOX');
  const currentAccountId = ref('');
  const accounts = ref<any[]>(JSON.parse(sessionStorage.getItem('flymail_accounts') || '[]'));
  const reauthAccountIds = ref<Set<string>>(new Set());
  const folderCountsByAccount = ref<Record<string, AccountFolderCounts>>(
    JSON.parse(sessionStorage.getItem('flymail_folder_counts_by_account') || '{}'),
  );
  const folderCounts = ref<AccountFolderCounts>(
    JSON.parse(sessionStorage.getItem('flymail_folder_counts') || '{}'),
  );
  const folderPaths = ref<Record<string, string>>({
    '收件箱': 'INBOX',
    '已发送': 'Sent Messages',
    '草稿箱': 'Drafts',
    '垃圾邮件': 'Junk',
    '已删除': 'Trash',
  });
  const notifications = ref<MailNotification[]>([]);
  const composeDraft = ref<any>(null);
  let folderCountRequestVersion = 0;

  const folders = computed(() => CORE_FOLDERS.map((folder) => ({
    name: folder.name,
    path: folderPaths.value[folder.name] || folder.defaultPath,
    unread_count: folderCounts.value[folder.name]?.unread_count || 0,
    total_count: folderCounts.value[folder.name]?.total_count || 0,
  })));

  const currentFolderName = computed(() => {
    const folder = folders.value.find((item: any) => item.path === currentFolder.value);
    return folder ? folder.name : folderDisplayName(currentFolder.value);
  });

  const unreadNotificationCount = computed(() => notifications.value.filter((item) => !item.read).length);

  function folderDisplayName(name: string): string {
    const map: Record<string, string> = {
      INBOX: '收件箱',
      Inbox: '收件箱',
      Sent: '已发送',
      'Sent Messages': '已发送',
      'Sent Items': '已发送',
      '[Gmail]/Sent Mail': '已发送',
      '[Google Mail]/Sent Mail': '已发送',
      Drafts: '草稿箱',
      '[Gmail]/Drafts': '草稿箱',
      '[Google Mail]/Drafts': '草稿箱',
      Junk: '垃圾邮件',
      'Junk Email': '垃圾邮件',
      Spam: '垃圾邮件',
      '[Gmail]/Spam': '垃圾邮件',
      '[Google Mail]/Spam': '垃圾邮件',
      Trash: '已删除',
      Deleted: '已删除',
      'Deleted Items': '已删除',
      'Deleted Messages': '已删除',
      '已删除': '已删除',
      '[Gmail]/Trash': '已删除',
      '[Google Mail]/Trash': '已删除',
      '[Gmail]/Starred': '已加星标',
      '[Gmail]/Important': '重要邮件',
      '[Gmail]/All Mail': '所有邮件',
      Starred: '已加星标',
      Important: '重要邮件',
      'All Mail': '所有邮件',
    };
    return map[name] || name;
  }

  function findFolderCount(counts: Record<string, any>, folderName: string, folderPath: string) {
    const core = CORE_FOLDERS.find((folder) => folder.name === folderName);
    const candidates = [folderPath, ...(core?.aliases || [])];
    for (const path of candidates) {
      if (counts[path]) return counts[path];
    }
    return null;
  }

  function setCurrentFolderCounts(nextCounts: AccountFolderCounts) {
    folderCounts.value = nextCounts;
    if (currentAccountId.value) {
      folderCountsByAccount.value = {
        ...folderCountsByAccount.value,
        [currentAccountId.value]: nextCounts,
      };
      sessionStorage.setItem('flymail_folder_counts_by_account', JSON.stringify(folderCountsByAccount.value));
    }
    sessionStorage.setItem('flymail_folder_counts', JSON.stringify(folderCounts.value));
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

  async function loadAccounts() {
    try {
      const data = await api.get('/accounts') as any;
      accounts.value = data.accounts || [];
      const nextReauthIds = new Set<string>();
      for (const account of accounts.value) {
        if (account.reauth_needed) nextReauthIds.add(account.id);
      }
      reauthAccountIds.value = nextReauthIds;
      sessionStorage.setItem('flymail_accounts', JSON.stringify(accounts.value));
      if (accounts.value.length === 0) {
        currentAccountId.value = '';
        folderCountRequestVersion++;
        folderCounts.value = {};
        sessionStorage.removeItem('flymail_folder_counts');
        return;
      }
      const exists = accounts.value.some((account: any) => account.id === currentAccountId.value);
      if (!currentAccountId.value || !exists) {
        currentAccountId.value = accounts.value[0].id;
        currentFolder.value = 'INBOX';
        folderCountRequestVersion++;
        folderCounts.value = folderCountsByAccount.value[currentAccountId.value] || {};
      }
    } catch (e) {
      console.error('加载账号失败:', e);
      uiStore.error('加载账号失败');
    }
  }

  async function loadFolders() {
    if (accounts.value.length === 0 || !currentAccountId.value) {
      folderCountRequestVersion++;
      folderCounts.value = {};
      sessionStorage.removeItem('flymail_folder_counts');
      return;
    }
    try {
      const params: Record<string, string> = {};
      if (currentAccountId.value) params.account_id = currentAccountId.value;
      const data = await api.get('/folders', { params }) as any;
      if (data.reconnecting) {
        uiStore.error('邮箱连接异常，正在重新连接...');
        return;
      }
      const nextPaths: Record<string, string> = {};
      for (const folder of (data.folders || [])) {
        nextPaths[folder.name] = folder.path;
      }
      folderPaths.value = { ...folderPaths.value, ...nextPaths };
      loadFolderCounts();
    } catch (e) {
      console.error('加载文件夹失败:', e);
      uiStore.error('加载文件夹失败');
    }
  }

  async function loadFolderCounts() {
    if (accounts.value.length === 0 || !currentAccountId.value) {
      folderCountRequestVersion++;
      folderCounts.value = {};
      sessionStorage.removeItem('flymail_folder_counts');
      return;
    }
    const requestVersion = ++folderCountRequestVersion;
    try {
      const params: Record<string, string> = {};
      if (currentAccountId.value) params.account_id = currentAccountId.value;
      const data = await api.get('/folder-counts', { params }) as any;
      if (requestVersion !== folderCountRequestVersion || data.reconnecting) return;
      const counts = data.counts || {};
      const nextCounts: Record<string, FolderCount> = {};
      for (const [folderName, folderPath] of Object.entries(folderPaths.value)) {
        const count = findFolderCount(counts, folderName, folderPath as string);
        nextCounts[folderName] = {
          unread_count: count?.unread || 0,
          total_count: count?.total || 0,
        };
      }
      setCurrentFolderCounts(nextCounts);
    } catch (e) {
      if (requestVersion !== folderCountRequestVersion) return;
      console.error('加载文件夹计数失败:', e);
      uiStore.error('加载文件夹计数失败');
    }
  }

  function setFolder(path: string) {
    currentFolder.value = path;
    sessionStorage.setItem('flymail_folder', path);
  }

  function setAccount(id: string) {
    currentAccountId.value = id;
    currentFolder.value = 'INBOX';
    folderCountRequestVersion++;
    folderCounts.value = folderCountsByAccount.value[id] || {};
  }

  function updateFolderCounts(counts: Record<string, any>) {
    if (!counts || Object.keys(counts).length === 0) return;
    const nextCounts: Record<string, FolderCount> = {};
    for (const [folderName, folderPath] of Object.entries(folderPaths.value)) {
      const count = findFolderCount(counts, folderName, folderPath as string);
      nextCounts[folderName] = {
        unread_count: count?.unread || 0,
        total_count: count?.total || 0,
      };
    }
    setCurrentFolderCounts(nextCounts);
  }

  function decrementUnreadCount(folderPath: string) {
    const entry = Object.entries(folderPaths.value).find(([, path]) => path === folderPath);
    if (!entry) return;
    const folderName = entry[0];
    const existing = folderCounts.value[folderName];
    if (!existing || existing.unread_count <= 0) return;
    folderCounts.value = {
      ...folderCounts.value,
      [folderName]: { ...existing, unread_count: existing.unread_count - 1 },
    };
    sessionStorage.setItem('flymail_folder_counts', JSON.stringify(folderCounts.value));
  }

  function clearCurrentAccountState() {
    currentAccountId.value = '';
    currentFolder.value = 'INBOX';
    folderCountRequestVersion++;
    folderCounts.value = {};
    sessionStorage.removeItem('flymail_folder');
    sessionStorage.removeItem('flymail_folder_counts');
  }

  async function loadNotifications() {
    try {
      const data = await api.get('/notifications') as any;
      notifications.value = (data.notifications || []).map((item: any) => ({
        id: item.id,
        provider: item.provider,
        email: item.email,
        folder: item.folder,
        time: item.time,
        read: item.is_read,
        type: item.type || 'new_mail',
        message: item.message || '',
      }));
    } catch (e) {
      console.error('加载通知失败:', e);
      uiStore.error('加载通知失败');
    }
  }

  function addNotification(provider: string, email: string, folder: string, notificationId?: string, type = 'new_mail', message = '') {
    const id = notificationId || (Date.now().toString(36) + Math.random().toString(36).slice(2, 6));
    notifications.value.unshift({ id, provider, email, folder, time: Date.now(), read: false, type, message });
    if (notifications.value.length > 50) {
      notifications.value = notifications.value.slice(0, 50);
    }
  }

  async function markNotificationRead(id: string) {
    const index = notifications.value.findIndex((item) => item.id === id);
    if (index === -1 || notifications.value[index].read) return;
    notifications.value[index] = { ...notifications.value[index], read: true };
    try {
      await api.post(`/notifications/${id}/read`);
    } catch (e) {
      console.error('标记通知已读失败:', e);
      uiStore.error('标记已读失败');
    }
  }

  async function markAllNotificationsRead() {
    notifications.value = notifications.value.map((item) => ({ ...item, read: true }));
    try {
      await api.post('/notifications/read-all');
    } catch (e) {
      console.error('标记全部已读失败:', e);
      uiStore.error('标记已读失败');
    }
  }

  async function clearNotifications() {
    notifications.value = [];
    try {
      await api.delete('/notifications');
    } catch (e) {
      console.error('清空通知失败:', e);
      uiStore.error('操作失败');
    }
  }

  function setComposeDraft(draft: { to?: string[]; cc?: string[]; bcc?: string[]; subject?: string; body_html?: string; in_reply_to?: string; account_id?: string }) {
    composeDraft.value = draft;
  }

  function consumeComposeDraft() {
    const draft = composeDraft.value;
    composeDraft.value = null;
    return draft;
  }

  return {
    user,
    loading,
    fetchUser,
    currentFolder,
    currentAccountId,
    accounts,
    reauthAccountIds,
    folders,
    currentFolderName,
    loadAccounts,
    loadFolders,
    loadFolderCounts,
    setFolder,
    setAccount,
    clearCurrentAccountState,
    folderDisplayName,
    updateFolderCounts,
    decrementUnreadCount,
    notifications,
    unreadNotificationCount,
    addNotification,
    markNotificationRead,
    markAllNotificationsRead,
    clearNotifications,
    loadNotifications,
    composeDraft,
    setComposeDraft,
    consumeComposeDraft,
  };
});
