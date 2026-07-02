<template>
  <LoginView v-if="!authReady || !currentUser" @success="handleLoginSuccess" />

  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <img src="/icon.png" alt="FlyMail" class="brand-logo" />
        <div>
          <div class="brand-name">FlyMail</div>
          <div class="brand-subtitle">Docker 多用户版</div>
        </div>
      </div>

      <div class="nav-scroll">
        <nav class="nav">
          <button
            v-for="item in navItems"
            :key="item.key"
            class="nav-item"
            :class="{ active: currentView === item.key }"
            @click="currentView = item.key"
          >
            {{ item.label }}
          </button>
        </nav>
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <div>
          <h1>{{ currentTitle }}</h1>
          <p>{{ currentUser.username }} · {{ currentUser.role === 'admin' ? '管理员' : '普通用户' }}</p>
        </div>
        <div class="topbar-actions">
          <button class="btn btn-secondary" @click="changePassword">修改密码</button>
          <button class="btn btn-secondary" @click="logout">退出登录</button>
        </div>
      </header>

      <main class="content" :class="`content-${currentView}`">
        <ComposeEmail v-if="currentView === 'compose'" @sent="handleComposeSent" @discard="currentView = 'mail'" />
        <template v-else-if="currentView === 'mail'">
          <KeepAlive>
            <MailList />
          </KeepAlive>
        </template>
        <AccountList v-else-if="currentView === 'accounts'" />
        <HistorySync v-else-if="currentView === 'history-sync'" />
        <UserManagement v-else-if="currentView === 'users' && isAdmin" />
        <Settings v-else-if="currentView === 'settings'" />
        <About v-else-if="currentView === 'about'" />
      </main>
    </div>

    <div class="toast-container">
      <transition-group name="toast">
        <div v-for="t in uiStore.toasts" :key="t.id" class="toast-item" :class="'toast-' + t.type">
          {{ t.message }}
        </div>
      </transition-group>
    </div>

    <div v-if="uiStore.confirmVisible" class="confirm-overlay" @click.self="uiStore.confirmCancel()">
      <div class="confirm-dialog">
        <h3 class="confirm-title">{{ uiStore.confirmOptions.title }}</h3>
        <p class="confirm-message">{{ uiStore.confirmOptions.message }}</p>
        <div class="confirm-actions">
          <button class="btn btn-secondary" @click="uiStore.confirmCancel()">
            {{ uiStore.confirmOptions.cancelText || '取消' }}
          </button>
          <button
            class="btn"
            :class="uiStore.confirmOptions.danger ? 'btn-danger' : 'btn-primary'"
            @click="uiStore.confirmOk()"
          >
            {{ uiStore.confirmOptions.confirmText || '确定' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import About from './views/About.vue';
import AccountList from './views/AccountList.vue';
import ComposeEmail from './views/ComposeEmail.vue';
import HistorySync from './views/HistorySync.vue';
import LoginView from './views/LoginView.vue';
import MailList from './views/MailList.vue';
import Settings from './views/Settings.vue';
import UserManagement from './views/UserManagement.vue';
import { useMailStore } from './stores/mail';
import { useUIStore } from './stores/ui';
import api from './utils/api';

const mailStore = useMailStore();
const uiStore = useUIStore();

const currentUser = ref<any>(null);
const authReady = ref(false);
const currentView = ref(sessionStorage.getItem('flymail_view') || 'compose');

const isAdmin = computed(() => currentUser.value?.role === 'admin');

const navItems = computed(() => {
  const items = [
    { key: 'compose', label: '写邮件' },
    { key: 'mail', label: '邮件' },
    { key: 'accounts', label: '账号管理' },
    { key: 'history-sync', label: '同步管理' },
    { key: 'settings', label: '设置' },
    { key: 'about', label: '关于' },
  ];
  if (isAdmin.value) {
    items.splice(4, 0, { key: 'users', label: '用户管理' });
  }
  return items;
});

const currentTitle = computed(() => navItems.value.find((item) => item.key === currentView.value)?.label || 'FlyMail');

async function bootstrapAfterLogin() {
  currentUser.value = await api.get('/auth/me');
  await mailStore.fetchUser();
  await mailStore.loadAccounts();
  await mailStore.loadNotifications();
}

async function checkAuth() {
  try {
    await bootstrapAfterLogin();
  } catch {
    currentUser.value = null;
  } finally {
    authReady.value = true;
  }
}

async function handleLoginSuccess() {
  await bootstrapAfterLogin();
  authReady.value = true;
}

function handleComposeSent(payload?: { sentFolder?: string }) {
  if (payload?.sentFolder) {
    mailStore.setFolder(payload.sentFolder);
  }
  currentView.value = 'mail';
}

async function logout() {
  await api.post('/auth/logout');
  currentUser.value = null;
  mailStore.accounts = [];
  sessionStorage.removeItem('flymail_view');
}

async function changePassword() {
  const currentPassword = window.prompt('请输入当前密码');
  if (!currentPassword) return;
  const newPassword = window.prompt('请输入新密码');
  if (!newPassword) return;
  await api.post('/auth/change-password', {
    current_password: currentPassword,
    new_password: newPassword,
  });
  uiStore.success('密码已更新');
}

onMounted(checkAuth);

watch(currentView, (value) => {
  sessionStorage.setItem('flymail_view', value);
});
</script>

<style scoped>
.app-shell {
  height: 100vh;
  min-height: 100vh;
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  background: #f5f7fb;
  overflow: hidden;
}

.sidebar {
  padding: 24px 18px;
  background: #fff;
  border-right: 1px solid #e8edf3;
  overflow-y: auto;
  min-width: 0;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 24px;
}

.brand-logo {
  width: 44px;
  height: 44px;
}

.brand-name {
  font-size: 24px;
  font-weight: 700;
}

.brand-subtitle {
  color: #64748b;
  font-size: 13px;
}

.nav-scroll {
  min-width: 0;
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.nav-item {
  height: 42px;
  border: none;
  border-radius: 10px;
  text-align: left;
  padding: 0 14px;
  background: transparent;
  cursor: pointer;
  font-size: 14px;
}

.nav-item.active {
  background: #1677ff;
  color: #fff;
}

.main {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 24px 28px 0;
}

.topbar h1 {
  margin: 0;
  font-size: 28px;
}

.topbar p {
  margin: 6px 0 0;
  color: #64748b;
}

.topbar-actions {
  display: flex;
  gap: 10px;
}

.content {
  flex: 1;
  min-height: 0;
  min-width: 0;
  width: 100%;
  display: flex;
  padding: 20px 28px 28px;
  overflow: hidden;
}

.content-mail {
  padding: 20px 28px 28px;
}

.content-compose,
.content-accounts,
.content-history-sync,
.content-users,
.content-settings,
.content-about {
  padding: 0;
  width: 100%;
}

.btn {
  height: 40px;
  border: none;
  border-radius: 10px;
  padding: 0 14px;
  cursor: pointer;
}

.btn-secondary {
  background: #e9eef5;
  color: #0f172a;
}

.btn-primary {
  background: #1677ff;
  color: #fff;
}

.btn-danger {
  background: #dc2626;
  color: #fff;
}

.toast-container {
  position: fixed;
  right: 20px;
  bottom: 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  z-index: 10000;
}

.toast-item {
  min-width: 220px;
  padding: 12px 14px;
  border-radius: 10px;
  color: #fff;
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.16);
}

.toast-success {
  background: #16a34a;
}

.toast-error {
  background: #dc2626;
}

.toast-warning {
  background: #d97706;
}

.toast-info {
  background: #2563eb;
}

.confirm-overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.36);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9000;
  padding: 20px;
}

.confirm-dialog {
  width: min(100%, 420px);
  background: #fff;
  border-radius: 14px;
  padding: 20px;
  box-shadow: 0 20px 48px rgba(15, 23, 42, 0.18);
}

.confirm-title {
  margin: 0;
  font-size: 18px;
  color: #0f172a;
}

.confirm-message {
  margin: 10px 0 0;
  font-size: 14px;
  line-height: 1.6;
  color: #475569;
}

.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 18px;
}

@media (max-width: 960px) {
  .app-shell {
    grid-template-columns: 1fr;
    grid-template-rows: auto auto minmax(0, 1fr);
    height: 100dvh;
    overflow-y: auto;
  }

  .sidebar {
    border-right: none;
    border-bottom: 1px solid #e8edf3;
    padding: 18px 16px 14px;
    overflow: visible;
  }

  .brand {
    margin-bottom: 16px;
  }

  .brand-logo {
    width: 40px;
    height: 40px;
  }

  .brand-name {
    font-size: 20px;
  }

  .brand-subtitle {
    font-size: 12px;
  }

  .nav-scroll {
    overflow-x: auto;
    overflow-y: hidden;
    padding-right: 4px;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
  }

  .nav-scroll::-webkit-scrollbar {
    display: none;
  }

  .nav {
    display: inline-flex;
    flex-direction: row;
    gap: 8px;
    min-width: max-content;
    padding-bottom: 2px;
  }

  .nav-item {
    flex: 0 0 auto;
    height: 38px;
    padding: 0 14px;
    white-space: nowrap;
    border-radius: 999px;
    background: #f1f5f9;
  }

  .nav-item.active {
    background: #1677ff;
    color: #fff;
  }

  .topbar {
    flex-direction: column;
    align-items: flex-start;
    gap: 14px;
    padding: 18px 16px 0;
  }

  .topbar h1 {
    font-size: 22px;
  }

  .topbar p {
    margin-top: 4px;
    font-size: 14px;
  }

  .topbar-actions {
    width: 100%;
    flex-wrap: wrap;
    gap: 8px;
  }

  .topbar-actions .btn {
    flex: 0 0 auto;
    height: 36px;
    padding: 0 12px;
    border-radius: 9px;
  }

  .main {
    overflow: visible;
  }

  .content {
    padding: 0 0 calc(16px + env(safe-area-inset-bottom, 0px));
    overflow: visible;
    width: 100%;
  }

  .content-mail {
    padding: 12px 0 calc(16px + env(safe-area-inset-bottom, 0px));
  }
}
</style>
