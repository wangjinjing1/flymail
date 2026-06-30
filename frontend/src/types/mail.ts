/** 邮件相关类型定义 */

/** 附件 */
export interface Attachment {
  filename: string
  content_type: string
  size: number
  part_number: number
  content_id: string // 内联图片的 CID 引用，如 <img src="cid:xxx">
  is_inline: boolean // true=内嵌附件（如邮件正文图片），false=普通附件
}

/** 邮件消息 */
export interface Message {
  id: string
  uid?: number // IMAP UID，用于跨标签页同步等场景
  from_addr: string // 格式："发件人名 <email@domain.com>" 或纯邮箱地址
  subject: string
  date: string
  is_read: boolean
  body_text?: string
  body_html?: string
  attachments?: Attachment[]
  has_attachments?: boolean
  account_id?: string // 聚合视图专用：邮件所属账号 ID
  account_email?: string // 聚合视图专用：邮件所属账号邮箱
  account_provider?: string // 聚合视图专用：邮件所属邮箱提供商
  folder?: string // IMAP 文件夹路径，如 INBOX、Sent Messages
}
