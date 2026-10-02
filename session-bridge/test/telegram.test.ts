import { afterEach, expect, test } from 'bun:test'
import { Telegram } from '../src/telegram'

const realFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = realFetch })

test('send returns accepted Telegram message IDs', async () => {
  globalThis.fetch = (async () => new Response(JSON.stringify({ ok: true, result: { message_id: 71 } }), { status: 200 })) as any
  const receipt = await new Telegram('fake-token').send(1, 2, 'hello')
  expect(receipt).toEqual({ messageIds: [71] })
})

test('HTTP success with Telegram ok false is rejected', async () => {
  globalThis.fetch = (async () => new Response(JSON.stringify({ ok: false, error_code: 400, description: 'bad' }), { status: 200 })) as any
  await expect(new Telegram('fake-token').send(1, 2, 'hello')).rejects.toThrow('failed')
})
