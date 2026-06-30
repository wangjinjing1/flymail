<template>
  <div class="mail-view">
    <!-- 多账号 Tab 切换 -->
    <div v-if="mailStore.accounts.length > 1" class="account-tabs">
      <div v-for="acc in mailStore.accounts" :key="acc.id" class="account-tab-wrapper">
        <button
          class="account-tab"
          :class="{ active: mailStore.currentAccountId === acc.id }"
          @click="switchAccount(acc.id)"
        >
          <span class="account-icon" :class="acc.provider" v-html="providerIcon(acc.provider)"></span>
          <span class="account-email">{{ acc.email }}</span>
        </button>
        <button v-if="mailStore.reauthAccountIds.has(acc.id)" class="btn-reauth" @click.stop="reauthorize(acc.id)" title="重新授权">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
        </button>
      </div>
    </div>

    <!-- 单账号重新授权提示（多账号时 Tab 栏已有按钮，此处仅单账号显示） -->
    <div v-if="mailStore.accounts.length === 1 && mailStore.reauthAccountIds.has(mailStore.currentAccountId)" class="reauth-banner">
      <span>账号授权已过期</span>
      <button class="btn btn-primary btn-sm" @click="reauthorize(mailStore.currentAccountId)">重新授权</button>
    </div>

    <!-- 邮件列表视图 -->
    <div v-if="!selectedMessage" class="mail-list">
      <!-- 普通模式工具栏 -->
      <div v-if="!selectMode" class="list-toolbar">
        <div class="toolbar-left">
          <!-- 多选图标按钮 -->
          <button class="btn-icon" @click="enterSelectMode()" title="多选">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
            </svg>
          </button>
          <!-- 移动端：iOS风格文件夹选择器 -->
          <button v-if="isMobile" class="folder-picker" @click="showFolderSheet = true">
            <span class="picker-label">{{ mailStore.currentFolderName }}</span>
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="6 9 12 15 18 9"/></svg>
          </button>
          <!-- 桌面端：文件夹名+数量 -->
          <span v-else class="list-count">{{ mailStore.currentFolderName }} · {{ totalMessages }}封</span>
          <!-- 筛选按钮 -->
          <span class="toolbar-divider"></span>
          <button class="filter-btn" :class="{ active: readFilter === '' && !attachmentFilter }" @click="setReadFilter('')">全部 {{ filterCounts.all }}</button>
          <button class="filter-btn" :class="{ active: readFilter === 'unread' }" @click="setReadFilter('unread')">未读 {{ filterCounts.unread }}</button>
          <button class="filter-btn" :class="{ active: readFilter === 'read' }" @click="setReadFilter('read')">已读 {{ filterCounts.read }}</button>
          <button class="filter-btn" :class="{ active: attachmentFilter }" @click="setAttachmentFilter()">附件 {{ filterCounts.attachments }}</button>
        </div>
        <div class="toolbar-right">
          <!-- 移动端：筛选展开/收起按钮 -->
          <button class="btn-icon mobile-filter-toggle" :class="{ active: hasActiveFilter }" @click="showMobileFilters = !showMobileFilters">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>
          </button>
          <button class="btn-icon rebuild-btn" @click="rebuildSync" title="数据缓存不准确，可尝试清空缓存同步" :disabled="rebuilding">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
            </svg>
          </button>
          <span v-if="rebuilding" class="sync-badge">{{ syncProgress || '同步中' }}</span>
          <span class="sync-status" :class="{ connected: wsConnected }" :title="wsConnected ? '实时同步已连接' : '实时同步未连接'">
            <span class="status-dot"></span>
          </span>
        </div>
      <!-- 移动端：筛选下拉菜单（右侧紧凑面板） -->
      <transition name="filter-dropdown">
        <div v-if="showMobileFilters" class="mobile-filter-dropdown">
          <div class="filter-backdrop" @click="showMobileFilters = false"></div>
          <div class="filter-dropdown-menu filter-dropdown-compact">
            <button class="filter-dropdown-item" :class="{ active: readFilter === '' && !attachmentFilter }" @click="setReadFilter(''); showMobileFilters = false">全部 {{ filterCounts.all }}</button>
            <button class="filter-dropdown-item" :class="{ active: readFilter === 'unread' }" @click="setReadFilter('unread'); showMobileFilters = false">未读 {{ filterCounts.unread }}</button>
            <button class="filter-dropdown-item" :class="{ active: readFilter === 'read' }" @click="setReadFilter('read'); showMobileFilters = false">已读 {{ filterCounts.read }}</button>
            <button class="filter-dropdown-item" :class="{ active: attachmentFilter }" @click="setAttachmentFilter(); showMobileFilters = false">附件 {{ filterCounts.attachments }}</button>
          </div>
        </div>
      </transition>
      </div>

      <!-- 多选模式工具栏（用 template v-else 包裹，保持与 v-if 相邻） -->
      <template v-else>
        <div class="select-toolbar">
        <button class="select-btn" @click="exitSelectMode">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
        <span class="select-info">已选 {{ selectedIds.size }} 封</span>
        <div class="select-actions">
          <button class="select-btn" @click="toggleSelectAll" :title="isAllSelected ? '取消全选' : '全选'">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
            </svg>
          </button>
          <button class="select-btn mark-read" @click="batchMarkRead" :disabled="selectedIds.size === 0" title="标记已读">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/>
            </svg>
          </button>
          <button class="select-btn delete" @click="batchDelete" :disabled="selectedIds.size === 0" title="删除选中">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
            </svg>
          </button>
        </div>
        </div>
      </template>

      <!-- iOS风格底部弹出文件夹选择 -->
      <transition name="sheet">
        <div v-if="showFolderSheet" class="sheet-backdrop" @click.self="showFolderSheet = false">
          <div class="sheet-content">
            <div class="sheet-handle"></div>
            <div class="sheet-title">文件夹</div>
            <div class="sheet-list">
              <button
                v-for="folder in mailStore.folders"
                :key="folder.path"
                class="sheet-item"
                :class="{ active: mailStore.currentFolder === folder.path }"
                @click="mailStore.setFolder(folder.path); showFolderSheet = false"
              >
                <span class="sheet-folder-name">{{ mailStore.folderDisplayName(folder.name) }}</span>
                <span class="sheet-folder-count" v-if="getFolderCount(folder)">{{ getFolderCount(folder) }}</span>
                <svg v-if="mailStore.currentFolder === folder.path" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--accent-blue)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
              </button>
            </div>
          </div>
        </div>
      </transition>

      <!-- 加载中（首次加载无缓存数据时显示） -->
      <div v-if="loading && messages.length === 0" class="list-loading">
        <div class="spinner"></div>
        <span>加载中...</span>
      </div>

      <!-- 空状态 -->
      <div v-else-if="!loading && messages.length === 0" class="list-empty">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" opacity="0.3">
          <rect x="2" y="4" width="20" height="16" rx="2"/><path d="M22 4L12 13L2 4"/>
        </svg>
        <span>暂无邮件</span>
      </div>

      <!-- 邮件列表 -->
      <div v-else class="list-items">
        <button
          v-for="msg in messages"
          :key="msg.id"
          class="mail-item"
          :class="{ unread: !msg.is_read, selected: selectMode && selectedIds.has(msg.id) }"
          @click="selectMode ? toggleSelect(msg.id) : selectMessage(msg)"
          @mouseenter="prefetchMessage(msg)"
          @contextmenu.prevent="enterSelectMode(msg.id)"
        >
          <!-- 多选模式下的勾选框 -->
          <div v-if="selectMode" class="check-circle" :class="{ checked: selectedIds.has(msg.id) }">
            <svg v-if="selectedIds.has(msg.id)" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
          </div>
          <!-- 左列：头像 + 发件人 -->
          <div class="mail-sender">
            <div class="mail-avatar" :style="{ background: getAvatarColor(msg.from_addr) }">
              {{ getInitial(msg.from_addr) }}
            </div>
            <span class="mail-from">{{ extractName(msg.from_addr) }}</span>
          </div>
          <!-- 中列：状态图标 + 主题 + 附件 + 日期 -->
          <div class="mail-info">
            <div class="mail-main-row">
              <!-- 中列：已读/未读图标 + 主题 + 附件 -->
              <svg v-if="!msg.is_read" class="mail-status-icon unread-icon" width="16" height="16" viewBox="0 0 24 24"><path fill="currentColor" d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/></svg>
              <svg v-else class="mail-status-icon read-icon" width="16" height="16" viewBox="0 0 1024 1024" fill="currentColor"><path d="M461.816 79.279c30.333-20.364 69.97-20.373 100.311-0.021l384.19 257.69c9.256 6.208 13.947 16.672 13.216 27.044 0.108 1.548 0.096 3.1-0.034 4.64 0.33 1.778 0.501 3.61 0.501 5.483v495.903C960 919.714 919.706 960 870 960H154c-49.706 0-90-40.286-90-89.982V374.115c0-2.663 0.347-5.245 0.999-7.704-0.004-0.803 0.025-1.608 0.086-2.412-0.804-10.432 3.883-20.985 13.191-27.234z m70.259 519.057c-11.417-10.283-28.76-10.278-40.171 0.012L157.358 900.01h709.674zM124 425.237v424.071L381.796 616.85 124 425.237z m776 0.224L642.268 616.842 900 848.964V425.461zM528.7 129.074a30.005 30.005 0 0 0-33.437 0.007L143.678 365.114l283.558 210.762 24.483-22.075c33.891-30.56 85.223-30.88 119.48-0.952l1.034 0.916 24.56 22.121 283.833-210.763z"/></svg>
              <span class="mail-subject">{{ msg.subject || '(无主题)' }}</span>
              <!-- 附件图标 -->
              <svg v-if="msg.has_attachments" class="att-badge" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg>
            </div>
          </div>
          <!-- 已读/未读标签 -->
          <span class="mail-status-tag" :class="msg.is_read ? 'read' : 'unread'">
            {{ msg.is_read ? '已读' : '未读' }}
          </span>
          <!-- 右列：日期（独立固定宽度列，保证最右侧对齐） -->
          <span class="mail-date">{{ formatDate(msg.date) }}</span>
        </button>
      </div>

      <div v-if="!selectMode && totalPages > 1" class="pagination">
        <button class="page-btn" :disabled="currentPage <= 1" @click="goPage(currentPage - 1)">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
        </button>
        <template v-for="p in pageNumbers" :key="p">
          <span v-if="p === '...'" class="page-ellipsis">...</span>
          <button v-else class="page-btn" :class="{ active: p === currentPage }" @click="goPage(p as number)">{{ p }}</button>
        </template>
        <button class="page-btn" :disabled="currentPage >= totalPages" @click="goPage(currentPage + 1)">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
        </button>
      </div>
    </div>

    <!-- 邮件详情视图（支持左滑返回） -->
    <div v-else class="mail-detail"
         @touchstart="onDetailTouchStart"
         @touchmove="onDetailTouchMove"
         @touchend="onDetailTouchEnd">
      <div class="detail-toolbar">
        <button class="btn-back" @click="backToList">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="15 18 9 12 15 6"/>
          </svg>
          <span>返回</span>
        </button>
        <div class="detail-actions">
          <button class="btn-action" @click="replyMessage" title="回复邮件">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/>
            </svg>
            <span>回复</span>
          </button>
          <button class="btn-action" @click="forwardMessage" title="转发邮件">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="15 17 20 12 15 7"/><path d="M4 18v-2a4 4 0 0 1 4-4h12"/>
            </svg>
            <span>转发</span>
          </button>
          <button class="btn-action" :class="{ confirm: deleteConfirm }" @click="onDeleteMessage" :title="deleteConfirm ? '再次点击确认删除' : '删除邮件'">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
            </svg>
            <span v-if="deleteConfirm">确认删除</span>
          </button>
        </div>
      </div>

      <!-- 标题+正文+附件全部在一个滚动区域内 -->
      <div class="detail-body">
        <div class="detail-header">
          <h2 class="detail-subject">{{ selectedMessage.subject || '(无主题)' }}</h2>
          <div class="detail-meta">
            <div class="meta-avatar" :style="{ background: getAvatarColor(selectedMessage.from_addr) }">
              {{ getInitial(selectedMessage.from_addr) }}
            </div>
            <div class="meta-info">
              <div class="meta-from">{{ selectedMessage.from_addr }}</div>
              <div class="meta-date">{{ formatDetailDate(selectedMessage.date) }}</div>
            </div>
          </div>
        </div>

        <div v-if="selectedMessage.body_html || selectedMessage.body_text" v-html="sanitizeHtml(selectedMessage.body_html) || selectedMessage.body_text" class="detail-content"></div>
        <!-- 正文加载中：显示骨架屏 -->
        <div v-else class="body-skeleton">
          <div class="skeleton-line" style="width: 90%"></div>
          <div class="skeleton-line" style="width: 100%"></div>
          <div class="skeleton-line" style="width: 75%"></div>
          <div class="skeleton-line" style="width: 95%"></div>
          <div class="skeleton-line" style="width: 60%"></div>
          <div class="skeleton-line" style="width: 85%"></div>
          <div class="skeleton-line" style="width: 100%"></div>
          <div class="skeleton-line" style="width: 40%"></div>
        </div>

        <!-- 附件列表（放在正文后面，随正文一起滚动） -->
        <div class="attachment-list" v-if="selectedMessage.attachments && selectedMessage.attachments.length > 0">
          <div class="attachment-header">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg>
            <span>附件 ({{ selectedMessage.attachments.length }})</span>
          </div>
          <div class="attachment-item" v-for="att in selectedMessage.attachments" :key="att.part_number" @click="downloadAttachment(att)">
            <div class="att-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            </div>
            <div class="att-info">
              <div class="att-name">{{ att.filename || '未命名附件' }}</div>
              <div class="att-meta">{{ formatFileSize(att.size) }}</div>
            </div>
            <div class="att-download">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue';
import { useMailStore } from '../stores/mail';
import { useUIStore } from '../stores/ui';
import api from '../utils/api';
import { providerIcon } from '../utils/provider';
import { sanitizeHtml } from '../utils/sanitize';
import { extractName, getInitial, getAvatarColor, formatDate, formatDetailDate, formatFileSize, downloadAttachment as downloadAttachmentFile, getFolderCount } from '../utils/mail-helpers';
import type { Attachment, Message } from '../types/mail';
import { useWebSocket } from '../composables/useWebSocket';
import { useSelectMode } from '../composables/useSelectMode';
import { useConfirmAction } from '../composables/useConfirmAction';

const mailStore = useMailStore();
const uiStore = useUIStore();

const messages = ref<Message[]>([]);
const selectedMessage = ref<Message | null>(null);
const loading = ref(false);
const totalMessages = ref(0);
const currentPage = ref(1);
const pageSize = 40;
const readFilter = ref('');
const attachmentFilter = ref(false);
const filterCounts = ref({ all: 0, unread: 0, read: 0, attachments: 0 });
const showMobileFilters = ref(false);
const hasActiveFilter = computed(() => readFilter.value !== '' || attachmentFilter.value);
const syncing = ref(false);
const rebuilding = ref(false);
const syncProgress = ref('');

interface MessagePageCache {
  messages: Message[];
  total: number;
  unreadTotal: number;
}

// 前端内存页缓存：账号 + 文件夹 + 页码 + 页大小作为 key，切换分类时先秒显旧数据，再后台刷新。
const pageCache = new Map<string, MessagePageCache>();
const totalPages = computed(() => Math.max(1, Math.ceil(totalMessages.value / pageSize)));

/** 生成分页页码数组（含省略号） */
const pageNumbers = computed(() => {
  const total = totalPages.value;
  const current = currentPage.value;
  // 总页数 <= 7 时全部显示
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }
  // 总页数 > 7 时，显示：1 ... 当前附近 ... 末页
  const pages: (number | string)[] = [1];
  if (current > 3) pages.push('...');
  const start = Math.max(2, current - 1);
  const end = Math.min(total - 1, current + 1);
  for (let i = start; i <= end; i++) pages.push(i);
  if (current < total - 2) pages.push('...');
  pages.push(total);
  return pages;
});

/** 跳转到指定页 */
function goPage(page: number) {
  if (page < 1 || page > totalPages.value || page === currentPage.value) return;
  currentPage.value = page;
  loadMessages();
}

// ==================== 筛选 ====================

function setReadFilter(filter: string) {
  readFilter.value = filter;
  attachmentFilter.value = false;
  currentPage.value = 1;
  pageCache.clear();
  loadMessages();
}

function setAttachmentFilter() {
  attachmentFilter.value = !attachmentFilter.value;
  if (attachmentFilter.value) {
    readFilter.value = '';
  }
  currentPage.value = 1;
  pageCache.clear();
  loadMessages();
}

// 移动端检测（防抖 + 组件卸载时清理事件监听）
const isMobile = ref(window.innerWidth <= 768);
let resizeTimer: ReturnType<typeof setTimeout> | null = null;
const onResize = () => {
  if (resizeTimer) clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { isMobile.value = window.innerWidth <= 768; }, 150);
};
window.addEventListener('resize', onResize);

// iOS风格文件夹弹出层
const showFolderSheet = ref(false);

// 多选模式（使用 composable，全选范围限定为当前页）
const { selectMode, selectedIds, isAllSelected, enterSelectMode, exitSelectMode, toggleSelect, toggleSelectAll } = useSelectMode(() => messages.value.map(m => m.id));

/** 批量删除 */
async function batchDelete() {
  if (selectedIds.value.size === 0) return;
  const count = selectedIds.value.size;
  uiStore.success(`正在删除 ${count} 封邮件...`);
  try {
    await api.post('/messages/batch-delete', {
      message_ids: [...selectedIds.value],
      account_id: mailStore.currentAccountId,
      folder: mailStore.currentFolder,
    });
    exitSelectMode();
    pageCache.clear();
    await loadMessages();
    uiStore.success(`已删除 ${count} 封邮件`);
  } catch (e) {
    console.error('批量删除失败:', e);
    uiStore.error('批量删除失败');
  }
}

/** 批量标记已读 */
async function batchMarkRead() {
  if (selectedIds.value.size === 0) return;
  const count = selectedIds.value.size;
  uiStore.success(`正在标记 ${count} 封邮件...`);
  try {
    await api.post('/messages/batch-mark-read', {
      message_ids: [...selectedIds.value],
      account_id: mailStore.currentAccountId,
      folder: mailStore.currentFolder,
    });
    // 更新本地邮件列表中已选邮件的已读状态
    messages.value = messages.value.map(m =>
      selectedIds.value.has(m.id) ? { ...m, is_read: true } : m
    );
    // 更新侧边栏未读数
    for (let i = 0; i < count; i++) {
      mailStore.decrementUnreadCount(mailStore.currentFolder);
    }
    exitSelectMode();
    uiStore.success(`已标记 ${count} 封邮件为已读`);
  } catch (e) {
    console.error('批量标记已读失败:', e);
    uiStore.error('标记已读失败');
  }
}

// 请求版本号：防止切换账号时旧请求的响应覆盖新数据
let loadVersion = 0;

// WebSocket 实时同步（使用 composable）
const { wsConnected, connect: connectWs, disconnect: disconnectWs } = useWebSocket(handleWsMessage)

/** 处理 WebSocket 业务消息 */
function handleWsMessage(data: any) {
  if (data.type === 'new_mail') {
    // IDLE 检测到新邮件：只弹通知，不刷新列表（缓存还没同步完，刷新会读到旧数据）
    if (data.provider && data.email) {
      mailStore.addNotification(data.provider, data.email, data.folder || 'INBOX', data.notification_id);
    }
    // 只刷新侧边栏未读计数（STATUS 命令是即时的，不需要等缓存同步）
    if (!data.account_id || data.account_id === mailStore.currentAccountId) {
      mailStore.loadFolderCounts();
    }
  } else if (data.type === 'cache_updated') {
    // 缓存同步完成：刷新列表和计数（此时缓存已有新邮件数据）
    if (!data.account_id || data.account_id === mailStore.currentAccountId) {
      if (!data.folder || data.folder === mailStore.currentFolder) {
        pageCache.clear();
        loadMessages();
      }
      mailStore.loadFolderCounts();
    }
  } else if (data.type === 'rebuild_done') {
    // 重建同步完成：后端广播，前端静默刷新列表和计数，结束同步中状态
    if (!data.account_id || data.account_id === mailStore.currentAccountId) {
      pageCache.clear();
      mailStore.loadFolderCounts();
      loadMessages();
      rebuilding.value = false;
      syncing.value = false;
      syncProgress.value = '';
      if (data.error) {
        uiStore.error('重建同步失败: ' + data.error);
      }
    }
  } else if (data.type === 'schedule_success' || data.type === 'schedule_failed') {
    // 定时发送结果通知
    mailStore.addNotification(
      data.provider || '', data.email || '', '', data.notification_id,
      data.type, data.message || ''
    );
    // 发送成功时刷新侧边栏计数（已发送文件夹可能有新邮件）
    if (data.type === 'schedule_success') {
      mailStore.loadFolderCounts();
    }
  } else if (data.type === 'connection_status') {
    // 账号连接状态变化
    if (data.account_id === mailStore.currentAccountId) {
      if (data.status === 'connected') {
        // 连接恢复，自动重试加载数据（替代 30 秒 setTimeout 轮询）
        if (rebuilding.value) return; // 重建同步中不干扰
        mailStore.reauthAccountIds.delete(data.account_id);
        pageCache.clear();
        loadMessages();
        mailStore.loadFolderCounts();
      }
    }
    // 任何账号的 reauth_needed 都记录（不限于当前账号）
    if (data.status === 'reauth_needed' && data.account_id) {
      mailStore.reauthAccountIds.add(data.account_id);
    }
  } else if (data.type === 'sync_progress') {
    // 同步进度更新
    if (data.account_id === mailStore.currentAccountId && rebuilding.value) {
      syncProgress.value = `同步中 (${data.completed}/${data.total})`;
    }
  } else if (data.type === 'message_state_changed') {
    // 跨标签页邮件状态同步
    if (data.account_id === mailStore.currentAccountId) {
      if (data.action === 'mark_read' || data.action === 'mark_unread') {
        const isRead = data.action === 'mark_read';
        for (const uid of data.uids) {
          const msg = messages.value.find(m => String(m.id) === String(uid));
          if (msg) msg.is_read = isRead;
        }
        mailStore.loadFolderCounts();
      } else if (data.action === 'delete' || data.action === 'move') {
        messages.value = messages.value.filter(m => !data.uids.includes(String(m.id)));
        totalMessages.value = Math.max(0, totalMessages.value - data.uids.length);
        mailStore.loadFolderCounts();
      }
    }
  }
}

function getPageCacheKey() {
  return `${mailStore.currentAccountId}::${mailStore.currentFolder}::${currentPage.value}::${pageSize}`;
}

function applyCachedPage(cache: MessagePageCache) {
  messages.value = cache.messages;
  totalMessages.value = cache.total;
  mailStore.updateFolderCounts(mailStore.currentFolder, cache.total, cache.unreadTotal);
}

/** 将当前页数据保存到内存缓存 */
function saveCurrentPageCache(data: any) {
  pageCache.set(getPageCacheKey(), {
    messages: data.messages || [],
    total: data.total || 0,
    unreadTotal: data.unread_total || 0,
  });
}

// 监听文件夹和账号变化，重新加载邮件
// 切换时立即清空列表，避免请求旧 UID 导致 404
watch(
  () => [mailStore.currentFolder, mailStore.currentAccountId],
  () => {
    messages.value = [];
    selectedMessage.value = null;
    currentPage.value = 1;
    readFilter.value = '';
    attachmentFilter.value = false;
    pageCache.clear();
    loadMessages();
  }
);

onMounted(() => {
  loadMessages();
  connectWs();
});

onUnmounted(() => {
  disconnectWs();
  // 清理 resize 事件监听，防止内存泄漏
  window.removeEventListener('resize', onResize);
  if (resizeTimer) { clearTimeout(resizeTimer); resizeTimer = null; }
});

/** 切换账号 */
async function switchAccount(id: string) {
  mailStore.setAccount(id);
  await mailStore.loadFolders();
}

/** 重新授权指定账号（复用添加账号的 OAuth 流程） */
async function reauthorize(accountId?: string) {
  try {
    const targetId = accountId || mailStore.currentAccountId;
    const targetAccount = mailStore.accounts.find((a: any) => a.id === targetId);
    if (!targetAccount) return;
    const provider = targetAccount.provider;
    const settingsData = await api.get('/settings') as any;
    const settings = settingsData.settings || {};
    let redirectUri = '';
    if (provider === 'outlook') {
      redirectUri = settings.outlook_redirect_uri || '';
      if (!redirectUri) { uiStore.error('请先在设置页面配置 Microsoft 重定向 URI'); return; }
    } else {
      redirectUri = settings.gmail_redirect_uri || '';
      if (!redirectUri) { uiStore.error('请先在设置页面配置 Gmail 重定向 URI'); return; }
    }
    // 标记这是重新授权，OAuth 回调后不跳转到账号页
    sessionStorage.setItem('flymail_oauth_reauth', '1');
    const data = await api.post('/accounts/auth-url', { provider, redirect_uri: redirectUri }) as any;
    if (data.error) { uiStore.error('获取授权链接失败：' + data.error); return; }
    if (data.auth_url) { window.open(data.auth_url, '_blank'); }
    else { uiStore.error('获取授权链接失败'); }
  } catch (e: any) {
    uiStore.error('重新授权失败：' + (e.response?.data?.error || e.message || '网络错误'));
  }
}

// 删除确认（使用 composable，两次点击确认机制）
const { confirmTarget: deleteConfirm, requestConfirm: onDeleteConfirm } = useConfirmAction()

/** 删除邮件（两次点击确认机制） */
async function onDeleteMessage() {
  if (!selectedMessage.value) return;
  // 第一次点击进入确认状态，第二次点击执行删除
  if (!onDeleteConfirm(selectedMessage.value.id)) return;

  try {
    const params: Record<string, string> = { folder: mailStore.currentFolder };
    if (mailStore.currentAccountId) params.account_id = mailStore.currentAccountId;
    await api.delete(`/messages/${selectedMessage.value!.id}`, { params });
    selectedMessage.value = null;
    // 清除所有页缓存，避免翻页时显示旧的缓存数据
    pageCache.clear();
    // 删除后重新加载当前页，让后端返回正确的分页数据（自动补充新邮件）
    await loadMessages();
  } catch (e) {
    console.error('删除邮件失败:', e);
    uiStore.error('删除邮件失败');
  }
}

/** 加载邮件列表（带竞态保护：只接受最新请求的结果） */
async function loadMessages() {
  const cachedPage = pageCache.get(getPageCacheKey());
  if (cachedPage) {
    applyCachedPage(cachedPage);
  }

  // 有当前页缓存时直接显示缓存并后台刷新；没有任何内容时才显示加载中。
  const showLoading = !cachedPage && messages.value.length === 0;
  loading.value = showLoading;
  syncing.value = showLoading;
  const version = ++loadVersion;
  try {
    const params: Record<string, string | number> = {
      folder: mailStore.currentFolder,
      page: currentPage.value,
      page_size: pageSize,
    };
    if (mailStore.currentAccountId) params.account_id = mailStore.currentAccountId;
    if (readFilter.value) params.read_filter = readFilter.value;
    if (attachmentFilter.value) params.attachment_filter = 'true';
    const data = await api.get('/messages', { params }) as any;
    // 只接受最新版本的结果，丢弃旧请求的响应
    if (version !== loadVersion) return;
    // Outlook 连接异常时，后端返回 reconnecting: true，前端展示友好提示
    if (data.reconnecting) {
      uiStore.error('邮箱连接异常，正在重新连接，请稍后再试');
      return;
    }
    saveCurrentPageCache(data);
    messages.value = data.messages || [];
    totalMessages.value = data.total || 0;
    // 更新筛选计数
    if (data.filter_counts) {
      filterCounts.value = data.filter_counts;
    }
    // 用 list_messages API 返回的数据更新侧边栏文件夹计数
    // 收件箱显示未读数，其他文件夹显示邮件总数
    mailStore.updateFolderCounts(
      mailStore.currentFolder,
      data.total || 0,
      data.unread_total || 0,
    );
  } catch (e) {
    if (version !== loadVersion) return;
    console.error('加载邮件失败:', e);
    uiStore.error('加载邮件失败');
  } finally {
    if (version === loadVersion) {
      loading.value = false;
      // 重建同步期间不重置 syncing（由 rebuild_done WebSocket 消息控制）
      if (!rebuilding.value) syncing.value = false;
      // 列表加载完成后，后台批量预取当前页邮件正文
      nextTick(() => { prefetchVisibleMessages(); });
    }
  }
}

/** 重建同步：清空当前账号缓存并重新拉取 */
async function rebuildSync() {
  if (rebuilding.value) return;

  const accountId = mailStore.currentAccountId;
  if (!accountId) return;

  // 使用项目统一的确认弹窗
  const ok = await uiStore.showConfirm({
    title: '清空缓存同步',
    message: '数据缓存不准确，可尝试清空缓存同步。将清空当前账号的本地缓存并重新从邮箱拉取所有邮件。',
    confirmText: '清空并同步',
    danger: true,
  });
  if (!ok) return;

  rebuilding.value = true;
  syncing.value = true;
  syncProgress.value = '同步中';
  try {
    await api.post(`/accounts/${accountId}/rebuild-sync`);
    // 后端立即返回，同步在后台执行。清空前端缓存并刷新文件夹
    pageCache.clear();
    await mailStore.loadFolders();
    // 保持 syncing 状态（按钮转圈），等待后端 WebSocket 推送 rebuild_done 后自动结束
  } catch (e: any) {
    console.error('重建同步失败:', e);
    uiStore.error('重建同步失败');
    rebuilding.value = false;
    syncing.value = false;
  }
  // 注意：成功时不重置 rebuilding/syncing，由 WebSocket rebuild_done 消息处理
}

/** 选择邮件查看详情（带竞态保护）
 * 用户点击邮件时：
 * 1. 用 BODY.PEEK[] 拉取正文（不自动标已读）
 * 2. 如果邮件未读，调用 STORE +FLAGS \Seen 标记已读（同步到邮箱服务器）
 * 3. 更新本地列表中的已读状态和侧边栏未读数
 */
async function selectMessage(msg: Message) {
  const version = ++loadVersion;

  // 乐观 UI：立即用摘要数据渲染头部，正文留空显示骨架屏
  selectedMessage.value = {
    ...msg,
    body_html: '',
    body_text: '',
    attachments: [],
  };

  try {
    const params: Record<string, string> = { folder: mailStore.currentFolder };
    if (mailStore.currentAccountId) params.account_id = mailStore.currentAccountId;
    const data = await api.get(`/messages/${msg.id}`, { params }) as any;
    if (version !== loadVersion) return;
    // 用完整数据替换（正文填充）
    selectedMessage.value = data;

    // 未读邮件：调用 IMAP STORE +FLAGS \Seen 标记已读，同步到邮箱服务器
    if (!msg.is_read) {
      // 直接修改 messages 数组中对应项的 is_read，确保 Vue 响应式追踪
      const idx = messages.value.findIndex((m: Message) => m.id === msg.id);
      if (idx !== -1) {
        messages.value[idx] = { ...messages.value[idx], is_read: true };
      }
      // 异步调用标记已读API，不阻塞界面
      api.post('/mark-read', {
        message_id: msg.id,
        folder: mailStore.currentFolder,
        account_id: mailStore.currentAccountId || '',
      }).catch((e: any) => console.error('[FlyMail] 标记已读失败:', e));

      // 更新侧边栏未读数（收件箱减1）
      mailStore.decrementUnreadCount(mailStore.currentFolder);
    }
  } catch (e: any) {
    if (version !== loadVersion) return;
    console.error('加载邮件详情失败:', e);
    // Outlook 连接异常时，后端返回 503 + { reconnecting: true }
    const respData = e?.response?.data;
    if (respData?.reconnecting) {
      uiStore.error('邮箱连接异常，正在重新连接，请稍后再试');
    } else {
      uiStore.error('加载邮件详情失败');
    }
  }
}

function backToList() {
  selectedMessage.value = null;
}

// ==================== 左滑返回手势 ====================
let _touchStartX = 0;
let _touchStartY = 0;
let _touchStartTime = 0;

function onDetailTouchStart(e: TouchEvent) {
  _touchStartX = e.touches[0].clientX;
  _touchStartY = e.touches[0].clientY;
  _touchStartTime = Date.now();
}

function onDetailTouchMove(_e: TouchEvent) {
  // 不阻止默认行为，允许正常滚动
}

function onDetailTouchEnd(e: TouchEvent) {
  const dx = e.changedTouches[0].clientX - _touchStartX;
  const dy = Math.abs(e.changedTouches[0].clientY - _touchStartY);
  const dt = Date.now() - _touchStartTime;
  // 左滑条件：水平滑动 > 80px，垂直偏移 < 100px，时间 < 500ms
  if (dx > 80 && dy < 100 && dt < 500) {
    backToList();
  }
}

/** 回复邮件：预填收件人+主题+引用原文，跳转到写邮件 */
function replyMessage() {
  if (!selectedMessage.value) return;
  const msg = selectedMessage.value;
  const replyTo = (msg as any).reply_to || msg.from_addr;
  const subject = msg.subject?.startsWith('Re:') ? msg.subject : `Re: ${msg.subject || ''}`;
  const quoteHtml = `<br><br><blockquote style="border-left:3px solid #ccc;padding-left:10px;color:#666;">${msg.body_html || msg.body_text || ''}</blockquote>`;
  mailStore.setComposeDraft({
    to: [replyTo],
    subject,
    body_html: quoteHtml,
    in_reply_to: msg.id,
    account_id: mailStore.currentAccountId,
  });
  // 通过 App.vue 的 currentView 切换到 compose
  const event = new CustomEvent('flymail-navigate', { detail: 'compose' });
  window.dispatchEvent(event);
}

/** 转发邮件：预填主题+引用原文，收件人留空，跳转到写邮件 */
function forwardMessage() {
  if (!selectedMessage.value) return;
  const msg = selectedMessage.value;
  const subject = msg.subject?.startsWith('Fwd:') ? msg.subject : `Fwd: ${msg.subject || ''}`;
  const fwdHtml = `<br><br><p>---------- 转发的邮件 ----------</p><p>发件人: ${msg.from_addr}</p><p>主题: ${msg.subject}</p><p>日期: ${msg.date}</p><hr/><div>${msg.body_html || msg.body_text || ''}</div>`;
  mailStore.setComposeDraft({
    to: [],
    subject,
    body_html: fwdHtml,
    account_id: mailStore.currentAccountId,
  });
  const event = new CustomEvent('flymail-navigate', { detail: 'compose' });
  window.dispatchEvent(event);
}

// 悬停预取：鼠标悬停时静默预取邮件正文，点击时大概率已缓存
let _prefetchTimer: ReturnType<typeof setTimeout> | null = null;
function prefetchMessage(msg: Message) {
  if (_prefetchTimer) return; // 防抖：300ms 内只预取一封
  _prefetchTimer = setTimeout(() => { _prefetchTimer = null; }, 300);
  const params: Record<string, string> = { folder: mailStore.currentFolder };
  if (mailStore.currentAccountId) params.account_id = mailStore.currentAccountId;
  api.get(`/messages/${msg.id}`, { params }).catch(() => {});
}

// 列表加载完成后，后台批量预取当前页邮件正文
function prefetchVisibleMessages() {
  const ids = messages.value.slice(0, 10).map((m: Message) => m.id);
  if (ids.length === 0) return;
  api.post('/prefetch-messages', {
    message_ids: ids,
    folder: mailStore.currentFolder,
    account_id: mailStore.currentAccountId || '',
  }).catch(() => {});
}

/** 下载附件（适配器：模板只传 Attachment，补全消息上下文后调用公共工具函数） */
function downloadAttachment(att: Attachment) {
  const msg = selectedMessage.value;
  if (!msg) return;
  downloadAttachmentFile({
    messageId: msg.id,
    accountId: msg.account_id || mailStore.currentAccountId || '',
    folder: msg.folder || 'INBOX',
    partNumber: att.part_number,
    filename: att.filename || 'attachment',
  });
}
</script>

<style scoped>
.mail-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

/* 账号 Tab 切换 */
/* 重新授权提示条 */
.reauth-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  background: #fef3e2;
  border-bottom: 1px solid #f5d0a0;
  font-size: var(--text-sm);
  color: #b45309;
  flex-shrink: 0;
}

:root.dark .reauth-banner {
  background: #3d2e00;
  border-bottom-color: #5a4400;
  color: #fbbf24;
}

.reauth-banner .btn-sm {
  padding: 3px 12px;
  font-size: 12px;
}

.account-tabs {
  display: flex;
  gap: var(--space-1);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-secondary);
  flex-shrink: 0;
}

.account-tab {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 6px 14px;
  border: 1px solid transparent;
  background: transparent;
  border-radius: var(--border-radius-full);
  cursor: pointer;
  transition: all var(--transition-fast);
  color: var(--text-secondary);
  font-size: var(--text-xs);
  font-family: inherit;
  white-space: nowrap;
}

.account-tab:hover {
  background: var(--bg-hover);
}

.account-tab.active {
  background: var(--bg-active);
  border-color: var(--color-accent);
  color: var(--color-accent);
  font-weight: var(--font-medium);
}

.account-tab-wrapper {
  display: flex;
  align-items: center;
  gap: 2px;
}

.btn-reauth {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  border-radius: 50%;
  cursor: pointer;
  color: #e67e22;
  transition: all var(--transition-fast);
  padding: 0;
}

.btn-reauth:hover {
  background: #fef3e2;
  color: #d35400;
}

:root.dark .btn-reauth:hover {
  background: #3d2e00;
}

.account-icon {
  width: 18px;
  height: 18px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  overflow: hidden;
}

.account-icon.qq { background: #fff; }
.account-icon.gmail { background: #fff; }
.account-icon.netease { background: #fff; }
.account-icon.icloud { background: #fff; }

.account-email {
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ==================== 邮件列表 ==================== */
.mail-list {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg-primary);
}

/* 普通模式工具栏 */
.list-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

/* 筛选按钮（工具栏内） */
.filter-btn {
  padding: 3px 10px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--text-tertiary);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.15s;
}
.filter-btn:hover {
  background: var(--bg-hover);
  color: var(--text-secondary);
}
.filter-btn.active {
  background: rgba(0, 122, 255, 0.1);
  color: var(--color-accent);
  font-weight: 500;
}

/* 工具栏分隔线（筛选按钮和同步按钮之间） */
.toolbar-divider {
  width: 1px;
  height: 16px;
  background: var(--border-color);
  flex-shrink: 0;
  margin: 0 4px;
}

/* 图标按钮（多选入口） */
.btn-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
}

.btn-icon:hover {
  background: var(--bg-hover);
  color: var(--accent-blue);
}

.btn-icon:active {
  background: rgba(0, 122, 255, 0.1);
  color: var(--accent-blue);
}

/* 重建同步按钮旋转动画 */
.rebuild-btn:disabled svg {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* 多选模式工具栏 */
.select-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 6px 12px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.select-info {
  flex: 1;
  font-size: var(--text-sm);
  color: var(--text-secondary);
  font-weight: 500;
}

.select-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

/* 多选工具栏图标按钮 */
.select-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s;
}

.select-btn:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.select-btn:active {
  background: var(--bg-active);
}

.select-btn.delete {
  color: #FF3B30;
}

.select-btn.delete:hover {
  background: rgba(255, 59, 48, 0.1);
  color: #FF3B30;
}

.select-btn.delete:disabled {
  color: var(--text-tertiary);
  opacity: 0.4;
  cursor: not-allowed;
}

.select-btn.delete:disabled:hover {
  background: transparent;
}

/* 标记已读按钮 */
.select-btn.mark-read {
  color: var(--accent-blue, #007AFF);
}

.select-btn.mark-read:hover {
  background: rgba(0, 122, 255, 0.1);
  color: var(--accent-blue, #007AFF);
}

.select-btn.mark-read:disabled {
  color: var(--text-tertiary);
  opacity: 0.4;
  cursor: not-allowed;
}

.select-btn.mark-read:disabled:hover {
  background: transparent;
}

/* 实时同步状态指示器 */
.sync-badge {
  font-size: 11px;
  color: var(--accent-blue, #007AFF);
  background: rgba(0, 122, 255, 0.1);
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 500;
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.sync-status {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-tertiary);
  opacity: 0.4;
  transition: all var(--transition-normal);
}

.sync-status.connected .status-dot {
  background: var(--color-success);
  opacity: 1;
  box-shadow: 0 0 4px rgba(52, 199, 89, 0.4);
}

.list-count {
  font-size: var(--text-xs);
  color: var(--text-tertiary);
  font-weight: var(--font-medium);
}

.list-loading,
.list-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  color: var(--text-tertiary);
  font-size: var(--text-sm);
}

.spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--border-color);
  border-top-color: var(--color-accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* 邮件列表项 */
.list-items {
  flex: 1;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}

.mail-item {
  display: flex;
  align-items: center;
  gap: 0;
  padding: 10px 16px;
  border: none;
  background: transparent;
  border-bottom: 1px solid var(--border-color);
  cursor: pointer;
  transition: background var(--transition-fast);
  width: 100%;
  text-align: left;
  font-family: inherit;
  min-height: 52px;
}

/* 左列：头像 + 发件人（固定宽度，保证各行对齐） */
.mail-sender {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-shrink: 0;
  width: 160px;
  min-width: 0;
  padding-right: 12px;
}

.mail-item:hover {
  background: var(--bg-hover);
}

.mail-item.unread .mail-from {
  font-weight: var(--font-semibold);
  color: var(--text-primary);
}

.mail-item.unread .mail-subject {
  color: var(--text-primary);
  font-weight: var(--font-medium);
}

/* 中列：状态图标 + 主题 + 日期（弹性宽度，自动填充剩余空间） */
.mail-main-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

/* 已读/未读邮件状态图标 */
.mail-status-icon {
  flex-shrink: 0;
  display: flex;
  align-items: center;
}
.unread-icon {
  color: #f5a623; /* 橙色实心信封 */
}
.read-icon {
  color: #c7c7cc; /* 灰色打开信封 */
}

/* 附件图标（列表中的回形针标记） */
.att-badge {
  flex-shrink: 0;
  color: var(--text-tertiary, #999);
  margin-left: 2px;
}

/* 多选模式选中行高亮 */
.mail-item.selected {
  background: rgba(0, 122, 255, 0.1);
}

/* 勾选圆圈 */
.check-circle {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  border: 2px solid #c7c7cc;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all 0.2s cubic-bezier(0.34, 1.56, 0.64, 1);
  margin-right: 10px;
}

.check-circle.checked {
  background: #007AFF;
  border-color: #007AFF;
  transform: scale(1.1);
}

/* ==================== 分页器 ==================== */
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 10px 16px;
  border-top: 1px solid var(--border-color);
  flex-shrink: 0;
  background: var(--bg-primary);
}

.page-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 32px;
  height: 32px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
  font-family: inherit;
  padding: 0 6px;
}

.page-btn:hover:not(:disabled):not(.active) {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.page-btn:active:not(:disabled) {
  background: var(--bg-active);
}

.page-btn.active {
  background: #007AFF;
  color: #fff;
  font-weight: 600;
}

.page-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.page-ellipsis {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 32px;
  color: var(--text-tertiary);
  font-size: 13px;
  user-select: none;
}

.mail-avatar {
  width: 34px;
  height: 34px;
  border-radius: var(--border-radius-full);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-size: 13px;
  font-weight: var(--font-semibold);
  flex-shrink: 0;
}

.mail-info {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
}

.mail-from {
  font-size: 13px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: var(--font-medium);
  flex: 1;
  min-width: 0;
}

.mail-subject {
  font-size: 13px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}

.mail-date {
  font-size: 11px;
  color: var(--text-tertiary);
  flex-shrink: 0;
  white-space: nowrap;
  width: 64px;
  text-align: right;
}

/* 已读/未读标签（日期前一列，固定宽度） */
.mail-status-tag {
  flex-shrink: 0;
  width: 42px;
  text-align: center;
  font-size: 10px;
  font-weight: 500;
  padding: 1px 0;
  border-radius: 4px;
  line-height: 1.5;
  white-space: nowrap;
}
.mail-status-tag.unread {
  background: rgba(245, 166, 35, 0.12);
  color: #D48806;
}
.mail-status-tag.read {
  background: rgba(142, 142, 147, 0.1);
  color: #8E8E93;
}

/* ==================== 邮件详情 ==================== */
.mail-detail {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg-primary);
}

.detail-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.btn-back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border: none;
  border-radius: var(--border-radius-sm);
  background: var(--bg-secondary);
  color: var(--text-secondary);
  font-size: var(--text-xs);
  font-family: inherit;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.btn-back:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.detail-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.btn-action {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border: none;
  border-radius: var(--border-radius-sm);
  background: var(--bg-secondary);
  color: var(--text-secondary);
  font-size: var(--text-xs);
  font-family: inherit;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.btn-action:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.btn-action.confirm {
  background: #FF3B30;
  color: #fff;
}

.btn-action.confirm:hover {
  background: #E03A22;
}

.detail-header {
  padding: var(--space-4) var(--space-5) var(--space-3);
  border-bottom: 1px solid var(--border-color);
}

.detail-subject {
  font-size: var(--text-xl);
  font-weight: var(--font-semibold);
  color: var(--text-primary);
  line-height: 1.3;
  margin-bottom: var(--space-3);
}

.detail-meta {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.meta-avatar {
  width: 36px;
  height: 36px;
  border-radius: var(--border-radius-full);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-size: var(--text-sm);
  font-weight: var(--font-semibold);
  flex-shrink: 0;
}

.meta-info {
  min-width: 0;
}

.meta-from {
  font-size: var(--text-sm);
  color: var(--text-primary);
  font-weight: var(--font-medium);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta-date {
  font-size: var(--text-xs);
  color: var(--text-tertiary);
  margin-top: 2px;
}

.detail-body {
  flex: 1;
  overflow-y: auto;
  font-size: var(--text-sm);
  line-height: 1.7;
  color: var(--text-primary);
  -webkit-overflow-scrolling: touch;
}

/* 正文内容区域 */
.detail-content {
  padding: var(--space-5);
}

/* 邮件正文中的图片和链接 */
.detail-body :deep(img) {
  max-width: 100%;
  height: auto;
  border-radius: var(--border-radius-md);
}

.detail-body :deep(a) {
  color: var(--color-accent);
  text-decoration: none;
}

.detail-body :deep(a:hover) {
  text-decoration: underline;
}

/* 附件列表 */
.attachment-list {
  margin-top: 16px;
  padding: 12px;
  border-top: 1px solid var(--border-primary, #e5e7eb);
}
.attachment-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary, #666);
  margin-bottom: 8px;
}
.attachment-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--bg-secondary, #f5f5f5);
  cursor: pointer;
  transition: background 0.15s;
}
.attachment-item:hover {
  background: var(--bg-tertiary, #eaeaea);
}
.att-icon {
  flex-shrink: 0;
  color: var(--text-tertiary, #999);
}
.att-info {
  flex: 1;
  min-width: 0;
}
.att-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary, #333);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.att-meta {
  font-size: 11px;
  color: var(--text-tertiary, #999);
  margin-top: 2px;
}
.att-download {
  flex-shrink: 0;
  color: var(--text-tertiary, #999);
  padding: 4px;
  border-radius: 4px;
  transition: color 0.15s;
}
.attachment-item:hover .att-download {
  color: var(--primary, #007aff);
}

/* 移动端：筛选切换按钮（桌面端隐藏） */
.mobile-filter-toggle { display: none; }

/* 移动端适配 */
@media (max-width: 768px) {
  /* 移动端缩小发件人列宽度，给主题更多空间 */
  .mail-sender { width: 120px; }

  /* 工具栏精简：只保留多选+文件夹+筛选图标+同步 */
  .list-toolbar { padding: 8px 12px; }
  .toolbar-left .toolbar-divider { display: none; }
  .toolbar-left .filter-btn { display: none; }

  /* 移动端筛选切换按钮 */
  .mobile-filter-toggle { display: flex; }
  .mobile-filter-toggle.active { color: var(--accent-blue); }

  /* 工具栏作为下拉菜单的定位基准 */
  .list-toolbar { position: relative; }

  /* 移动端筛选下拉菜单：相对工具栏定位 */
  .mobile-filter-dropdown {
    position: absolute;
    top: 100%;
    left: 0;
    right: 0;
    z-index: 100;
    pointer-events: none;
  }
  .filter-backdrop {
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    pointer-events: auto;
  }
  .filter-dropdown-menu {
    position: relative;
    pointer-events: auto;
    background: var(--bg-primary);
    border-bottom: 1px solid var(--border-color);
    box-shadow: 0 4px 12px rgba(0,0,0,0.1);
    max-height: 60vh;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
  }
  /* 紧凑下拉面板：右侧对齐，固定宽度，圆角卡片（用于邮件列表，只有4个选项） */
  .filter-dropdown-compact {
    position: absolute;
    right: 12px;
    top: 8px;
    min-width: 140px;
    max-height: none;
    border: 1px solid var(--border-color);
    border-radius: 10px;
    box-shadow: 0 6px 20px rgba(0,0,0,0.15);
    padding: 4px 0;
    overflow-y: auto;
    max-height: 50vh;
  }
  .filter-dropdown-item {
    display: block;
    width: 100%;
    padding: 10px 16px;
    font-size: 14px;
    text-align: left;
    background: none;
    border: none;
    color: var(--text-primary);
    cursor: pointer;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .filter-dropdown-item:active { background: var(--bg-secondary); }
  .filter-dropdown-item.active { color: var(--accent-blue); font-weight: 600; }

  /* 下拉菜单动画 */
  .filter-dropdown-enter-active { animation: dropIn 0.15s ease; }
  .filter-dropdown-leave-active { animation: dropOut 0.1s ease; }
  @keyframes dropIn { from { opacity: 0; transform: translateY(-4px) scale(0.96); } to { opacity: 1; transform: translateY(0) scale(1); } }
  @keyframes dropOut { from { opacity: 1; transform: translateY(0) scale(1); } to { opacity: 0; transform: translateY(-4px) scale(0.96); } }

  .account-tabs {
    padding: var(--space-2) var(--space-3);
    overflow-x: auto;
  }

  /* iOS风格文件夹选择按钮 */
  .folder-picker {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 5px 12px;
    border: none;
    border-radius: var(--radius-md);
    background: rgba(0, 122, 255, 0.1);
    color: var(--accent-blue);
    font-size: var(--text-sm);
    font-weight: 600;
    cursor: pointer;
    transition: background 0.2s;
  }
  .folder-picker:active {
    background: rgba(0, 122, 255, 0.2);
  }
  .picker-label {
    max-width: 120px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* iOS风格底部弹出层 */
  .sheet-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.3);
    z-index: 1000;
    display: flex;
    align-items: flex-end;
    justify-content: center;
  }
  .sheet-content {
    width: 100%;
    max-width: 420px;
    max-height: 60vh;
    background: #fff;
    border-radius: 14px 14px 0 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }
  .sheet-handle {
    width: 36px;
    height: 5px;
    border-radius: 3px;
    background: #d1d1d6;
    margin: 8px auto 4px;
  }
  .sheet-title {
    padding: 8px 20px 12px;
    font-size: 13px;
    font-weight: 600;
    color: #8e8e93;
    text-align: center;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .sheet-list {
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
    padding-bottom: env(safe-area-inset-bottom, 20px);
  }
  .sheet-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    padding: 13px 20px;
    border: none;
    background: #fff;
    font-size: 17px;
    color: #000;
    text-align: left;
    cursor: pointer;
    transition: background 0.15s;
  }
  .sheet-item:active {
    background: #f2f2f7;
  }
  .sheet-item.active {
    color: var(--accent-blue);
    font-weight: 500;
  }
  .sheet-item + .sheet-item {
    border-top: 0.5px solid #e5e5ea;
  }
  .sheet-folder-name {
    flex: 1;
  }
  .sheet-folder-count {
    font-size: 15px;
    color: #8e8e93;
    margin-right: 8px;
  }

  /* 弹出层动画 */
  .sheet-enter-active, .sheet-leave-active {
    transition: all 0.3s ease;
  }
  .sheet-enter-from, .sheet-leave-to {
    opacity: 0;
  }
  .sheet-enter-from .sheet-content, .sheet-leave-to .sheet-content {
    transform: translateY(100%);
  }
  .sheet-enter-active .sheet-content, .sheet-leave-active .sheet-content {
    transition: transform 0.3s ease;
  }

  .detail-header {
    padding: var(--space-3) var(--space-4) var(--space-2);
  }

  .detail-subject {
    font-size: var(--text-lg);
  }

  .detail-content {
    padding: var(--space-3) var(--space-4);
  }
}

/* 骨架屏：正文加载中的占位动画 */
.body-skeleton {
  padding: var(--space-5);
}

.skeleton-line {
  height: 14px;
  background: linear-gradient(90deg, var(--bg-tertiary) 25%, var(--bg-hover) 50%, var(--bg-tertiary) 75%);
  background-size: 200% 100%;
  animation: skeleton-loading 1.5s infinite;
  border-radius: 4px;
  margin-bottom: 12px;
}

@keyframes skeleton-loading {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
</style>
