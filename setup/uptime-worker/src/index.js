// bebop-uptime: the outside alarm. The Mac mini guard and the VM ping it; every
// 5 minutes Cloudflare runs the check and sends Rex a Telegram message when a ping
// is late (DOWN) or comes back (UP). State lives in one Durable Object.
//
//   GET|POST /ping/<check>/<PING_SECRET>   record a check-in (mini-guard, bebop-vm)
//   GET      /status/<PING_SECRET>         JSON: last cron run, checks, recent events
import { DurableObject } from "cloudflare:workers";
import { CHECKS, recordPing, evaluate, applyChanges, sameSecret } from "./logic.js";

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj, null, 1), { status, headers: { "content-type": "application/json" } });
const notFound = () => new Response("not found\n", { status: 404 });
const nowS = () => Math.floor(Date.now() / 1000);

export class Beacon extends DurableObject {
  async load() {
    return (await this.ctx.storage.get("state")) || {};
  }

  async save(state) {
    await this.ctx.storage.put("state", state);
  }

  async ping(name) {
    await this.save(recordPing(await this.load(), name, nowS()));
  }

  async status() {
    const state = await this.load();
    return { now: nowS(), alerts: Boolean(this.env.TELEGRAM_TOKEN), ...state };
  }

  // One cron pass. A change is stored as announced only after Telegram took the
  // message, so a failed send is retried on the next pass and never repeated after.
  async tick() {
    const t = nowS();
    let state = { ...(await this.load()), lastCron: t };
    const { changes, text } = evaluate(state, t);
    if (text) {
      if (!this.env.TELEGRAM_TOKEN) {
        state = applyChanges(state, changes, t, "alerts off");
      } else if (await this.send(text)) {
        state = applyChanges(state, changes, t, "sent");
        state.lastSendError = null;
      } else {
        state.lastSendError = t;
      }
    }
    await this.save(state);
  }

  async send(text) {
    try {
      const r = await fetch(`https://api.telegram.org/bot${this.env.TELEGRAM_TOKEN}/sendMessage`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ chat_id: this.env.TELEGRAM_CHAT_ID, text }),
        signal: AbortSignal.timeout(10000),
      });
      return r.ok;
    } catch {
      return false;
    }
  }
}

const beacon = (env) => env.BEACON.get(env.BEACON.idFromName("main"));

export default {
  async fetch(request, env) {
    if (!["GET", "POST", "HEAD"].includes(request.method)) return notFound();
    const parts = new URL(request.url).pathname.split("/").filter(Boolean);
    if (parts[0] === "ping" && parts.length === 3 && CHECKS[parts[1]] && sameSecret(parts[2], env.PING_SECRET)) {
      await beacon(env).ping(parts[1]);
      return new Response("ok\n");
    }
    if (parts[0] === "status" && parts.length === 2 && sameSecret(parts[1], env.PING_SECRET)) {
      return json(await beacon(env).status());
    }
    return notFound();
  },

  async scheduled(_event, env, ctx) {
    ctx.waitUntil(beacon(env).tick());
  },
};
