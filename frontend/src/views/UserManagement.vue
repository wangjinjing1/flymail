<template>
  <div class="user-page">
    <div class="header">
      <div>
        <h2>用户管理</h2>
        <p>管理员可创建用户、重置密码、启用或禁用账号。</p>
      </div>
      <button class="btn btn-secondary" @click="loadUsers">刷新</button>
    </div>

    <form class="create-form" @submit.prevent="createUser">
      <input v-model="form.username" placeholder="用户名" />
      <input v-model="form.password" type="password" placeholder="初始密码" />
      <select v-model="form.role">
        <option value="user">普通用户</option>
        <option value="admin">管理员</option>
      </select>
      <button class="btn btn-primary">创建用户</button>
    </form>

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
        <tr v-for="user in users" :key="user.id">
          <td>{{ user.username }}</td>
          <td>{{ user.role }}</td>
          <td>{{ user.status }}</td>
          <td>{{ formatTime(user.created_at) }}</td>
          <td class="actions">
            <button class="btn btn-secondary" @click="promptReset(user.id)">重置密码</button>
            <button class="btn btn-secondary" @click="toggleStatus(user.id)">
              {{ user.status === 'active' ? '禁用' : '启用' }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import api from '../utils/api';

const users = ref<any[]>([]);
const form = reactive({
  username: '',
  password: '',
  role: 'user',
});

async function loadUsers() {
  const data = await api.get('/admin/users') as any;
  users.value = data.users || [];
}

async function createUser() {
  await api.post('/admin/users', form);
  form.username = '';
  form.password = '';
  form.role = 'user';
  await loadUsers();
}

async function promptReset(userId: string) {
  const newPassword = window.prompt('输入新密码');
  if (!newPassword) return;
  await api.post(`/admin/users/${userId}/reset-password`, { new_password: newPassword });
}

async function toggleStatus(userId: string) {
  await api.post(`/admin/users/${userId}/toggle-status`);
  await loadUsers();
}

function formatTime(timestamp: number) {
  return new Date(timestamp * 1000).toLocaleString();
}

onMounted(loadUsers);
</script>

<style scoped>
.user-page {
  padding: 24px;
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

.create-form {
  display: grid;
  grid-template-columns: 1.2fr 1fr 160px 120px;
  gap: 12px;
  margin-bottom: 20px;
}

.create-form input,
.create-form select {
  height: 40px;
  border: 1px solid #dbe2ea;
  border-radius: 10px;
  padding: 0 12px;
}

.user-table {
  width: 100%;
  border-collapse: collapse;
  background: #fff;
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

@media (max-width: 960px) {
  .create-form {
    grid-template-columns: 1fr;
  }
}
</style>
