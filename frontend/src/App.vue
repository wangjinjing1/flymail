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

      <main class="content">
        <UnifiedInbox v-if="currentView === 'unified'" />
        <MailList v-else-if="currentView === 'mail'" />
        <ComposeEmail v-else-if="currentView === 'compose'" @sent="currentView = 'mail'" @discard="currentView = 'mail'" />
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
import UnifiedInbox from './views/UnifiedInbox.vue';
import UserManagement from './views/UserManagement.vue';
import { useMailStore } from './stores/mail';
import { useUIStore } from './stores/ui';
import api from './utils/api';

const mailStore = useMailStore();
const uiStore = useUIStore();

const currentUser = ref<any>(null);
const authReady = ref(false);
const currentView = ref(sessionStorage.getItem('flymail_view') || 'unified');

const isAdmin = computed(() => currentUser.value?.role === 'admin');

const navItems = computed(() => {
  const items = [
    { key: 'unified', label: '聚合' },
    { key: 'mail', label: '邮件' },
    { key: 'compose', label: '写邮件' },
    { key: 'accounts', label: '账号' },
    { key: 'history-sync', label: '历史同步' },
    { key: 'settings', label: '设置' },
    { key: 'about', label: '关于' },
  ];
  if (isAdmin.value) {
    items.splice(5, 0, { key: 'users', label: '用户管理' });
  }
  return items;
});

const currentTitle = computed(() => navItems.value.find((item) => item.key === currentView.value)?.label || 'FlyMail');

async function bootstrapAfterLogin() {
  currentUser.value = await api.get('/auth/me');
  await mailStore.fetchUser();
  await mailStore.loadAccounts();
  await mailStore.loadUnifiedSettings();
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
  min-height: 100vh;
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  background: #f5f7fb;
}

.sidebar {
  padding: 24px 18px;
  background: #fff;
  border-right: 1px solid #e8edf3;
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
  padding: 20px 28px 28px;
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

.toast-container {
  position: fixed;
  right: 20px;
  bottom: 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  z-index: 30;
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

@media (max-width: 960px) {
  .app-shell {
    grid-template-columns: 1fr;
  }

  .sidebar {
    border-right: none;
    border-bottom: 1px solid #e8edf3;
  }

  .topbar {
    flex-direction: column;
    align-items: flex-start;
    gap: 14px;
  }
}
</style>
