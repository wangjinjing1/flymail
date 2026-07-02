<template>
  <div class="history-sync-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">同步管理</h2>
        <p class="page-subtitle">查看每个邮箱的同步进度，支持暂停、继续、刷新、清空和失败重试。</p>
      </div>
      <button class="btn btn-secondary" :disabled="manualRefreshing" @click="loadJobs({ showError: true, manual: true })">
        {{ manualRefreshing ? '刷新中...' : '刷新进度' }}
      </button>
    </div>

    <div v-if="initialLoading && jobs.length === 0" class="loading-state">
      <div class="loading-dot"></div>
      <span>加载中...</span>
    </div>

    <div v-else-if="jobs.length === 0" class="empty-state">暂无邮箱账号</div>

    <div v-else class="job-list">
      <section v-for="item in jobs" :key="item.account_id" class="job-card">
        <div class="job-header">
          <div>
            <div class="job-title-row">
              <h3 class="job-title">{{ item.email }}</h3>
              <span class="status-badge" :class="statusClass(item.status)">{{ statusText(item.status) }}</span>
            </div>
            <p class="job-provider">{{ providerName(item.provider) }}</p>
          </div>
          <div class="job-actions">
            <button v-if="canPause(item.status)" class="btn btn-secondary" @click="pauseJob(item.account_id)">暂停</button>
            <button v-else-if="canResume(item.status)" class="btn btn-primary" @click="resumeJob(item.account_id)">继续</button>
            <button v-else class="btn btn-secondary" disabled>暂停/继续</button>
            <button class="btn btn-warning" :disabled="isFullSyncActive(item)" @click="refreshSync(item)">刷新同步</button>
            <button class="btn btn-danger" :disabled="isClearActive(item.clear_job)" @click="clearJob(item)">清空</button>
            <button v-if="canRetry(item.status)" class="btn btn-primary" @click="retryJob(item.account_id)">重试</button>
          </div>
        </div>

        <div v-if="item.clear_job && item.clear_job.status !== 'completed'" class="clear-job-row">
          <span class="clear-job-label">清空任务：<strong>{{ statusText(item.clear_job.status, 'clear_cache') }}</strong></span>
          <span>已删除文件：{{ item.clear_job.downloaded_attachments || 0 }}</span>
        </div>

        <div class="progress-grid">
          <div class="progress-item progress-summary">
            <span class="progress-label">已同步邮件</span>
            <span class="progress-value">{{ syncedMessageCount(item) }} / {{ totalMessageCount(item) }}</span>
          </div>
          <div
            v-for="folder in folderProgress(item)"
            :key="folder.folder"
            class="progress-item"
          >
            <span class="progress-label">{{ folder.label }}</span>
            <span class="progress-value">{{ folder.cached_count || 0 }} / {{ folder.total_count || 0 }}</span>
          </div>
        </div>

        <div v-if="item.job?.error_message" class="error-box">{{ item.job.error_message }}</div>

        <div class="time-row">
          <span>更新时间：{{ formatTime(item.job?.updated_at) }}</span>
          <span v-if="item.job?.finished_at">完成时间：{{ formatTime(item.job?.finished_at) }}</span>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { useWebSocket } from '../composables/useWebSocket';
import { useUIStore } from '../stores/ui';
import api from '../utils/api';
import { providerName } from '../utils/provider';

interface HistorySyncJob {
  id: string
  job_type?: string
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

interface FolderProgressItem {
  folder: string
  label: string
  cached_count: number
  summary_count?: number
  total_count: number
  unread_count: number
  is_synced: boolean
  sync_job?: HistorySyncJob | null
  clear_job?: HistorySyncJob | null
}

interface HistorySyncItem {
  account_id: string
  email: string
  provider: string
  status: string
  job: HistorySyncJob | null
  clear_job?: HistorySyncJob | null
  folder_progress?: FolderProgressItem[]
}

const ui = useUIStore();
const jobs = ref<HistorySyncItem[]>([]);
const initialLoading = ref(false);
const manualRefreshing = ref(false);
let pollTimer: number | null = null;
let wsRefreshTimer: number | null = null;

const { connect: connectWs, disconnect: disconnectWs } = useWebSocket(handleWsMessage);

function scheduleLoadJobs() {
  if (wsRefreshTimer) window.clearTimeout(wsRefreshTimer);
  wsRefreshTimer = window.setTimeout(() => {
    wsRefreshTimer = null;
    loadJobs();
  }, 200);
}

function handleWsMessage(data: any) {
  if (data.type === 'cache_updated' || data.type === 'rebuild_done') {
    scheduleLoadJobs();
  }
}

async function loadJobs(options: { showError?: boolean; manual?: boolean; initial?: boolean } = {}) {
  if (options.manual && manualRefreshing.value) return;
  if (options.initial) initialLoading.value = true;
  if (options.manual) manualRefreshing.value = true;
  try {
    const data = await api.get('/history-sync/jobs') as any;
    jobs.value = data.jobs || [];
  } catch (e: any) {
    if (options.showError) ui.error(e.message || '加载同步状态失败');
  } finally {
    if (options.initial) initialLoading.value = false;
    if (options.manual) manualRefreshing.value = false;
  }
}

async function refreshSync(item: HistorySyncItem) {
  const ok = await ui.showConfirm({
    title: '刷新同步',
    message: `确定要补全同步 ${item.email} 的历史邮件吗？本地已有邮件和附件会复用，不会重复下载。`,
    confirmText: '确认刷新同步',
  });
  if (!ok) return;
  try {
    const data = await api.post(`/history-sync/jobs/${item.account_id}/start`) as any;
    if (!data?.success) {
      ui.error(data?.message || '刷新同步失败');
      return;
    }
    ui.success('已开始刷新同步');
    await loadJobs();
  } catch (e: any) {
    ui.error(e.message || '刷新同步失败');
  }
}

async function pauseJob(accountId: string) {
  try {
    await api.post(`/history-sync/jobs/${accountId}/pause`);
    ui.success('已暂停同步');
    await loadJobs();
  } catch (e: any) {
    ui.error(e.message || '暂停失败');
  }
}

async function resumeJob(accountId: string) {
  try {
    await api.post(`/history-sync/jobs/${accountId}/resume`);
    ui.success('已继续同步');
    await loadJobs();
  } catch (e: any) {
    ui.error(e.message || '继续失败');
  }
}

async function retryJob(accountId: string) {
  try {
    const data = await api.post(`/history-sync/jobs/${accountId}/retry`) as any;
    if (!data?.success) {
      ui.error(data?.message || '重试失败');
      return;
    }
    ui.success('已从失败断点重试');
    await loadJobs();
  } catch (e: any) {
    ui.error(e.message || '重试失败');
  }
}

async function clearJob(item: HistorySyncItem) {
  const ok = await ui.showConfirm({
    title: '清空本地缓存',
    message: `确定要清空 ${item.email} 的本地邮件、附件和图片缓存吗？这个过程会在后台执行。`,
    confirmText: '确认清空',
    danger: true,
  });
  if (!ok) return;
  try {
    const data = await api.post(`/history-sync/jobs/${item.account_id}/clear`) as any;
    if (!data?.success) {
      ui.error(data?.message || '启动清空失败');
      return;
    }
    ui.success('已开始后台清空');
    await loadJobs();
  } catch (e: any) {
    ui.error(e.message || '启动清空失败');
  }
}

function isFullSyncActive(item: HistorySyncItem) {
  return item.status === 'pending' || item.status === 'running';
}

function isClearActive(job?: HistorySyncJob | null) {
  return Boolean(job && (job.status === 'pending' || job.status === 'running'));
}

function hasActiveJobs() {
  return jobs.value.some((item) => isFullSyncActive(item) || isClearActive(item.clear_job));
}

function canPause(status: string) {
  return status === 'pending' || status === 'running';
}

function canResume(status: string) {
  return status === 'paused';
}

function canRetry(status: string) {
  return status === 'failed';
}

function statusClass(status: string) {
  return `status-${status || 'idle'}`;
}

function statusText(status: string, jobType = 'history_sync') {
  const textMap: Record<string, string> = {
    idle: '未开始',
    pending: '等待中',
    running: jobType === 'clear_cache' || jobType.startsWith('folder_clear') ? '清理中' : '同步中',
    paused: '已暂停',
    completed: '已完成',
    failed: '失败',
  };
  return textMap[status] || status || '未开始';
}

function folderProgress(item: HistorySyncItem) {
  return item.folder_progress || [];
}

function syncedMessageCount(item: HistorySyncItem) {
  return folderProgress(item).reduce((sum, folder) => sum + (folder.cached_count || 0), 0);
}

function totalMessageCount(item: HistorySyncItem) {
  return folderProgress(item).reduce((sum, folder) => sum + (folder.total_count || 0), 0);
}

function formatTime(timestamp?: number) {
  if (!timestamp) return '--';
  return new Date(timestamp * 1000).toLocaleString();
}

onMounted(async () => {
  await loadJobs({ initial: true, showError: true });
  connectWs();
  pollTimer = window.setInterval(() => {
    if (hasActiveJobs()) loadJobs();
  }, 3000);
});

onBeforeUnmount(() => {
  disconnectWs();
  if (pollTimer) window.clearInterval(pollTimer);
  if (wsRefreshTimer) window.clearTimeout(wsRefreshTimer);
});
</script>

<style scoped>
.history-sync-page {
  flex: 1;
  width: 100%;
  height: 100%;
  min-height: 0;
  min-width: 0;
  overflow-y: auto;
  padding: var(--space-6);
  background: var(--bg-secondary);
}

.page-header,
.job-header {
  display: flex;
  justify-content: space-between;
  gap: var(--space-4);
  align-items: flex-start;
}

.page-header { margin-bottom: var(--space-6); }

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

.loading-state { gap: var(--space-3); }

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
  border-radius: 8px;
  padding: var(--space-4);
  box-shadow: var(--shadow-sm);
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
  margin-top: var(--space-4);
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-2);
}

.progress-item {
  padding: var(--space-3);
  border-radius: 8px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-secondary);
}

.progress-summary {
  background: rgba(59, 130, 246, 0.08);
  border-color: rgba(59, 130, 246, 0.18);
}

.progress-label {
  display: block;
  color: var(--text-secondary);
  font-size: 12px;
  margin-bottom: 4px;
}

.progress-value {
  color: var(--text-primary);
  font-size: 18px;
  font-weight: 700;
  line-height: 1.25;
}

.time-row,
.clear-job-row {
  display: flex;
  gap: var(--space-5);
  flex-wrap: wrap;
  margin-top: var(--space-4);
  color: var(--text-secondary);
  font-size: 14px;
}

.clear-job-row {
  justify-content: space-between;
  padding: var(--space-3) var(--space-4);
  border-radius: 12px;
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.14);
  color: var(--text-primary);
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
  0%, 100% { opacity: 0.35; transform: scale(0.85); }
  50% { opacity: 1; transform: scale(1); }
}

@media (max-width: 1080px) {
  .progress-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 720px) {
  .history-sync-page { padding: var(--space-4); }
  .page-header,
  .job-header { flex-direction: column; }
  .job-actions { width: 100%; justify-content: flex-start; }
  .progress-grid { grid-template-columns: 1fr; }
}
</style>
