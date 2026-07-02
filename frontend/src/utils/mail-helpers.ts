export function extractName(addr: string): string {
  if (!addr) return 'Unknown'
  const match = addr.match(/^(.+?)\s*<.*>$/)
  if (match) return match[1].replace(/"/g, '').trim()
  return addr.split('@')[0]
}

export function getInitial(addr: string): string {
  const name = extractName(addr)
  return name.charAt(0).toUpperCase()
}

const AVATAR_COLORS = ['#007AFF', '#34C759', '#FF9500', '#FF3B30', '#AF52DE', '#5AC8FA', '#FF2D55', '#64D2FF']

function parseDisplayDate(dateStr: string): Date | null {
  if (!dateStr) return null
  const d = new Date(dateStr)
  if (Number.isNaN(d.getTime()) || d.getTime() <= 0 || d.getFullYear() <= 1970) {
    return null
  }
  return d
}

export function getAvatarColor(addr: string): string {
  let hash = 0
  for (let i = 0; i < (addr || '').length; i++) {
    hash = addr.charCodeAt(i) + ((hash << 5) - hash)
  }
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length]
}

export function formatDate(dateStr: string): string {
  if (!dateStr) return ''
  try {
    const d = parseDisplayDate(dateStr)
    if (!d) return ''
    const now = new Date()
    const isToday = d.toDateString() === now.toDateString()
    if (isToday) {
      return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    }
    const isThisYear = d.getFullYear() === now.getFullYear()
    if (isThisYear) {
      return `${d.getMonth() + 1}/${d.getDate()}`
    }
    return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`
  } catch {
    return dateStr
  }
}

export function formatDetailDate(dateStr: string): string {
  if (!dateStr) return ''
  try {
    const d = parseDisplayDate(dateStr)
    if (!d) return ''
    return d.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    })
  } catch {
    return dateStr
  }
}

export function formatFileSize(bytes: number): string {
  if (!bytes || bytes <= 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function downloadAttachment(params: {
  messageId: string
  accountId: string
  folder: string
  partNumber: number
  filename: string
}): void {
  const { messageId, accountId, folder, partNumber, filename } = params
  const basePath = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '')
  const url = `${basePath || ''}/api/messages/${messageId}/attachments/${partNumber}?account_id=${accountId}&folder=${encodeURIComponent(folder)}`
  const link = document.createElement('a')
  link.href = url
  link.download = filename || 'attachment'
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}

export function getFolderCount(folder: { name?: string; path?: string; unread_count?: number; total_count?: number }): number {
  if (!folder) return 0
  const key = `${folder.name || ''} ${folder.path || ''}`.toLowerCase()
  if (
    key.includes('sent') ||
    key.includes('draft') ||
    key.includes('deleted') ||
    key.includes('trash') ||
    key.includes('\u5df2\u53d1\u9001') ||
    key.includes('\u5df2\u53d1\u90ae\u4ef6') ||
    key.includes('\u8349\u7a3f\u7bb1') ||
    key.includes('\u5df2\u5220\u9664') ||
    key.includes('&xfjt0zab-') ||
    key.includes('&xfjt0zcutvy-') ||
    key.includes('&g0l6p3ux-') ||
    key.includes('&g0l6pw-') ||
    key.includes('&xfjsijzk-') ||
    key.includes('&xfjsijzkkk5o9g-') ||
    key.includes('[gmail]/sent mail') ||
    key.includes('[gmail]/drafts') ||
    key.includes('[gmail]/trash')
  ) {
    return folder.total_count || 0
  }
  return folder.unread_count || 0
}
