# bebop-uptime (the outside alarm)

Everything else that alerts Rex runs inside the house. If house power or internet
fails, none of it can send a message. This Cloudflare Worker runs outside the house
and notices silence: the Mac mini guard and the VM ping it, and every 5 minutes it
checks for late pings and messages Rex on Telegram.

Design context: [docs/uptime-design-2026-11-27.md](../../docs/uptime-design-2026-11-27.md)
(it chose healthchecks.io; Rex chose to build on the existing Cloudflare account instead,
2026-11-27).

## What it does

| Check | Pinged by | Late after | Message |
| --- | --- | --- | --- |
| `mini-guard` | the Mac mini guard, after every 2-minute tick (`healthcheck_url` in its config) | 17 min | `DOWN: no check-in from the Mac mini guard ...` |
| `bebop-vm` | VM cron, every 5 minutes (`~/.local/bin/bebop-uptime-ping`) | 20 min | `DOWN: no check-in from the server (bebop-vm) ...` |

- One DOWN when a check goes late, one UP when it checks in again. Delivery is at least
  once: in a rare failure after Telegram took a message, it can arrive twice.
- If every check is down, the message adds: likely house power or internet.
- A check that has never pinged is ignored, so a fresh deploy never alarms.
- A message counts as sent only when Telegram accepts it; otherwise the next 5-minute
  pass tries again.
- Without `TELEGRAM_TOKEN` the Worker still tracks state and logs changes as
  `alerts off` (see `/status`), but sends nothing.
- A planned VM or mini restart longer than 15 minutes sends DOWN then UP. That is expected.

## Who watches the alarm

The VM watchdog (`check_outside_alarm`, every 30 minutes) reads `/status`:
no cron pass for 20 minutes is crit; a missing secret file, unreachable or unreadable
status, no Telegram token, a failed send, or a check the Worker reports as down is warn.

## Paths

`https://bebop-uptime.rex-651.workers.dev`. Every request needs
`Authorization: Bearer <PING_SECRET>`; the secret is never in a URL, so Cloudflare's
request logs never hold it.

- `POST /ping/<check>` records a check-in (a `GET` never changes anything).
- `GET /status` returns JSON: `lastCron`, `alerts`, `lastSendError`, `checks`, recent `log`.
- Anything else, or a wrong secret, is 404.

Cloudflare refuses Python's default `User-Agent` (error 1010); mini-guard sends
`mini-guard/1`.

To change the secret: write a new `~/.config/bebop-uptime/secret`, run the
`wrangler secret put PING_SECRET` line below, then `install.sh` (it updates the mini).

## Files

| Where | What |
| --- | --- |
| `src/logic.js` | pure alarm logic (tested with `npm test`) |
| `src/index.js` | Worker: routes, cron, the `Beacon` Durable Object (state store) |
| `wrangler.jsonc` | name, cron `*/5 * * * *`, Durable Object, `TELEGRAM_CHAT_ID` |
| `vm-ping.sh` | the VM ping, installed as `~/.local/bin/bebop-uptime-ping` |
| `install.sh` | installs the VM ping cron line and sets the mini's `healthcheck_url`; `--undo` |
| VM `~/.config/bebop-uptime/secret` | `PING_SECRET` (0600); also the watchdog's key to `/status` |
| mini `~/.config/mini-guard/config.json` | `healthcheck_url` (the ping URL) and `healthcheck_token` (the secret) (0600) |

## Deploy and secrets (from the VM)

    cd setup/uptime-worker && npm install
    set -a; . ~/.env; set +a          # CLOUDFLARE_API_TOKEN (Workers edit) and CLOUDFLARE_ACCOUNT_ID
    npm run deploy                    # tests, then wrangler deploy
    tr -d '\n' < ~/.config/bebop-uptime/secret | npx wrangler secret put PING_SECRET
    npx wrangler secret put TELEGRAM_TOKEN     # the alert-only bot, once it exists
    setup/uptime-worker/install.sh

`CLOUDFLARE_API_TOKEN` can deploy Workers but cannot create D1 databases, which is why
state lives in a Durable Object. New cron triggers can take up to 15 minutes to start.

## Drill

1. On the VM, comment out the `bebop-uptime-ping` cron line.
2. Within about 25 minutes Rex gets `DOWN: no check-in from the server`.
3. Put the line back; within 5 minutes Rex gets `UP`.
4. `/status` shows both events in `log`.
