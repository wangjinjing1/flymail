/** 两次点击确认操作 composable，防止误删 */
import { ref } from 'vue'

// 3 秒内再次点击才触发确认操作，超时后需重新开始
export function useConfirmAction(timeout = 3000) {
  /** 当前确认目标（非 null 表示正在等待二次确认） */
  const confirmTarget = ref<string | null>(null)
  const confirmTimer = ref<ReturnType<typeof setTimeout> | null>(null)

  /** 请求确认，返回 true 表示已确认可执行操作，false 表示需要再次确认 */
  function requestConfirm(id: string): boolean {
    // 同一目标第二次点击 → 确认执行
    if (confirmTarget.value === id) {
      clearConfirm()
      return true
    }
    // 第一次点击，进入确认状态
    confirmTarget.value = id
    if (confirmTimer.value) clearTimeout(confirmTimer.value)
    confirmTimer.value = setTimeout(() => {
      confirmTarget.value = null
    }, timeout)
    return false
  }

  /** 清除确认状态 */
  function clearConfirm() {
    confirmTarget.value = null
    if (confirmTimer.value) {
      clearTimeout(confirmTimer.value)
      confirmTimer.value = null
    }
  }

  return {
    confirmTarget,
    requestConfirm,
    clearConfirm,
  }
}
