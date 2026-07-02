<template>
  <div class="user-page">
    <div class="header">
      <div>
        <h2>用户管理</h2>
        <p>管理员可创建普通用户、重置密码、启用或禁用账号。</p>
      </div>
      <div class="header-actions">
        <button class="btn btn-primary" @click="openCreateModal">新增用户</button>
        <button class="btn btn-secondary" @click="loadUsers">刷新</button>
      </div>
    </div>

    <div class="filters">
      <input v-model="filters.keyword" placeholder="筛选用户名" />
      <select v-model="filters.role">
        <option value="">全部角色</option>
        <option value="admin">管理员</option>
        <option value="user">普通用户</option>
      </select>
      <select v-model="filters.status">
        <option value="">全部状态</option>
        <option value="active">启用</option>
        <option value="disabled">禁用</option>
      </select>
    </div>

    <div class="user-table-wrap">
      <table class="user-table">
        <thead>
          <tr>
            <th>用户名</th>
            <th>角色</th>
            <th>状态</th>
            <th>创建时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="user in filteredUsers" :key="user.id">
            <td>{{ user.username }}</td>
            <td>{{ roleText(user.role) }}</td>
            <td>{{ statusText(user.status) }}</td>
            <td>{{ formatTime(user.created_at) }}</td>
            <td class="actions">
              <button class="btn btn-secondary" @click="promptReset(user.id)">重置密码</button>
              <button class="btn btn-secondary" @click="toggleStatus(user.id)">
                {{ user.status === 'active' ? '禁用' : '启用' }}
              </button>
              <button v-if="user.role !== 'admin'" class="btn btn-danger" @click="deleteUser(user)">删除</button>
            </td>
          </tr>
          <tr v-if="filteredUsers.length === 0">
            <td class="empty" colspan="5">没有匹配的用户</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="showCreateModal" class="modal-overlay" @click.self="closeCreateModal">
      <form class="modal-card" @submit.prevent="createUser">
        <div class="modal-header">
          <h3>新增用户</h3>
          <button class="icon-btn" type="button" title="关闭" @click="closeCreateModal">×</button>
        </div>
        <label class="field">
          <span>用户名</span>
          <input v-model.trim="form.username" placeholder="输入用户名" autocomplete="username" />
        </label>
        <label class="field">
          <span>初始密码</span>
          <div class="password-field">
            <input
              v-model="form.password"
              :type="showPassword ? 'text' : 'password'"
              placeholder="输入初始密码"
              autocomplete="new-password"
            />
            <button class="eye-btn" type="button" :title="showPassword ? '隐藏密码' : '显示密码'" @click="showPassword = !showPassword">
              <svg v-if="showPassword" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17.94 17.94A10.94 10.94 0 0 1 12 20C7 20 2.73 16.89 1 12a11.2 11.2 0 0 1 5.06-5.94"/><path d="M10.58 10.58A2 2 0 0 0 12 14a2 2 0 0 0 1.42-.58"/><path d="m3 3 18 18"/></svg>
              <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12Z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>
          </div>
        </label>
        <label class="field">
          <span>确认密码</span>
          <div class="password-field">
            <input
              v-model="form.confirmPassword"
              :type="showConfirmPassword ? 'text' : 'password'"
              placeholder="再次输入密码"
              autocomplete="new-password"
            />
            <button class="eye-btn" type="button" :title="showConfirmPassword ? '隐藏密码' : '显示密码'" @click="showConfirmPassword = !showConfirmPassword">
              <svg v-if="showConfirmPassword" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17.94 17.94A10.94 10.94 0 0 1 12 20C7 20 2.73 16.89 1 12a11.2 11.2 0 0 1 5.06-5.94"/><path d="M10.58 10.58A2 2 0 0 0 12 14a2 2 0 0 0 1.42-.58"/><path d="m3 3 18 18"/></svg>
              <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12Z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>
          </div>
        </label>
        <div class="modal-actions">
          <button class="btn btn-secondary" type="button" @click="closeCreateModal">取消</button>
          <button class="btn btn-primary" type="submit">创建普通用户</button>
        </div>
      </form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import api from '../utils/api';

const users = ref<any[]>([]);
const form = reactive({
  username: '',
  password: '',
  confirmPassword: '',
});
const filters = reactive({
  keyword: '',
  role: '',
  status: '',
});
const showCreateModal = ref(false);
const showPassword = ref(false);
const showConfirmPassword = ref(false);

const filteredUsers = computed(() => {
  const keyword = filters.keyword.trim().toLowerCase();
  return users.value.filter((user) => {
    if (keyword && !String(user.username || '').toLowerCase().includes(keyword)) return false;
    if (filters.role && user.role !== filters.role) return false;
    if (filters.status && user.status !== filters.status) return false;
    return true;
  });
});

async function loadUsers() {
  const data = await api.get('/admin/users') as any;
  users.value = data.users || [];
}

async function createUser() {
  if (!form.username.trim()) {
    window.alert('请输入用户名');
    return;
  }
  if (!form.password) {
    window.alert('请输入初始密码');
    return;
  }
  if (form.password !== form.confirmPassword) {
    window.alert('两次输入的密码不一致');
    return;
  }
  try {
    await api.post('/admin/users', { username: form.username, password: form.password });
    closeCreateModal();
    await loadUsers();
  } catch (e: any) {
    window.alert(e?.error || e?.message || '创建用户失败');
  }
}

function openCreateModal() {
  form.username = '';
  form.password = '';
  form.confirmPassword = '';
  showPassword.value = false;
  showConfirmPassword.value = false;
  showCreateModal.value = true;
}

function closeCreateModal() {
  showCreateModal.value = false;
  form.username = '';
  form.password = '';
  form.confirmPassword = '';
}

async function promptReset(userId: string) {
  const newPassword = window.prompt('输入新密码');
  if (!newPassword) return;
  try {
    await api.post(`/admin/users/${userId}/reset-password`, { new_password: newPassword });
  } catch (e: any) {
    window.alert(e?.error || e?.message || '重置密码失败');
  }
}

async function toggleStatus(userId: string) {
  try {
    await api.post(`/admin/users/${userId}/toggle-status`);
    await loadUsers();
  } catch (e: any) {
    window.alert(e?.error || e?.message || '更新状态失败');
  }
}

async function deleteUser(user: any) {
  if (!window.confirm(`确定删除用户 ${user.username} 吗？`)) return;
  try {
    await api.delete(`/admin/users/${user.id}`);
    await loadUsers();
  } catch (e: any) {
    window.alert(e?.error || e?.message || '删除用户失败');
  }
}

function roleText(role: string) {
  return role === 'admin' ? '管理员' : '普通用户';
}

function statusText(status: string) {
  return status === 'disabled' ? '禁用' : '启用';
}

function formatTime(timestamp: number) {
  return new Date(timestamp * 1000).toLocaleString();
}

onMounted(loadUsers);
</script>

<style scoped>
.user-page {
  flex: 1;
  width: 100%;
  height: 100%;
  min-height: 0;
  min-width: 0;
  overflow-y: auto;
  padding: 24px;
  background: var(--bg-secondary);
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 20px;
}

.header h2 {
  margin: 0;
}

.header p {
  margin: 8px 0 0;
  color: #64748b;
}

.filters {
  display: grid;
  gap: 12px;
  margin-bottom: 20px;
}

.header-actions {
  display: flex;
  gap: 10px;
}

.filters {
  grid-template-columns: 1.2fr 180px 180px;
}

.filters input,
.filters select {
  height: 40px;
  border: 1px solid #dbe2ea;
  border-radius: 10px;
  padding: 0 12px;
  min-width: 0;
}

.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 9000;
  background: rgba(15, 23, 42, 0.36);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.modal-card {
  width: min(100%, 420px);
  background: #fff;
  border-radius: 8px;
  padding: 20px;
  box-shadow: 0 20px 48px rgba(15, 23, 42, 0.18);
}

.modal-header,
.modal-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.modal-header {
  margin-bottom: 16px;
}

.modal-header h3 {
  margin: 0;
  font-size: 18px;
}

.modal-actions {
  justify-content: flex-end;
  margin-top: 18px;
}

.field {
  display: block;
  margin-bottom: 14px;
}

.field span {
  display: block;
  margin-bottom: 6px;
  color: #475569;
  font-size: 13px;
}

.field input,
.password-field input {
  width: 100%;
  height: 40px;
  border: 1px solid #dbe2ea;
  border-radius: 8px;
  padding: 0 12px;
  min-width: 0;
  box-sizing: border-box;
}

.password-field {
  position: relative;
}

.password-field input {
  padding-right: 42px;
}

.eye-btn,
.icon-btn {
  border: none;
  background: transparent;
  cursor: pointer;
  color: #64748b;
}

.eye-btn {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.icon-btn {
  width: 30px;
  height: 30px;
  font-size: 22px;
  line-height: 1;
}

.user-table-wrap {
  overflow-x: auto;
  border-radius: 14px;
  background: #fff;
}

.user-table {
  width: 100%;
  border-collapse: collapse;
  background: #fff;
  min-width: 720px;
}

.user-table th,
.user-table td {
  text-align: left;
  padding: 14px 12px;
  border-bottom: 1px solid #edf2f7;
}

.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.empty {
  text-align: center !important;
  color: #64748b;
}

.btn {
  height: 38px;
  border: none;
  border-radius: 10px;
  padding: 0 14px;
  cursor: pointer;
}

.btn-primary {
  background: #1677ff;
  color: #fff;
}

.btn-secondary {
  background: #eef2f7;
  color: #0f172a;
}

.btn-danger {
  background: #fee2e2;
  color: #b91c1c;
}

@media (max-width: 960px) {
  .user-page {
    padding: 16px;
  }

  .header {
    flex-direction: column;
    gap: 12px;
  }

  .filters {
    grid-template-columns: 1fr;
  }
}
</style>
