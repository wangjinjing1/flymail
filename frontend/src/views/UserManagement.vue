<template>
  <div class="user-page">
    <div class="header">
      <div>
        <h2>用户管理</h2>
        <p>管理员可创建普通用户、重置密码、启用或禁用账号。</p>
      </div>
      <button class="btn btn-secondary" @click="loadUsers">刷新</button>
    </div>

    <form class="create-form" @submit.prevent="createUser">
      <input v-model="form.username" placeholder="用户名" />
      <input v-model="form.password" type="password" placeholder="初始密码" />
      <button class="btn btn-primary">创建普通用户</button>
    </form>

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
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import api from '../utils/api';

const users = ref<any[]>([]);
const form = reactive({
  username: '',
  password: '',
});
const filters = reactive({
  keyword: '',
  role: '',
  status: '',
});

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
  try {
    await api.post('/admin/users', { username: form.username, password: form.password });
    form.username = '';
    form.password = '';
    await loadUsers();
  } catch (e: any) {
    window.alert(e?.error || e?.message || '创建用户失败');
  }
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

.create-form,
.filters {
  display: grid;
  gap: 12px;
  margin-bottom: 20px;
}

.create-form {
  grid-template-columns: 1.2fr 1fr 140px;
}

.filters {
  grid-template-columns: 1.2fr 180px 180px;
}

.create-form input,
.filters input,
.filters select {
  height: 40px;
  border: 1px solid #dbe2ea;
  border-radius: 10px;
  padding: 0 12px;
  min-width: 0;
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

  .create-form,
  .filters {
    grid-template-columns: 1fr;
  }
}
</style>
