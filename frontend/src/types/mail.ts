/** 邮件相关类型定义 */

/** 附件 */
export interface Attachment {
  filename: string
  content_type: string
  size: number
  part_number: number
  content_id: string
  is_inline: boolean
  local_path?: string
}

/** 邮件消息 */
export interface Message {
  id: string
  uid?: number
  from_addr: string
  subject: string
  date: string
  is_read: boolean
  body_text?: string
  body_html?: string
  attachments?: Attachment[]
  has_attachments?: boolean
  account_id?: string
  folder?: string
}
