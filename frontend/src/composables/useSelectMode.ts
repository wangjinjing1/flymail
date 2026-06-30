/** 多选模式 composable，管理选中状态和全选逻辑 */
import { ref, computed } from 'vue'

// 使用函数而非直接数组，以便获取响应式更新后的最新值
export function useSelectMode(itemIds: () => string[]) {
  const selectMode = ref(false)
  const selectedIds = ref<Set<string>>(new Set())

  const isAllSelected = computed(() => {
    const ids = itemIds()
    return ids.length > 0 && selectedIds.value.size === ids.length
  })

  /** 进入多选模式（可选传入初始选中项） */
  function enterSelectMode(id?: string) {
    selectMode.value = true
    if (id) {
      selectedIds.value = new Set([id])
    } else {
      selectedIds.value = new Set()
    }
  }

  /** 退出多选模式 */
  function exitSelectMode() {
    selectMode.value = false
    selectedIds.value = new Set()
  }

  /** 切换单个选中 */
  function toggleSelect(id: string) {
    const newSet = new Set(selectedIds.value)
    if (newSet.has(id)) {
      newSet.delete(id)
    } else {
      newSet.add(id)
    }
    selectedIds.value = newSet
  }

  /** 全选/取消全选 */
  function toggleSelectAll() {
    if (isAllSelected.value) {
      selectedIds.value = new Set()
    } else {
      selectedIds.value = new Set(itemIds())
    }
  }

  return {
    selectMode,
    selectedIds,
    isAllSelected,
    enterSelectMode,
    exitSelectMode,
    toggleSelect,
    toggleSelectAll,
  }
}
