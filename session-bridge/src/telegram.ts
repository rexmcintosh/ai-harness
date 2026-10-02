import { chunkText } from './chunk'

export interface TelegramSendReceipt { messageIds: number[] }

export class Telegram {
  constructor(private token: string) {}

  async call(method: string, params: Record<string, unknown> = {}, timeoutMs = 30_000): Promise<any> {
    let res: Response
    try {
      res = await fetch(`https://api.telegram.org/bot${this.token}/${method}`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify(params), signal: AbortSignal.timeout(timeoutMs),
      })
    } catch {
      throw new Error(`telegram ${method}: network failure`)
    }
    let body: any
    try { body = await res.json() } catch { throw new Error(`telegram ${method}: invalid provider response`) }
    if (!res.ok || body?.ok !== true) {
      throw new Error(`telegram ${method} failed: ${body?.error_code ?? res.status} ${body?.description ?? 'provider rejection'}`)
    }
    return body.result
  }

  async getUpdates(offset: number, timeoutSec: number): Promise<any[]> {
    return this.call('getUpdates', { offset, timeout: timeoutSec, allowed_updates: ['message'] }, (timeoutSec + 15) * 1000)
  }

  async send(chatId: number, threadId: number | undefined, text: string): Promise<TelegramSendReceipt> {
    const messageIds: number[] = []
    for (const chunk of chunkText(text)) {
      const result = await this.call('sendMessage', {
        chat_id: chatId, text: chunk,
        ...(threadId !== undefined ? { message_thread_id: threadId } : {}),
      })
      if (!Number.isInteger(result?.message_id)) throw new Error('telegram sendMessage: accepted response has no message_id')
      messageIds.push(result.message_id)
    }
    return { messageIds }
  }

  async createTopic(chatId: number, name: string): Promise<number> {
    const topic = await this.call('createForumTopic', { chat_id: chatId, name: name.slice(0, 128) })
    return topic.message_thread_id
  }

  async renameAndClose(chatId: number, threadId: number, name: string): Promise<void> {
    await this.call('editForumTopic', { chat_id: chatId, message_thread_id: threadId, name: name.slice(0, 128) })
    await this.call('closeForumTopic', { chat_id: chatId, message_thread_id: threadId })
  }
}
