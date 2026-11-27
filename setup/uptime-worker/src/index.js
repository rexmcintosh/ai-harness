// bebop-uptime: the outside alarm. The Mac mini guard and the VM ping it; every
// 5 minutes Cloudflare runs the check and sends Rex a Telegram message when a ping
// is late (DOWN) or comes back (UP). State lives in one Durable Object.
//
//   POST /ping/<check>   record a check-in (mini-guard, bebop-vm)
//   GET  /status         JSON: last cron run, checks, recent events
// Both need "Authorization: Bearer <PING_SECRET>"; anything else is 404.
import { DurableObject } from "cloudflare:workers";
import { recordPing, route, runTick } from "./logic.js";

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

  async tick() {
    return runTick({
      load: () => this.load(),
      save: (state) => this.save(state),
      send: (text) => this.send(text),
      alertsOn: Boolean(this.env.TELEGRAM_TOKEN),
      nowS: nowS(),
    });
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
    const r = route(request.method, new URL(request.url).pathname,
      request.headers.get("authorization"), env.PING_SECRET);
    if (!r) return notFound();
    if (r.kind === "ping") {
      await beacon(env).ping(r.check);
      return new Response("ok\n");
    }
    return json(await beacon(env).status());
  },

  async scheduled(_event, env, ctx) {
    ctx.waitUntil(beacon(env).tick());
  },
};
