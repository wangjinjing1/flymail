<template>
  <div class="history-sync-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">&#x5386;&#x53F2;&#x90AE;&#x4EF6;&#x540C;&#x6B65;</h2>
        <p class="page-subtitle">
          &#x67E5;&#x770B;&#x6BCF;&#x4E2A;&#x90AE;&#x7BB1;&#x7684;&#x540C;&#x6B65;&#x8FDB;&#x5EA6;&#xFF0C;&#x652F;&#x6301;&#x6682;&#x505C;&#x3001;&#x7EE7;&#x7EED;&#x548C;&#x624B;&#x52A8;&#x5237;&#x65B0;&#x3002;
        </p>
      </div>
      <button class="btn btn-secondary" :disabled="loading" @click="loadJobs(true)">
        &#x5237;&#x65B0;
      </button>
    </div>

    <div v-if="loading && jobs.length === 0" class="loading-state">
      <div class="loading-dot"></div>
      <span>&#x52A0;&#x8F7D;&#x4E2D;...</span>
    </div>

    <div v-else-if="jobs.length === 0" class="empty-state">
      &#x6682;&#x65E0;&#x90AE;&#x7BB1;&#x8D26;&#x53F7;
    </div>

    <div v-else class="job-list">
      <section v-for="item in jobs" :key="item.account_id" class="job-card">
        <div class="job-header">
          <div>
            <div class="job-title-row">
              <h3 class="job-title">{{ item.email }}</h3>
              <span class="status-badge" :class="statusClass(item.status)">
                {{ statusText(item.status) }}
              </span>
            </div>
            <p class="job-provider">{{ providerName(item.provider) }}</p>
          </div>
          <div class="job-actions">
            <button
              v-if="canStart(item.status)"
              class="btn btn-primary"
              @click="startJob(item.account_id)"
            >
              &#x5F00;&#x59CB;
            </button>
            <button
              v-if="canPause(item.status)"
              class="btn btn-secondary"
              @click="pauseJob(item.account_id)"
            >
              &#x6682;&#x505C;
            </button>
            <button
              v-if="canResume(item.status)"
              class="btn btn-primary"
              @click="resumeJob(item.account_id)"
            >
              &#x7EE7;&#x7EED;
            </button>
            <button class="btn btn-secondary" @click="queryJob(item.account_id)">
              &#x67E5;&#x8BE2;
            </button>
          </div>
        </div>

        <div class="progress-grid">
          <div class="progress-item">
            <span class="progress-label">&#x6587;&#x4EF6;&#x5939;</span>
            <span class="progress-value">
              {{ item.job?.completed_folders || 0 }} / {{ item.job?.total_folders || 0 }}
            </span>
          </div>
          <div class="progress-item">
            <span class="progress-label">&#x5DF2;&#x540C;&#x6B65;&#x90AE;&#x4EF6;</span>
            <span class="progress-value">{{ item.job?.fetched_messages || 0 }}</span>
          </div>
          <div class="progress-item">
            <span class="progress-label">&#x5DF2;&#x4E0B;&#x8F7D;&#x9644;&#x4EF6;</span>
            <span class="progress-value">{{ item.job?.downloaded_attachments || 0 }}</span>
          </div>
          <div class="progress-item">
            <span class="progress-label">&#x5185;&#x5D4C;&#x56FE;&#x7247;</span>
            <span class="progress-value">{{ item.job?.downloaded_inline_images || 0 }}</span>
          </div>
        </div>

        <div class="folder-row">
          <span>
            &#x5F53;&#x524D;&#x6587;&#x4EF6;&#x5939;&#xFF1A;{{ item.job?.current_folder || '--' }}
          </span>
          <span>&#x9875;&#x7801;&#xFF1A;{{ item.job?.current_page || 1 }}</span>
          <span>UID&#xFF1A;{{ item.job?.current_uid || 0 }}</span>
        </div>

        <div v-if="item.job?.error_message" class="error-box">{{ item.job.error_message }}</div>

        <div class="time-row">
          <span>&#x66F4;&#x65B0;&#x65F6;&#x95F4;&#xFF1A;{{ formatTime(item.job?.updated_at) }}</span>
          <span v-if="item.job?.finished_at">
            &#x5B8C;&#x6210;&#x65F6;&#x95F4;&#xFF1A;{{ formatTime(item.job?.finished_at) }}
          </span>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { useUIStore } from '../stores/ui';
import api from '../utils/api';
import { providerName } from '../utils/provider';

interface HistorySyncJob {
  id: string
  status: string
  current_folder: string
  current_page: number
  current_uid: number
  total_folders: number
  completed_folders: number
  fetched_messages: number
  downloaded_attachments: number
  downloaded_inline_images: number
  error_message: string
  updated_at: number
  finished_at: number
}

interface HistorySyncItem {
  account_id: string
  email: string
  provider: string
  status: string
  job: HistorySyncJob | null
}

const ui = useUIStore();
const jobs = ref<HistorySyncItem[]>([]);
const loading = ref(false);
let pollTimer: number | null = null;

async function loadJobs(showError = false) {
  loading.value = true;
  try {
    const data = await api.get('/history-sync/jobs') as any;
    jobs.value = data.jobs || [];
  } catch (e: any) {
    if (showError) {
      ui.error(e.message || '\u52A0\u8F7D\u540C\u6B65\u72B6\u6001\u5931\u8D25');
    }
  } finally {
    loading.value = false;
  }
}

async function queryJob(accountId: string) {
  try {
    const data = await api.get(`/history-sync/jobs/${accountId}`) as any;
    const index = jobs.value.findIndex(item => item.account_id === accountId);
    if (index >= 0) {
      jobs.value[index] = {
        ...jobs.value[index],
        status: data.job?.status || 'idle',
        job: data.job || null,
      };
    }
  } catch (e: any) {
    ui.error(e.message || '\u67E5\u8BE2\u540C\u6B65\u72B6\u6001\u5931\u8D25');
  }
}

async function startJob(accountId: string) {
  try {
    await api.post(`/history-sync/jobs/${accountId}/start`);
    ui.success('\u5DF2\u5F00\u59CB\u540C\u6B65');
    await loadJobs();
  } catch (e: any) {
    ui.error(e.message || '\u540C\u6B65\u542F\u52A8\u5931\u8D25');
  }
}

async function pauseJob(accountId: string) {
  try {
    await api.post(`/history-sync/jobs/${accountId}/pause`);
    ui.success('\u5DF2\u6682\u505C\u540C\u6B65');
    await loadJobs();
  } catch (e: any) {
    ui.error(e.message || '\u6682\u505C\u5931\u8D25');
  }
}

async function resumeJob(accountId: string) {
  try {
    await api.post(`/history-sync/jobs/${accountId}/resume`);
    ui.success('\u5DF2\u7EE7\u7EED\u540C\u6B65');
    await loadJobs();
  } catch (e: any) {
    ui.error(e.message || '\u7EE7\u7EED\u5931\u8D25');
  }
}

function canStart(status: string) {
  return !status || status === 'idle' || status === 'completed' || status === 'failed';
}

function canPause(status: string) {
  return status === 'pending' || status === 'running';
}

function canResume(status: string) {
  return status === 'paused';
}

function statusClass(status: string) {
  return `status-${status || 'idle'}`;
}

function statusText(status: string) {
  const textMap: Record<string, string> = {
    idle: '\u672A\u5F00\u59CB',
    pending: '\u7B49\u5F85\u4E2D',
    running: '\u540C\u6B65\u4E2D',
    paused: '\u5DF2\u6682\u505C',
    completed: '\u5DF2\u5B8C\u6210',
    failed: '\u5931\u8D25',
  };
  return textMap[status] || status;
}

function formatTime(timestamp?: number) {
  if (!timestamp) return '--';
  return new Date(timestamp * 1000).toLocaleString();
}

onMounted(async () => {
  await loadJobs();
  pollTimer = window.setInterval(() => {
    loadJobs();
  }, 3000);
});

onBeforeUnmount(() => {
  if (pollTimer) {
    window.clearInterval(pollTimer);
  }
});
</script>

<style scoped>
.history-sync-page {
  height: 100%;
  overflow-y: auto;
  padding: var(--space-6);
  background: var(--bg-secondary);
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--space-4);
  margin-bottom: var(--space-6);
}

.page-title {
  margin: 0;
  font-size: 28px;
  font-weight: 700;
  color: var(--text-primary);
}

.page-subtitle {
  margin: var(--space-2) 0 0;
  color: var(--text-secondary);
  font-size: 15px;
}

.loading-state,
.empty-state {
  min-height: 320px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-secondary);
  font-size: 18px;
}

.loading-state {
  gap: var(--space-3);
}

.loading-dot {
  width: 12px;
  height: 12px;
  border-radius: 999px;
  background: var(--color-primary);
  animation: pulse 1.2s infinite ease-in-out;
}

.job-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.job-card {
  background: var(--bg-primary);
  border: 1px solid var(--border-primary);
  border-radius: 16px;
  padding: var(--space-5);
  box-shadow: var(--shadow-sm);
}

.job-header {
  display: flex;
  justify-content: space-between;
  gap: var(--space-4);
  align-items: flex-start;
}

.job-title-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.job-title {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  color: var(--text-primary);
}

.job-provider {
  margin: var(--space-2) 0 0;
  color: var(--text-secondary);
}

.job-actions {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
  justify-content: flex-end;
}

.progress-grid {
  margin-top: var(--space-5);
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
}

.progress-item {
  padding: var(--space-4);
  border-radius: 12px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-secondary);
}

.progress-label {
  display: block;
  color: var(--text-secondary);
  font-size: 13px;
  margin-bottom: var(--space-2);
}

.progress-value {
  color: var(--text-primary);
  font-size: 24px;
  font-weight: 700;
}

.folder-row,
.time-row {
  display: flex;
  gap: var(--space-5);
  flex-wrap: wrap;
  margin-top: var(--space-4);
  color: var(--text-secondary);
  font-size: 14px;
}

.error-box {
  margin-top: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border-radius: 12px;
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.2);
  color: #b91c1c;
  word-break: break-word;
}

.status-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 28px;
  padding: 0 12px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 600;
}

.status-idle,
.status-completed {
  background: rgba(34, 197, 94, 0.12);
  color: #15803d;
}

.status-pending {
  background: rgba(245, 158, 11, 0.12);
  color: #b45309;
}

.status-running {
  background: rgba(59, 130, 246, 0.12);
  color: #1d4ed8;
}

.status-paused {
  background: rgba(107, 114, 128, 0.14);
  color: #374151;
}

.status-failed {
  background: rgba(239, 68, 68, 0.12);
  color: #b91c1c;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 0.35;
    transform: scale(0.85);
  }

  50% {
    opacity: 1;
    transform: scale(1);
  }
}

@media (max-width: 1080px) {
  .progress-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .history-sync-page {
    padding: var(--space-4);
  }

  .page-header,
  .job-header {
    flex-direction: column;
  }

  .job-actions {
    width: 100%;
    justify-content: flex-start;
  }

  .progress-grid {
    grid-template-columns: 1fr;
  }
}
</style>
