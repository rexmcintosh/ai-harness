import { readFileSync } from 'node:fs'

export const CONFIG_DIR = `${process.env.HOME}/.config/session-bridge`
export const STATE_DIR = `${process.env.HOME}/.local/state/session-bridge`

export interface Config {
  groupId: number
  allowedUserId: number
  excludePatterns: string[]
  excludeRegexps: RegExp[]
  pollTimeoutSec: number
  topicSyncIntervalSec: number
}

function requireNumber(o: any, field: string, path: string): number {
  const v = o[field]
  if (typeof v !== 'number' || !Number.isFinite(v)) {
    throw new Error(`session-bridge config ${path}: "${field}" must be a number`)
  }
  return v
}

function optionalNumber(o: any, field: string, path: string, fallback: number): number {
  if (o[field] === undefined) return fallback
  return requireNumber(o, field, path)
}

// Compile at load so a bad pattern fails the daemon at startup, not mid-sync.
function compileExcludes(o: any, path: string): { patterns: string[]; regexps: RegExp[] } {
  const raw = o.excludePatterns ?? []
  if (!Array.isArray(raw)) {
    throw new Error(`session-bridge config ${path}: "excludePatterns" must be an array of strings`)
  }
  const regexps: RegExp[] = []
  for (const p of raw) {
    if (typeof p !== 'string') {
      throw new Error(`session-bridge config ${path}: "excludePatterns" must be an array of strings`)
    }
    try {
      regexps.push(new RegExp(p))
    } catch (e) {
      throw new Error(`session-bridge config ${path}: "excludePatterns" entry ${JSON.stringify(p)} is not a valid regexp: ${e}`)
    }
  }
  return { patterns: raw as string[], regexps }
}

export function loadConfig(path = `${CONFIG_DIR}/config.json`): Config {
  let raw: string
  try {
    raw = readFileSync(path, 'utf8')
  } catch {
    throw new Error(`session-bridge config not readable at ${path}`)
  }
  let parsed: any
  try {
    parsed = JSON.parse(raw)
  } catch (e) {
    throw new Error(`session-bridge config at ${path} is not valid JSON: ${e}`)
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(`session-bridge config at ${path} must be a JSON object`)
  }
  const { patterns, regexps } = compileExcludes(parsed, path)
  return {
    groupId: requireNumber(parsed, 'groupId', path),
    allowedUserId: requireNumber(parsed, 'allowedUserId', path),
    excludePatterns: patterns,
    excludeRegexps: regexps,
    pollTimeoutSec: optionalNumber(parsed, 'pollTimeoutSec', path, 50),
    topicSyncIntervalSec: optionalNumber(parsed, 'topicSyncIntervalSec', path, 30),
  }
}

export function loadToken(path = `${CONFIG_DIR}/.env`): string {
  const raw = readFileSync(path, 'utf8')
  const m = raw.match(/^\s*(?:export\s+)?TELEGRAM_BOT_TOKEN\s*=\s*(.*)$/m)
  if (!m) throw new Error(`TELEGRAM_BOT_TOKEN not found in ${path}`)
  const value = m[1].trim()
  // Strip one matching pair of surrounding quotes, if present.
  const unquoted = /^(["'])(.*)\1$/.exec(value)
  const token = (unquoted ? unquoted[2] : value).trim()
  if (token === '') throw new Error(`TELEGRAM_BOT_TOKEN is empty in ${path}`)
  return token
}
