import { expect, test } from 'bun:test'
import { mkdtempSync, writeFileSync, chmodSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { sessionGeneration } from '../src/tmux'

test('session generation uses tmux session, pane, and pane creation identities', () => {
  const d = mkdtempSync(join(tmpdir(), 'sb-tmux-'))
  const fake = join(d, 'tmux')
  writeFileSync(fake, '#!/usr/bin/env bash\nprintf "\\$1:@7:1720000000\\n"\n')
  chmodSync(fake, 0o755)
  const oldCommand = process.env.SESSION_BRIDGE_TMUX
  const old = process.env.PATH
  process.env.PATH = `${d}:${old}`
  process.env.SESSION_BRIDGE_TMUX = fake
  try { expect(sessionGeneration('demo')).toBe('$1:@7:1720000000') }
  finally { process.env.PATH = old; if (oldCommand === undefined) delete process.env.SESSION_BRIDGE_TMUX; else process.env.SESSION_BRIDGE_TMUX = oldCommand }
})
