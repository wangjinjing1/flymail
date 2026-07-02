<template>
  <div class="compose-page" @dragover.prevent="isDragging = true" @dragleave.prevent="isDragging = false" @drop.prevent="handleDrop">
    <!-- 拖拽上传遮罩 -->
    <div v-if="isDragging" class="drop-overlay">
      <div class="drop-hint">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--accent-blue)" stroke-width="1.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
        <span>释放以添加附件</span>
      </div>
    </div>

    <!-- 顶部工具栏 -->
    <div class="compose-toolbar">
      <button class="toolbar-btn primary" @click="sendMail" :disabled="sending">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
        <span>{{ sending ? '发送中...' : '发送' }}</span>
      </button>
      <button class="toolbar-btn" @click="showScheduleModal = true; initScheduleTime()" title="定时发送">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
        <span>定时</span>
      </button>
      <button class="toolbar-btn" @click="saveDraft" :disabled="savingDraft">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
        <span>{{ savingDraft ? '保存中...' : '草稿' }}</span>
      </button>

      <!-- 签名模板选择器 -->
      <div class="toolbar-dropdown sig-dropdown">
        <button class="toolbar-btn" title="签名" type="button" @click="showSignaturePanel = !showSignaturePanel">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/></svg>
          <span>签名</span>
        </button>
        <div v-if="showSignaturePanel" class="sig-panel">
          <!-- 内置预设模板 -->
          <div class="sig-section">
            <div class="sig-section-label">内置模板</div>
            <div class="sig-preset-grid">
              <div
                v-for="preset in builtinSignatures"
                :key="preset.id"
                class="sig-preset-card"
              >
                <!-- 点击预览区域直接插入 -->
                <div class="sig-preset-click-area" @click="insertSigToEditor(preset.content_html)">
                  <div class="sig-preset-preview" v-html="preset.preview"></div>
                  <span class="sig-preset-name">{{ preset.name }}</span>
                </div>
                <!-- 自定义按钮：点击打开编辑对话框 -->
                <button
                  class="sig-customize-btn"
                  type="button"
                  title="自定义此模板"
                  @click.stop="openCustomizeDialog(preset)"
                >
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>
                </button>
              </div>
            </div>
          </div>

          <!-- 自定义编辑对话框（内嵌） -->
          <div v-if="showCustomizeDialog && showSignaturePanel" class="modal-overlay" @click.self="showCustomizeDialog = false">
            <div class="modal-content sig-customize-modal">
              <h3>自定义签名：{{ customizingPreset?.name }}</h3>
              <textarea v-model="editingSigHtml" class="sig-customize-textarea" spellcheck="false"></textarea>
              <div class="modal-actions">
                <button class="toolbar-btn" @click="showCustomizeDialog = false">取消</button>
                <button class="toolbar-btn primary" @click="saveCustomizedSig">保存签名</button>
              </div>
            </div>
          </div>

          <!-- 编辑用户签名对话框（内嵌） -->
          <div v-if="showEditUserSigDialog && showSignaturePanel" class="modal-overlay" @click.self="showEditUserSigDialog = false">
            <div class="modal-content sig-customize-modal">
              <h3>编辑签名：{{ editingUserSig?.name }}</h3>
              <input v-model="editingUserSigName" placeholder="签名名称" class="sig-save-input" />
              <textarea v-model="editingUserSigHtml" class="sig-customize-textarea" spellcheck="false"></textarea>
              <div class="modal-actions">
                <button class="toolbar-btn" @click="showEditUserSigDialog = false">取消</button>
                <button class="toolbar-btn danger" @click="deleteEditingUserSig">删除</button>
                <button class="toolbar-btn primary" @click="saveEditedUserSig">保存</button>
              </div>
            </div>
          </div>

          <div class="sig-panel-divider"></div>
          <!-- 我的签名 -->
          <div class="sig-section">
            <div class="sig-section-label">我的签名</div>
            <template v-if="userSigs.length > 0">
              <div class="sig-preset-grid">
                <div
                  v-for="sig in userSigs"
                  :key="'u'+sig.id"
                  class="sig-preset-card sig-user-card"
                >
                  <!-- 标记：默认 / 操作按钮 -->
                  <span v-if="sig.is_default" class="sig-default-badge-inline">默认</span>
                  <button class="sig-delete-btn" type="button" @click.stop="deleteUserSig(sig.id)" title="删除">×</button>
                  <button
                    class="sig-customize-btn"
                    type="button"
                    title="编辑此签名"
                    @click.stop="openEditUserSigDialog(sig)"
                  >
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>
                  </button>
                  <!-- 点击区域：插入签名 -->
                  <div class="sig-preset-click-area" @click="insertSigToEditor(sig.content_html)">
                    <div class="sig-preset-preview sig-user-preview" v-html="sig.content_html"></div>
                    <span class="sig-preset-name">{{ sig.name }}</span>
                  </div>
                </div>
              </div>
            </template>
            <div v-else class="sig-empty-hint">暂无自定义签名</div>
          </div>
          <div class="sig-panel-divider"></div>
          <button class="sig-save-current-btn" type="button" @click="showSaveSigDialog = true">
            + 保存签名
          </button>
        </div>
      </div>

      <!-- 保存签名的内嵌对话框 -->
      <div v-if="showSaveSigDialog && showSignaturePanel" class="modal-overlay" @click.self="showSaveSigDialog = false">
        <div class="modal-content sig-save-dialog">
          <input v-model="newSigName" placeholder="输入签名名称" class="sig-save-input" @keyup.enter="saveCurrentAsSig" />
          <div class="modal-actions">
            <button class="toolbar-btn" @click="showSaveSigDialog = false">取消</button>
            <button class="toolbar-btn primary" @click="saveCurrentAsSig" :disabled="!newSigName.trim()">保存</button>
          </div>
        </div>
      </div>

      <div class="toolbar-spacer"></div>
      <button class="toolbar-btn danger" @click="discardMail" title="关闭">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>

    <!-- 邮件表单 -->
    <div class="compose-form">
      <!-- 发件人 -->
      <div class="form-row">
        <label>发件人</label>
        <select v-model="fromAccountId" class="form-select">
          <option v-for="acc in accounts" :key="acc.id" :value="acc.id">{{ acc.email }}</option>
        </select>
        <span class="cc-links">
          <button v-if="!showCc" class="text-btn" @click="showCc = true">抄送</button>
          <button v-if="!showBcc" class="text-btn" @click="showBcc = true">密送</button>
        </span>
      </div>

      <!-- 收件人 -->
      <div class="form-row">
        <label>收件人</label>
        <div class="tag-input">
          <span v-for="(addr, i) in toList" :key="'to'+i" class="tag">
            {{ addr }}
            <button class="tag-remove" @click="toList.splice(i, 1)">&times;</button>
          </span>
          <input v-model="toInput" type="email" inputmode="email" enterkeyhint="done" @keydown.enter.prevent="addRecipient('to')" @keyup.enter.prevent="addRecipient('to')" @keydown.comma.prevent="addRecipient('to')" @change="addRecipient('to')" @blur="addRecipient('to')" placeholder="输入邮箱后回车" class="tag-input-field" />
        </div>
      </div>

      <!-- 抄送（点击后显示） -->
      <div v-if="showCc" class="form-row">
        <label>抄送</label>
        <div class="tag-input">
          <span v-for="(addr, i) in ccList" :key="'cc'+i" class="tag">
            {{ addr }}
            <button class="tag-remove" @click="ccList.splice(i, 1)">&times;</button>
          </span>
          <input v-model="ccInput" type="email" inputmode="email" enterkeyhint="done" @keydown.enter.prevent="addRecipient('cc')" @keyup.enter.prevent="addRecipient('cc')" @keydown.comma.prevent="addRecipient('cc')" @change="addRecipient('cc')" @blur="addRecipient('cc')" placeholder="输入邮箱后回车" class="tag-input-field" />
        </div>
      </div>

      <!-- 密送（点击后显示） -->
      <div v-if="showBcc" class="form-row">
        <label>密送</label>
        <div class="tag-input">
          <span v-for="(addr, i) in bccList" :key="'bcc'+i" class="tag">
            {{ addr }}
            <button class="tag-remove" @click="bccList.splice(i, 1)">&times;</button>
          </span>
          <input v-model="bccInput" type="email" inputmode="email" enterkeyhint="done" @keydown.enter.prevent="addRecipient('bcc')" @keyup.enter.prevent="addRecipient('bcc')" @keydown.comma.prevent="addRecipient('bcc')" @change="addRecipient('bcc')" @blur="addRecipient('bcc')" placeholder="输入邮箱后回车" class="tag-input-field" />
        </div>
      </div>

      <!-- 主题 -->
      <div class="form-row">
        <label>主题</label>
        <input v-model="subject" placeholder="邮件主题" class="form-input" />
      </div>

      <!-- 富文本编辑器 -->
      <div class="editor-row">
        <TiptapEditor v-model="bodyHtml" ref="editorRef" />
      </div>

      <!-- 附件区域（在编辑器下方、表单底部） -->
      <div class="attachments-section">
        <div class="attachments-header">
          <label class="upload-btn">
            <input type="file" multiple @change="handleFileSelect" class="hidden-input" />
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
            附件
          </label>
          <span v-if="attachments.length" class="attachments-count">{{ attachments.length }}个</span>
        </div>
        <div v-if="attachments.length" class="attachments-list">
          <div v-for="(att, i) in attachments" :key="i" class="attachment-item">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            <span class="att-name">{{ att.filename }}</span>
            <span class="att-size">{{ formatSize(att.size) }}</span>
            <button class="att-remove" @click="removeAttachment(i)">&times;</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 定时发送弹窗 -->
    <div v-if="showScheduleModal" class="modal-overlay" @click.self="showScheduleModal = false">
      <div class="modal-content schedule-modal">
        <div class="schedule-header">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent-blue)" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
          <h3>定时发送</h3>
        </div>
        <p class="modal-desc">选择发送时间，邮件将在指定时间自动发送</p>

        <!-- 日期时间选择卡片 -->
        <div class="schedule-card">
          <div class="schedule-card-row">
            <select v-model="scheduleYear" class="sc-select sc-select-wide">
              <option v-for="y in yearOptions" :key="y" :value="y">{{ y }}</option>
            </select>
            <span class="sc-sep">/</span>
            <select v-model="scheduleMonth" class="sc-select">
              <option v-for="m in 12" :key="m" :value="m">{{ String(m).padStart(2, '0') }}</option>
            </select>
            <span class="sc-sep">/</span>
            <select v-model="scheduleDay" class="sc-select">
              <option v-for="d in dayOptions" :key="d" :value="d">{{ String(d).padStart(2, '0') }}</option>
            </select>
            <span class="sc-gap"></span>
            <select v-model="scheduleHour" class="sc-select">
              <option v-for="h in 24" :key="h" :value="h - 1">{{ String(h - 1).padStart(2, '0') }}</option>
            </select>
            <span class="sc-sep">:</span>
            <select v-model="scheduleMinute" class="sc-select">
              <option v-for="m in 60" :key="m" :value="m - 1">{{ String(m - 1).padStart(2, '0') }}</option>
            </select>
          </div>
        </div>

        <!-- 预览条 -->
        <div class="schedule-preview-bar">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--accent-blue)" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
          <span>将于 <strong>{{ schedulePreview }}</strong> 自动发送</span>
        </div>

        <!-- 待发邮件列表（有待发任务时才显示） -->
        <div v-if="scheduledJobs.length > 0" class="scheduled-list">
          <div class="scheduled-list-title">待发邮件</div>
          <div v-for="job in scheduledJobs" :key="job.id" class="scheduled-item">
            <div class="scheduled-item-info">
              <span class="scheduled-item-subject">{{ job.kwargs?.subject || '(无主题)' }}</span>
              <span class="scheduled-item-time">{{ formatScheduleTime(job.next_run_time) }}</span>
            </div>
            <button class="scheduled-item-cancel" @click="cancelSchedule(job.id)" title="取消发送">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
          </div>
        </div>
        <div class="modal-actions">
          <button class="toolbar-btn" @click="showScheduleModal = false">取消</button>
          <button class="toolbar-btn primary" @click="scheduleMail" :disabled="!isScheduleValid">定时发送</button>
        </div>
      </div>
    </div>

    <!-- 定时发送成功弹窗 -->
    <div v-if="showScheduleSuccessModal" class="modal-overlay" @click.self="showScheduleSuccessModal = false">
      <div class="modal-content success-modal">
        <div class="success-icon">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#34C759" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
        </div>
        <h3>定时任务已创建</h3>
        <p class="success-desc">邮件将在 <strong>{{ scheduleSuccessTime }}</strong> 自动发送</p>
        <div class="modal-actions" style="justify-content: center;">
          <button class="toolbar-btn primary" @click="showScheduleSuccessModal = false">知道了</button>
        </div>
      </div>
    </div>

    <!-- 确认对话框 -->
    <div v-if="showConfirmDialog" class="modal-overlay" @click.self="showConfirmDialog = false">
      <div class="modal-content confirm-modal">
        <div class="confirm-icon">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--text-secondary)" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        </div>
        <p class="confirm-text">{{ confirmMessage }}</p>
        <div class="modal-actions">
          <button class="toolbar-btn" @click="showConfirmDialog = false">取消</button>
          <button class="toolbar-btn danger" @click="confirmCallback(); showConfirmDialog = false">确认</button>
        </div>
      </div>
    </div>

    <!-- Toast 通知 -->
    <Transition name="toast">
      <div v-if="toast.visible" class="toast" :class="toast.type">
        <svg v-if="toast.type === 'success'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
        <svg v-else-if="toast.type === 'error'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
        <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        <span>{{ toast.message }}</span>
      </div>
    </Transition>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue';
import api from '../utils/api';
import { useMailStore } from '../stores/mail';
import TiptapEditor from '../components/TiptapEditor.vue';

const emit = defineEmits<{
  discard: [];
  sent: [];
}>();

const mailStore = useMailStore();

// 表单数据
const fromAccountId = ref('');
const toList = ref<string[]>([]);
const ccList = ref<string[]>([]);
const bccList = ref<string[]>([]);
const subject = ref('');
const bodyHtml = ref('');
const showCc = ref(false);
const showBcc = ref(false);

// 收件人输入
const toInput = ref('');
const ccInput = ref('');
const bccInput = ref('');

// 附件
const attachments = ref<{ filename: string; size: number; path: string }[]>([]);
const isDragging = ref(false);

// 状态
const sending = ref(false);
const savingDraft = ref(false);
const showScheduleModal = ref(false);

// ---- Toast 通知系统 ----
const toast = ref({ visible: false, message: '', type: 'success' as 'success' | 'error' | 'info' });
let toastTimer: ReturnType<typeof setTimeout> | null = null;

/** 显示 Toast 通知（替代 alert） */
function showToast(message: string, type: 'success' | 'error' | 'info' = 'success') {
  if (toastTimer) clearTimeout(toastTimer);
  toast.value = { visible: true, message, type };
  toastTimer = setTimeout(() => { toast.value.visible = false; }, 2500);
}

// ---- 确认对话框（替代 confirm） ----
const showConfirmDialog = ref(false);
const confirmMessage = ref('');
const confirmCallback = ref(() => {});

// ---- 确认对话框（替代 confirm） ----
function showConfirm(message: string, callback: () => void) {
  confirmMessage.value = message;
  confirmCallback.value = callback;
  showConfirmDialog.value = true;
}

// ---- 定时发送：下拉框选择器 ----
const scheduleYear = ref(new Date().getFullYear());
const scheduleMonth = ref(new Date().getMonth() + 1);
const scheduleDay = ref(new Date().getDate());
const scheduleHour = ref(new Date().getHours());
const scheduleMinute = ref(new Date().getMinutes());

// ---- 定时发送：待发邮件列表 ----
const scheduledJobs = ref<any[]>([]);
const showScheduleSuccessModal = ref(false);
const scheduleSuccessTime = ref('');

/** 加载待执行的定时发送任务 */
async function loadScheduledJobs() {
  try {
    const data = await api.get('/messages/scheduled') as any;
    const allJobs = data?.jobs || [];
    // 只显示有待执行时间的任务（已执行的任务 next_run_time 为 null）
    scheduledJobs.value = allJobs.filter((j: any) => j.next_run_time);
    console.log('[定时发送] 加载待发列表:', scheduledJobs.value.length, '条', scheduledJobs.value);
  } catch (e) {
    console.warn('[定时发送] 加载待发列表失败:', e);
    scheduledJobs.value = [];
  }
}

/** 取消定时发送任务 */
async function cancelSchedule(jobId: string) {
  try {
    await api.delete(`/messages/scheduled/${jobId}`);
    scheduledJobs.value = scheduledJobs.value.filter((j: any) => j.id !== jobId);
  } catch { /* 忽略 */ }
}

/** 年份选项：当前年 ~ 后3年 */
const yearOptions = computed(() => {
  const now = new Date().getFullYear();
  return [now, now + 1, now + 2, now + 3];
});

/** 日期选项：根据年月动态计算天数 */
const dayOptions = computed(() => {
  const daysInMonth = new Date(scheduleYear.value, scheduleMonth.value, 0).getDate();
  return daysInMonth;
});

/** 定时时间是否有效（不早于当前时间） */
const isScheduleValid = computed(() => {
  const now = new Date();
  const scheduled = new Date(scheduleYear.value, scheduleMonth.value - 1, scheduleDay.value, scheduleHour.value, scheduleMinute.value);
  return scheduled > now;
});

/** 定时时间预览文字 */
const schedulePreview = computed(() => {
  const y = scheduleYear.value;
  const m = String(scheduleMonth.value).padStart(2, '0');
  const d = String(scheduleDay.value).padStart(2, '0');
  const h = String(scheduleHour.value).padStart(2, '0');
  const min = String(scheduleMinute.value).padStart(2, '0');
  const str = `${y}-${m}-${d} ${h}:${min}`;
  return str;
});

/** 格式化定时任务执行时间（ISO 字符串 → 可读格式） */
function formatScheduleTime(isoStr: string): string {
  if (!isoStr) return '';
  try {
    const match = isoStr.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
    if (match) {
      return `${match[1]}-${match[2]}-${match[3]} ${match[4]}:${match[5]}`;
    }
    return isoStr;
  } catch { return isoStr; }
}

/** 打开定时弹窗时，初始化为当前时间 + 加载待发列表 */
function initScheduleTime() {
  const now = new Date();
  scheduleYear.value = now.getFullYear();
  scheduleMonth.value = now.getMonth() + 1;
  scheduleDay.value = now.getDate();
  scheduleHour.value = now.getHours();
  scheduleMinute.value = now.getMinutes();
  loadScheduledJobs();
}

// 账号列表
const accounts = computed(() => mailStore.accounts);

// ==================== 签名模板（内置 + 用户自定义） ====================
const showSignaturePanel = ref(false);
const showSaveSigDialog = ref(false);
const newSigName = ref('');
const editorRef = ref<InstanceType<typeof import('../components/TiptapEditor.vue').default> | null>(null);

/** 内置预设签名模板（4款精美常用签名） */
const builtinSignatures = [
  {
    id: 'business',
    name: '商务正式',
    content_html: '<div style="border-top: 2px solid #333; padding-top: 10px; margin-top: 16px; font-family: -apple-system, sans-serif;"><strong style="font-size: 14px; color: #1a1a1a;">张三</strong><br><span style="font-size: 12px; color: #666;">产品经理 | 飞邮科技</span><br><span style="font-size: 11px; color: #999;">📧 zhangsan@flymail.com &nbsp; 📱 138-xxxx-xxxx</span></div>',
    preview: '<div style="padding:6px 0;font-size:9px;line-height:1.5;color:#666"><b>张三</b><br>产品经理 | 飞邮科技<br>📧 zhangsan@flymail.com</div>',
  },
  {
    id: 'minimal',
    name: '简洁现代',
    content_html: '<div style="margin-top: 18px; font-family: -apple-system, sans-serif; color: #555; font-size: 13px; line-height: 1.8;"><span style="color: #007AFF; font-weight: 600;">张三</span>&nbsp;&nbsp;<span style="color: #999;">|</span>&nbsp;&nbsp;<em style="color: #888;">用心做好每一封邮件</em></div>',
    preview: '<div style="padding:6px 0;font-size:9px;line-height:1.5"><b style="color:#007AFF">张三</b> <span style="color:#ccc">|</span> <i style="color:#999">用心做好每一封邮件</i></div>',
  },
  {
    id: 'creative',
    name: '创意个性',
    content_html: '<div style="margin-top: 16px; font-family: -apple-system, sans-serif; border-left: 3px solid #ff6b35; padding-left: 12px;"><span style="font-size: 15px; font-weight: 700; color: #1a1a1a;">张三</span><br><span style="font-size: 12px; color: #888;">保持好奇，保持热爱 ✨</span><br><a href="mailto:zhangsan@flymail.com" style="font-size: 11px; color: #ff6b35; text-decoration: none;">zhangsan@flymail.com →</a></div>',
    preview: '<div style="padding:6px 0;font-size:9px;line-height:1.5;border-left:2px solid #ff6b35;padding-left:6px"><b>张三</b><br><span style="color:#999">保持好奇，保持热爱 ✨</span></div>',
  },
  {
    id: 'english',
    name: '英文正式',
    content_html: '<div style="margin-top: 16px; font-family: Georgia, serif; border-top: 1px solid #ccc; padding-top: 10px;"><span style="font-size: 14px; color: #222; font-style: italic;">Zhang San</span><br><span style="font-size: 11px; color: #777;">Product Manager, FlyMail Inc.</span><br><span style="font-size: 10px; color: #aaa;">Tel: +86-138-xxxx-xxxx &nbsp; Email: zhangsan@flymail.com</span></div>',
    preview: '<div style="padding:6px 0;font-size:9px;line-height:1.5;color:#555;font-family:Georgia,serif"><i>Zhang San</i><br>Product Manager, FlyMail Inc.</div>',
  },
];

/** 用户自定义签名 */
interface UserSig { id: number; name: string; content_html: string; is_default: boolean; }
const userSigs = ref<UserSig[]>([]);

async function loadUserSigs() {
  try {
    const data = await api.get('/signatures') as any;
    userSigs.value = data.signatures || [];
  } catch { userSigs.value = []; }
}

/** 通过编辑器 ref 插入签名到光标位置 */
function insertSigToEditor(contentHtml: string) {
  if (editorRef.value) {
    editorRef.value.insertText(contentHtml);
  }
  showSignaturePanel.value = false;
}

/** 保存当前编辑器内容为用户签名 */
async function saveCurrentAsSig() {
  const name = newSigName.value.trim();
  if (!name || !editorRef.value) return;
  try {
    const htmlContent = editorRef.value.getHTML?.() || '';
    await api.post('/signatures', {
      name,
      content_html: htmlContent,
      is_default: userSigs.value.length === 0 ? true : undefined,
    });
    newSigName.value = '';
    showSaveSigDialog.value = false;
    await loadUserSigs();
  } catch (e: any) { console.error('保存签名失败:', e); }
}

/** 删除用户签名 */
async function deleteUserSig(sigId: number) {
  try {
    await api.delete(`/signatures/${sigId}`);
    await loadUserSigs();
  } catch (e: any) { console.error('删除签名失败:', e); }
}

// ==================== 内置模板自定义编辑 ====================
const showCustomizeDialog = ref(false);
const customizingPreset = ref<{ id: string; name: string; content_html: string } | null>(null);
const editingSigHtml = ref('');

/** 打开自定义编辑对话框，加载内置模板的原始 HTML */
function openCustomizeDialog(preset: { id: string; name: string; content_html: string }) {
  customizingPreset.value = preset;
  // 将单行 HTML 格式化为可读的多行形式
  editingSigHtml.value = formatHtmlForEdit(preset.content_html);
  showCustomizeDialog.value = true;
}

/** 将压缩的 HTML 格式化为可读多行文本 */
function formatHtmlForEdit(html: string): string {
  // 简单格式化：在 > 后换行，缩进子标签
  return html
    .replace(/></g, '>\n<')
    .split('\n')
    .map((line, i) => {
      if (line.match(/^<\//)) return '  '.repeat(Math.max(0, i > 0 ? 1 : 0)) + line;
      return line;
    })
    .join('\n');
}

/** 保存自定义后的签名到"我的签名"列表 */
async function saveCustomizedSig() {
  if (!customizingPreset.value) return;
  try {
    // 名称：基于原模板名 + "(自定义)"
    const baseName = customizingPreset.value.name;
    const name = `${baseName}(自定义)`;
    await api.post('/signatures', {
      name,
      content_html: editingSigHtml.value,
      is_default: userSigs.value.length === 0 ? true : undefined,
    });
    showCustomizeDialog.value = false;
    await loadUserSigs();
  } catch (e: any) { console.error('保存自定义签名失败:', e); }
}

// ==================== 编辑用户签名 ====================
const showEditUserSigDialog = ref(false);
const editingUserSig = ref<UserSig | null>(null);
const editingUserSigName = ref('');
const editingUserSigHtml = ref('');

/** 打开编辑用户签名对话框 */
function openEditUserSigDialog(sig: UserSig) {
  editingUserSig.value = sig;
  editingUserSigName.value = sig.name;
  editingUserSigHtml.value = formatHtmlForEdit(sig.content_html);
  showEditUserSigDialog.value = true;
}

/** 保存修改后的用户签名（PUT 更新） */
async function saveEditedUserSig() {
  if (!editingUserSig.value) return;
  try {
    await api.put(`/signatures/${editingUserSig.value.id}`, {
      name: editingUserSigName.value.trim(),
      content_html: editingUserSigHtml.value,
      is_default: editingUserSig.value.is_default,
    });
    showEditUserSigDialog.value = false;
    await loadUserSigs();
  } catch (e: any) { console.error('保存签名失败:', e); }
}

/** 在编辑对话框中直接删除当前签名 */
async function deleteEditingUserSig() {
  if (!editingUserSig.value) return;
  try {
    await api.delete(`/signatures/${editingUserSig.value.id}`);
    showEditUserSigDialog.value = false;
    await loadUserSigs();
  } catch (e: any) { console.error('删除签名失败:', e); }
}

async function loadDefaultSignatureIfEmpty() {
  if (bodyHtml.value) return;
  try {
    const data = await api.get('/signatures') as any;
    // 找到 is_default=1 的签名模板
    const defaultSig = data.signatures?.find((s: any) => s.is_default);
    if (defaultSig?.content_html) {
      bodyHtml.value = '<p><br></p>' + defaultSig.content_html;
    }
  } catch {
    // 签名加载失败不影响写邮件
  }
}

async function applyComposeDraft(draft: any = null) {
  clearComposeForm();
  toList.value = draft?.to || [];
  ccList.value = draft?.cc || [];
  bccList.value = draft?.bcc || [];
  subject.value = draft?.subject || '';
  bodyHtml.value = draft?.body_html || '';
  fromAccountId.value = draft?.account_id || mailStore.currentAccountId || accounts.value[0]?.id || '';
  showCc.value = ccList.value.length > 0;
  showBcc.value = bccList.value.length > 0;
  if (!draft?.body_html) {
    await loadDefaultSignatureIfEmpty();
  }
}

// 初始化：选择当前账号，加载签名，消费草稿数据
onMounted(async () => {
  await applyComposeDraft(mailStore.consumeComposeDraft());
  // 加载用户自定义签名列表（用于签名面板）
  loadUserSigs();
});

watch(
  () => mailStore.composeDraft,
  async (draft) => {
    if (draft) {
      await applyComposeDraft(mailStore.consumeComposeDraft());
    }
  },
);

// 添加收件人
function addRecipient(field: 'to' | 'cc' | 'bcc') {
  const inputRef = field === 'to' ? toInput : field === 'cc' ? ccInput : bccInput;
  const listRef = field === 'to' ? toList : field === 'cc' ? ccList : bccList;
  const email = inputRef.value.trim().replace(/,$/, '');
  if (!email) return;
  // 简单邮箱格式校验
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return;
  if (!listRef.value.includes(email)) {
    listRef.value.push(email);
  }
  inputRef.value = '';
}

function commitRecipientInputs() {
  addRecipient('to');
  addRecipient('cc');
  addRecipient('bcc');
}

function getErrorMessage(e: any) {
  return e?.error || e?.message || e?.response?.data?.error || '网络错误';
}

function clearComposeForm() {
  toList.value = [];
  ccList.value = [];
  bccList.value = [];
  toInput.value = '';
  ccInput.value = '';
  bccInput.value = '';
  subject.value = '';
  bodyHtml.value = '';
  attachments.value = [];
  showCc.value = false;
  showBcc.value = false;
}

// 发送邮件
async function sendMail() {
  commitRecipientInputs();
  if (toList.value.length === 0) {
    showToast('请输入收件人', 'info');
    return;
  }
  sending.value = true;
  try {
    await api.post('/messages/compose', {
      account_id: fromAccountId.value,
      to: toList.value,
      cc: ccList.value,
      bcc: bccList.value,
      subject: subject.value,
      body_html: bodyHtml.value,
      attachments: attachments.value.map(a => a.path),
      action: 'send',
    }) as any;
    showToast('发送成功', 'success');
    clearComposeForm();
    emit('sent');
  } catch (e: any) {
    showToast('发送失败: ' + getErrorMessage(e), 'error');
  } finally {
    sending.value = false;
  }
}

// 保存草稿
async function saveDraft() {
  savingDraft.value = true;
  try {
    await api.post('/messages/compose', {
      account_id: fromAccountId.value,
      to: toList.value,
      cc: ccList.value,
      bcc: bccList.value,
      subject: subject.value,
      body_html: bodyHtml.value,
      action: 'draft',
    });
    showToast('草稿已保存', 'success');
  } catch (e: any) {
    showToast('保存草稿失败: ' + getErrorMessage(e), 'error');
  } finally {
    savingDraft.value = false;
  }
}

// 定时发送
async function scheduleMail() {
  if (toList.value.length === 0) {
    showToast('请输入收件人', 'info');
    return;
  }
  if (!isScheduleValid.value) {
    showToast('请选择未来的时间', 'info');
    return;
  }
  // 组装 ISO 时间字符串
  const y = scheduleYear.value;
  const m = String(scheduleMonth.value).padStart(2, '0');
  const d = String(scheduleDay.value).padStart(2, '0');
  const h = String(scheduleHour.value).padStart(2, '0');
  const min = String(scheduleMinute.value).padStart(2, '0');
  const scheduleTimeISO = `${y}-${m}-${d}T${h}:${min}:00`;
  try {
    await api.post('/messages/compose', {
      account_id: fromAccountId.value,
      to: toList.value,
      cc: ccList.value,
      bcc: bccList.value,
      subject: subject.value,
      body_html: bodyHtml.value,
      action: 'draft',
    });
    await api.post('/messages/compose', {
      account_id: fromAccountId.value,
      to: toList.value,
      cc: ccList.value,
      bcc: bccList.value,
      subject: subject.value,
      body_html: bodyHtml.value,
      attachments: attachments.value.map(a => a.path),
      action: 'schedule',
      schedule_time: scheduleTimeISO,
    });
    showScheduleModal.value = false;
    // 不跳转页面，显示成功弹窗
    scheduleSuccessTime.value = schedulePreview.value;
    showScheduleSuccessModal.value = true;
  } catch (e: any) {
    showToast('设置定时发送失败: ' + getErrorMessage(e), 'error');
  }
}

// 关闭邮件
function discardMail() {
  if (subject.value || bodyHtml.value || toList.value.length > 0) {
    showConfirm('确定关闭写邮件？未保存的内容将丢失', () => { emit('discard'); });
  } else {
    emit('discard');
  }
}

// 附件处理
async function handleFileSelect(event: Event) {
  const input = event.target as HTMLInputElement;
  if (!input.files) return;
  for (const file of Array.from(input.files)) {
    await uploadFile(file);
  }
  input.value = '';
}

async function handleDrop(event: DragEvent) {
  isDragging.value = false;
  if (!event.dataTransfer?.files) return;
  for (const file of Array.from(event.dataTransfer.files)) {
    await uploadFile(file);
  }
}

async function uploadFile(file: File) {
  const formData = new FormData();
  formData.append('file', file);
  try {
    const data = await api.post('/messages/upload-attachment', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }) as any;
    attachments.value.push({
      filename: data.filename,
      size: data.size,
      path: data.path,
    });
  } catch (e: any) {
    showToast('上传附件失败: ' + file.name, 'error');
  }
}

async function removeAttachment(index: number) {
  const att = attachments.value[index];
  try {
    await api.delete('/messages/upload-attachment', { params: { path: att.path } });
  } catch {
    // 删除失败也从前端移除
  }
  attachments.value.splice(index, 1);
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}
</script>

<style scoped>
.compose-page {
  flex: 1;
  width: 100%;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  min-width: 0;
  position: relative;
  background: var(--bg-primary);
}

/* 拖拽上传遮罩 */
.drop-overlay {
  position: absolute;
  inset: 0;
  z-index: 100;
  background: rgba(0, 122, 255, 0.08);
  border: 2px dashed var(--accent-blue, #007AFF);
  border-radius: var(--border-radius-lg, 8px);
  display: flex;
  align-items: center;
  justify-content: center;
}

.drop-hint {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  color: var(--accent-blue, #007AFF);
  font-size: var(--text-base);
  font-weight: 500;
}

/* 工具栏 */
.compose-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-secondary);
  min-width: 0;
}

.toolbar-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}

.toolbar-btn:hover {
  background: var(--bg-hover);
}

.toolbar-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.toolbar-btn.primary {
  background: var(--accent-blue, #007AFF);
  color: #fff;
  border-color: var(--accent-blue, #007AFF);
}

.toolbar-btn.primary:hover {
  opacity: 0.9;
}

.toolbar-btn.danger:hover {
  background: #ff3b30;
  color: #fff;
  border-color: #ff3b30;
}

.toolbar-spacer {
  flex: 1;
}

/* 表单 */
.compose-form {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
  min-width: 0;
  width: 100%;
  padding: 0 16px 16px;
  display: flex;
  flex-direction: column;
}

.form-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--border-color);
  min-width: 0;
}

.form-row label {
  flex-shrink: 0;
  width: 56px;
  font-size: 13px;
  color: var(--text-secondary);
  text-align: right;
}

.form-input {
  flex: 1;
  min-width: 0;
  padding: 6px 10px;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 14px;
  outline: none;
}

.form-input:focus {
  border-color: var(--accent-blue, #007AFF);
}

.form-select {
  flex: 1;
  min-width: 0;
  padding: 6px 10px;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 14px;
  outline: none;
}

/* 标签输入 */
.tag-input {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 4px 8px;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background: var(--bg-primary);
  min-height: 36px;
  align-items: center;
}

.tag-input:focus-within {
  border-color: var(--accent-blue, #007AFF);
}

.tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  background: var(--accent-blue, #007AFF);
  color: #fff;
  border-radius: 4px;
  font-size: 12px;
}

.tag-remove {
  background: none;
  border: none;
  color: rgba(255, 255, 255, 0.7);
  cursor: pointer;
  font-size: 14px;
  padding: 0 2px;
  line-height: 1;
}

.tag-remove:hover {
  color: #fff;
}

.tag-input-field {
  flex: 1;
  min-width: 120px;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-primary);
  font-size: 14px;
  padding: 2px 0;
}

.text-btn {
  background: none;
  border: none;
  color: var(--accent-blue, #007AFF);
  cursor: pointer;
  font-size: 12px;
  padding: 0;
}

.text-btn:hover {
  text-decoration: underline;
}

/* 编辑器行：独立于 form-row 布局，占满剩余空间 */
.editor-row {
  padding: 8px 0;
  flex: 1;
  min-height: 200px;
  overflow: hidden;
  min-width: 0;
}

/* 抄送/密送链接 */
.cc-links {
  display: flex;
  gap: 8px;
  margin-left: 8px;
  flex-shrink: 0;
}

/* 附件区域 */
.attachments-section {
  padding: 4px 0;
  flex-shrink: 0;
  min-width: 0;
}

.attachments-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.attachments-count {
  font-size: 12px;
  color: var(--text-tertiary);
}

.upload-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border: 1px dashed var(--border-color);
  border-radius: 6px;
  color: var(--accent-blue, #007AFF);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}

.upload-btn:hover {
  background: var(--bg-hover);
  border-color: var(--accent-blue, #007AFF);
}

.hidden-input {
  display: none;
}

.attachments-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.attachment-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  background: var(--bg-secondary);
  border-radius: 6px;
  font-size: 12px;
  color: var(--text-primary);
}

.att-name {
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.att-size {
  color: var(--text-tertiary);
}

.att-remove {
  background: none;
  border: none;
  color: var(--text-tertiary);
  cursor: pointer;
  font-size: 16px;
  padding: 0 2px;
  line-height: 1;
}

.att-remove:hover {
  color: #ff3b30;
}

/* 弹窗 */
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 200;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: visible;
}

.modal-content {
  background: var(--bg-primary);
  border-radius: var(--border-radius-lg, 8px);
  padding: 24px;
  width: 360px;
  max-width: 90vw;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
  overflow: visible;
}

.modal-content h3 {
  margin: 0 0 8px;
  font-size: 16px;
  color: var(--text-primary);
}

.modal-desc {
  margin: 0 0 16px;
  font-size: 13px;
  color: var(--text-secondary);
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}

/* 定时发送弹窗 */
.schedule-modal {
  width: 420px;
  max-height: 80vh;
}

.schedule-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.schedule-header h3 {
  margin: 0;
}

/* 日期/时间选择卡片 */
.schedule-card {
  background: var(--bg-secondary);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 8px;
}

.schedule-card-row {
  display: flex;
  align-items: center;
  gap: 2px;
  justify-content: center;
}

.sc-gap {
  width: 8px;
  flex-shrink: 0;
}

.sc-select {
  padding: 5px 18px 5px 6px;
  border: 1px solid var(--border-color);
  border-radius: 5px;
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 500;
  outline: none;
  cursor: pointer;
  appearance: none;
  -webkit-appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg width='8' height='5' viewBox='0 0 8 5' fill='none' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M1 1L4 4L7 1' stroke='%23999' stroke-width='1.2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 5px center;
  transition: border-color 0.15s;
  width: 44px;
  text-align: center;
  flex-shrink: 0;
}

.sc-select-wide {
  width: 60px;
}

.sc-select:focus {
  border-color: var(--accent-blue, #007AFF);
}

.sc-sep {
  font-size: 16px;
  color: var(--text-secondary);
  font-weight: 300;
  line-height: 1;
  user-select: none;
}

/* 预览条 */
.schedule-preview-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 12px;
  background: rgba(0, 122, 255, 0.06);
  border: 1px solid rgba(0, 122, 255, 0.12);
  border-radius: 6px;
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}

.schedule-preview-bar strong {
  color: var(--accent-blue, #007AFF);
  font-weight: 600;
}

/* 定时发送成功弹窗 */
.success-modal {
  width: 320px;
  text-align: center;
}

.success-icon {
  margin-bottom: 12px;
}

.success-desc {
  margin: 0;
  font-size: 14px;
  color: var(--text-secondary);
  line-height: 1.5;
}

.success-desc strong {
  color: var(--text-primary);
}

/* 待发邮件列表 */
.scheduled-list {
  margin-top: 12px;
  border-top: 1px solid var(--border-color);
  padding-top: 10px;
}

.scheduled-list-title {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 8px;
  font-weight: 500;
}

.scheduled-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 8px;
  border-radius: 6px;
  margin-bottom: 4px;
  background: var(--bg-secondary);
  transition: background 0.15s;
}

.scheduled-item:hover {
  background: var(--bg-hover);
}

.scheduled-item-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.scheduled-item-subject {
  font-size: 13px;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.scheduled-item-time {
  font-size: 11px;
  color: var(--text-secondary);
}

.scheduled-item-cancel {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  background: none;
  color: var(--text-secondary);
  cursor: pointer;
  border-radius: 4px;
  flex-shrink: 0;
  margin-left: 8px;
  transition: all 0.15s;
}

.scheduled-item-cancel:hover {
  background: rgba(255, 59, 48, 0.1);
  color: #FF3B30;
}

/* 确认对话框 */
.confirm-modal {
  width: 320px;
  text-align: center;
}

.confirm-icon {
  margin-bottom: 12px;
}

.confirm-text {
  margin: 0 0 4px;
  font-size: 14px;
  color: var(--text-primary);
  line-height: 1.5;
}

/* Toast 通知 */
.toast {
  position: fixed;
  top: 20px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 300;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 20px;
  border-radius: 8px;
  font-size: 14px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
  pointer-events: none;
}

.toast.success {
  background: #34C759;
  color: #fff;
}

.toast.error {
  background: #FF3B30;
  color: #fff;
}

.toast.info {
  background: var(--bg-primary);
  color: var(--text-primary);
  border: 1px solid var(--border-color);
}

/* Toast 动画 */
.toast-enter-active {
  transition: all 0.3s ease-out;
}

.toast-leave-active {
  transition: all 0.2s ease-in;
}

.toast-enter-from {
  opacity: 0;
  transform: translateX(-50%) translateY(-20px);
}

.toast-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(-10px);
}

/* 移动端适配 */
@media (max-width: 768px) {
  .compose-toolbar {
    padding: 8px 12px;
    gap: 4px;
    align-items: stretch;
  }

  .toolbar-btn span {
    display: none;
  }

  .toolbar-btn {
    padding: 8px;
  }

  .toolbar-spacer {
    display: none;
  }

  .compose-form {
    padding: 0 12px 12px;
  }

  .form-row {
    flex-wrap: wrap;
    align-items: flex-start;
    gap: 8px;
    padding: 10px 0;
  }

  .form-row label {
    width: 100%;
    font-size: 12px;
    text-align: left;
  }

  .form-input,
  .form-select,
  .tag-input {
    width: 100%;
  }

  .cc-links {
    width: 100%;
    margin-left: 0;
  }

  .tag-input-field {
    min-width: 80px;
  }

  .attachments-list {
    width: 100%;
    flex-direction: column;
  }

  .attachment-item {
    width: 100%;
    min-width: 0;
  }

  .att-name {
    flex: 1;
    max-width: none;
    min-width: 0;
  }

  .modal-content {
    width: 90vw;
    padding: 16px;
  }

  .schedule-modal {
    width: 90vw;
  }

  .sc-select {
    font-size: 13px;
    padding: 4px 16px 4px 6px;
  }
}

/* ==================== 签名模板选择器（工具栏内嵌） ==================== */
.sig-dropdown {
  position: relative;
  display: inline-flex;
}

.sig-panel {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: 4px;
  width: min(320px, calc(100vw - 24px));
  max-height: min(460px, calc(100vh - 120px));
  overflow-y: auto;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.15);
  z-index: 200;
  padding: 10px;
}

.sig-section-label {
  font-size: 11px;
  color: var(--text-tertiary);
  padding: 2px 4px 6px;
  margin-bottom: 2px;
  letter-spacing: 0.5px;
}

.sig-preset-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-bottom: 4px;
}

.sig-preset-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  padding: 8px 6px;
  border: 1px solid var(--border-color);
  border-radius: 7px;
  background: var(--bg-secondary);
  cursor: pointer;
  transition: all 0.15s;
}

.sig-preset-card:hover {
  border-color: var(--accent-blue, #007AFF);
  background: rgba(0, 122, 255, 0.04);
  transform: translateY(-1px);
}

.sig-preset-preview {
  width: 100%;
  min-height: 48px;
  max-height: 56px;
  overflow: hidden;
  opacity: 0.85;
  line-height: 1.3;
}

.sig-preset-name {
  font-size: 11px;
  color: var(--text-secondary);
  font-weight: 500;
}

/* 预设卡片：点击区域 + 自定义按钮 */
.sig-preset-click-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  cursor: pointer;
  min-width: 0;
}

.sig-customize-btn {
  position: absolute;
  top: 4px; right: 4px;
  width: 20px; height: 20px;
  border: none; border-radius: 50%;
  background: rgba(0,0,0,0.06); color: var(--text-tertiary);
  font-size: 10px; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  opacity: 0; transition: all 0.15s;
  flex-shrink: 0;
}
.sig-preset-card:hover .sig-customize-btn { opacity: 1; }
.sig-customize-btn:hover {
  background: var(--accent-blue, #007AFF);
  color: #fff;
}

/* 自定义编辑对话框 */
.sig-customize-modal {
  width: min(420px, calc(100vw - 32px));
  text-align: left;
}
.sig-customize-modal h3 { margin-bottom: 8px; font-size: 14px; }
.sig-customize-textarea {
  width: 100%; height: 180px;
  padding: 10px 12px;
  border: 1px solid var(--border-color); border-radius: 6px;
  background: var(--bg-secondary); color: var(--text-primary);
  font-family: 'Courier New', Consolas, monospace;
  font-size: 12px; line-height: 1.5;
  resize: vertical; outline: none;
  box-sizing: border-box;
}
.sig-customize-textarea:focus { border-color: var(--accent-blue, #007AFF); }

/* 我的签名：复用预设卡片样式 + 额外标记 */
.sig-user-card {
  position: relative;
}

/* 用户签名预览：限制高度，避免内容过长撑开卡片 */
.sig-user-preview {
  max-height: 56px;
  overflow: hidden;
  font-size: 9px !important;
  line-height: 1.3 !important;
}

/* 缩小用户签名预览内的文字 */
.sig-user-preview :deep(*) {
  font-size: inherit !important;
}

.sig-default-badge-inline {
  position: absolute;
  top: 4px; left: 6px;
  padding: 1px 7px;
  font-size: 10px;
  background: rgba(0,122,255,0.12);
  color: var(--accent-blue, #007AFF);
  border-radius: 3px;
  line-height: 1.2;
}

.sig-panel-divider {
  height: 1px;
  background: var(--border-color);
  margin: 8px 0;
}

.sig-user-item {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 7px 10px;
  border: none;
  background: none;
  color: var(--text-primary);
  font-size: 13px;
  cursor: pointer;
  border-radius: 5px;
  transition: all 0.12s;
  text-align: left;
}

.sig-user-item:hover { background: var(--bg-hover); }
.sig-user-item.active .sig-user-name { color: var(--accent-blue, #007AFF); font-weight: 500; }

.sig-user-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sig-default-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--accent-blue, #007AFF);
  flex-shrink: 0;
}

.sig-delete-btn {
  width: 18px; height: 18px;
  border: none; border-radius: 50%;
  background: transparent; color: var(--text-tertiary);
  font-size: 14px; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0; opacity: 0; transition: all 0.12s;
}
.sig-user-item:hover .sig-delete-btn { opacity: 1; }
.sig-delete-btn:hover { background: rgba(255,59,48,0.1); color: #ff3b30; }

.sig-empty-hint {
  padding: 10px 8px; text-align: center;
  font-size: 12px; color: var(--text-tertiary);
}

.sig-save-current-btn {
  width: 100%; padding: 7px 10px;
  border: 1px dashed var(--accent-blue, #007AFF);
  border-radius: 6px; background: transparent;
  color: var(--accent-blue, #007AFF); font-size: 12px;
  cursor: pointer; transition: all 0.15s; text-align: left;
}
.sig-save-current-btn:hover { background: rgba(0,122,255,0.06); }

/* 保存签名对话框 */
.sig-save-dialog { text-align: center; }
.sig-save-input {
  width: 100%; padding: 9px 12px;
  border: 1px solid var(--border-color); border-radius: 6px;
  background: var(--bg-secondary); color: var(--text-primary);
  font-size: 13px; outline: none; box-sizing: border-box;
  margin-bottom: 12px;
}
.sig-save-input:focus { border-color: var(--accent-blue, #007AFF); }

/* ==================== 手机端签名面板覆盖（必须在桌面端之后） ==================== */
@media (max-width: 768px) {
  /* 签名面板：底部抽屉，position:fixed 绕过祖先 overflow:hidden 裁剪 */
  .sig-panel {
    position: fixed !important;
    left: 0 !important;
    right: 0 !important;
    bottom: 0 !important;
    top: auto !important;
    width: 100% !important;
    max-height: 70vh !important;
    margin-top: 0 !important;
    border-radius: 12px 12px 0 0 !important;
    z-index: 1000 !important;
    padding: 12px 16px 20px !important;
    box-shadow: 0 -4px 20px rgba(0, 0, 0, 0.15) !important;
  }

  /* 签名编辑对话框：手机端铺满 */
  .sig-customize-modal {
    width: 90vw !important;
  }
}
</style>
