import { defineStore } from 'pinia';
import { ref } from 'vue';

/** Toast 提示类型 */
export type ToastType = 'success' | 'error' | 'warning' | 'info';

/** Toast 提示项 */
export interface ToastItem {
  id: number;
  message: string;
  type: ToastType;
}

/** Confirm 确认框选项 */
export interface ConfirmOptions {
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
}

let toastId = 0;

export const useUIStore = defineStore('ui', () => {
  // ==================== Toast ====================
  const toasts = ref<ToastItem[]>([]);

  /** 显示 Toast 提示 */
  function showToast(message: string, type: ToastType = 'info', duration = 3000) {
    const id = ++toastId;
    toasts.value.push({ id, message, type });
    setTimeout(() => {
      toasts.value = toasts.value.filter(t => t.id !== id);
    }, duration);
  }

  /** 快捷方法：成功提示 */
  function success(message: string) { showToast(message, 'success'); }
  /** 快捷方法：错误提示 */
  function error(message: string) { showToast(message, 'error', 5000); }
  /** 快捷方法：警告提示 */
  function warning(message: string) { showToast(message, 'warning', 4000); }
  /** 快捷方法：信息提示 */
  function info(message: string) { showToast(message, 'info'); }

  // ==================== Confirm ====================
  const confirmVisible = ref(false);
  const confirmOptions = ref<ConfirmOptions>({
    title: '',
    message: '',
    confirmText: '确定',
    cancelText: '取消',
    danger: false,
  });
  let confirmResolve: ((value: boolean) => void) | null = null;

  /** 显示确认框，返回 Promise，用户点确定返回 true，取消返回 false */
  function showConfirm(options: ConfirmOptions): Promise<boolean> {
    confirmOptions.value = {
      confirmText: '确定',
      cancelText: '取消',
      danger: false,
      ...options,
    };
    confirmVisible.value = true;
    return new Promise((resolve) => {
      confirmResolve = resolve;
    });
  }

  /** 确认框 - 用户点击确定 */
  function confirmOk() {
    confirmVisible.value = false;
    confirmResolve?.(true);
    confirmResolve = null;
  }

  /** 确认框 - 用户点击取消 */
  function confirmCancel() {
    confirmVisible.value = false;
    confirmResolve?.(false);
    confirmResolve = null;
  }

  return {
    toasts, showToast, success, error, warning, info,
    confirmVisible, confirmOptions, showConfirm, confirmOk, confirmCancel,
  };
});
