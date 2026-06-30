/** 邮件相关公共工具函数 */

// 期望输入格式："张三 <zhangsan@qq.com>" 或纯 "zhangsan@qq.com"
/** 从邮箱地址提取显示名 */
export function extractName(addr: string): string {
  if (!addr) return '未知'
  const match = addr.match(/^(.+?)\s*<.*>$/)
  if (match) return match[1].replace(/"/g, '').trim()
  return addr.split('@')[0]
}

/** 获取头像首字母 */
export function getInitial(addr: string): string {
  const name = extractName(addr)
  return name.charAt(0).toUpperCase()
}

/** 根据邮箱地址生成头像颜色 */
const AVATAR_COLORS = ['#007AFF', '#34C759', '#FF9500', '#FF3B30', '#AF52DE', '#5AC8FA', '#FF2D55', '#64D2FF']
export function getAvatarColor(addr: string): string {
  let hash = 0
  for (let i = 0; i < (addr || '').length; i++) {
    hash = addr.charCodeAt(i) + ((hash << 5) - hash)
  }
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length]
}

/** 格式化邮件时间（列表用，简洁格式） */
export function formatDate(dateStr: string): string {
  if (!dateStr) return ''
  try {
    const d = new Date(dateStr)
    const now = new Date()
    const isToday = d.toDateString() === now.toDateString()
    if (isToday) return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    const isThisYear = d.getFullYear() === now.getFullYear()
    if (isThisYear) return `${d.getMonth() + 1}月${d.getDate()}日`
    return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`
  } catch {
    return dateStr
  }
}

/** 格式化邮件时间（详情页用，完整格式） */
export function formatDetailDate(dateStr: string): string {
  if (!dateStr) return ''
  try {
    const d = new Date(dateStr)
    return d.toLocaleString('zh-CN', {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      hour12: false
    })
  } catch {
    return dateStr
  }
}

/** 格式化文件大小 */
export function formatFileSize(bytes: number): string {
  if (!bytes || bytes <= 0) return ''
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

/** 下载附件 */
export function downloadAttachment(params: {
  messageId: string
  accountId: string
  folder: string
  partNumber: number
  filename: string
}): void {
  const { messageId, accountId, folder, partNumber, filename } = params
  // 使用 /app/flymail/api 前缀，确保走 Vite 开发代理（vite.config.ts 匹配 /app/flymail/api）
  // 生产环境通过 StripPrefixMiddleware 自动剥离 /app/flymail 前缀
  const basePath = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '')
  const url = `${basePath || ''}/api/messages/${messageId}/attachments/${partNumber}?account_id=${accountId}&folder=${encodeURIComponent(folder)}`
  // 通过创建临时 a 元素触发浏览器原生下载
  const link = document.createElement('a')
  link.href = url
  link.download = filename || 'attachment'
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}

/** 获取文件夹显示数量（收件箱显示未读数，其他显示总数） */
export function getFolderCount(folder: { name?: string; path?: string; unread_count?: number; total_count?: number }): number {
  if (!folder) return 0
  // 兼容两种判断方式：按中文名或按 IMAP 路径
  const isInbox = folder.name === '收件箱' || folder.path?.toUpperCase() === 'INBOX'
  return isInbox ? (folder.unread_count || 0) : (folder.total_count || 0)
}
