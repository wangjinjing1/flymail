/** HTML 净化配置，防止 XSS 攻击 */
import DOMPurify from 'dompurify'

// 允许的标签白名单
const ALLOWED_TAGS = [
  'a', 'b', 'br', 'div', 'em',
  // font/bgcolor/face/size 等已废弃标签：为兼容老式邮件客户端的 HTML 邮件而保留
  'font', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'hr', 'i', 'img', 'li', 'ol', 'p', 'pre', 'span', 'strong', 'sub', 'sup',
  'table', 'tbody', 'td', 'th', 'thead', 'tr', 'u', 'ul', 'blockquote', 'cite',
]

// 允许的属性白名单
const ALLOWED_ATTR = [
  'href', 'src', 'alt', 'style', 'class', 'id',
  // 允许 target 属性以支持邮件中的链接在新窗口打开（已知风险：未自动添加 rel="noopener noreferrer"）
  'target',
  'width', 'height', 'color', 'size', 'face',
  'align', 'valign', 'bgcolor', 'colspan', 'rowspan',
]

/** 净化邮件 HTML，防止 XSS 注入（移除 script、事件处理器等危险标签） */
export function sanitizeHtml(html: string | undefined | null): string {
  if (!html) return ''
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOW_DATA_ATTR: false,
  })
}
