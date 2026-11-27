// Pure alarm logic for the bebop-uptime Worker. No Cloudflare APIs here, so it runs
// under `node --test` as well as in the Worker.

// name -> how it is described, and how long without a ping before it counts as down.
// mini-guard pings every 2 min, bebop-vm every 5 min; each gets 15 min of grace.
export const CHECKS = {
  "mini-guard": { label: "the Mac mini guard", lateAfterS: 17 * 60 },
  "bebop-vm": { label: "the server (bebop-vm)", lateAfterS: 20 * 60 },
};

const LOG_KEEP = 30;

export function lisbonTime(epochS) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/Lisbon", weekday: "short", hour: "2-digit", minute: "2-digit",
  }).format(new Date(epochS * 1000));
}

function minutes(s) {
  return Math.max(0, Math.round(s / 60));
}

// Record a ping. A check that pings for the first time starts as "up", silently.
export function recordPing(state, name, nowS) {
  const checks = { ...(state.checks || {}) };
  const prev = checks[name] || {};
  checks[name] = { ...prev, lastSeen: nowS, announced: prev.announced || "up" };
  return { ...state, checks };
}

// Decide what to say. Returns { changes, text } where changes is a list of
// { name, to } to apply once the message is delivered, and text is null when
// there is nothing to say. A check that has never pinged is ignored.
export function evaluate(state, nowS, checks = CHECKS) {
  const seen = state.checks || {};
  const changes = [];
  for (const [name, cfg] of Object.entries(checks)) {
    const c = seen[name];
    if (!c || typeof c.lastSeen !== "number") continue;
    const want = nowS - c.lastSeen > cfg.lateAfterS ? "down" : "up";
    if (want !== (c.announced || "up")) changes.push({ name, to: want });
  }
  if (!changes.length) return { changes, text: null };

  const after = {};
  for (const name of Object.keys(checks)) after[name] = seen[name]?.announced || (seen[name] ? "up" : null);
  for (const ch of changes) after[ch.name] = ch.to;
  const tracked = Object.keys(checks).filter((n) => after[n] !== null);
  const allDown = tracked.length > 1 && tracked.every((n) => after[n] === "down");

  const lines = [];
  const downs = changes.filter((c) => c.to === "down");
  const ups = changes.filter((c) => c.to === "up");
  if (downs.length) {
    for (const { name } of downs) {
      const last = seen[name].lastSeen;
      lines.push(`DOWN: no check-in from ${checks[name].label} for ${minutes(nowS - last)} min `
        + `(last seen ${lisbonTime(last)} Lisbon time).`);
    }
    if (allDown) {
      lines.push("Nothing at home is checking in. The likely cause is house power or internet.");
    }
  }
  for (const { name } of ups) {
    lines.push(`UP: ${checks[name].label} checks in again.`);
  }
  return { changes, text: lines.join("\n") };
}

// Apply delivered changes and note them in the short event log.
export function applyChanges(state, changes, nowS, note = "sent") {
  const checks = { ...(state.checks || {}) };
  const log = [...(state.log || [])];
  for (const { name, to } of changes) {
    checks[name] = { ...checks[name], announced: to };
    log.push({ time: nowS, check: name, to, note });
  }
  return { ...state, checks, log: log.slice(-LOG_KEEP) };
}

// Which request is this? Pure, so it is tested without the Workers runtime.
// Returns { kind: "ping", check } | { kind: "status" } | null (answer 404).
// The secret travels only in "Authorization: Bearer ...", never in the URL, so
// request logs never hold it. Pings must be POST: a GET never changes state.
export function route(method, pathname, authorization, secret) {
  const token = typeof authorization === "string" && authorization.startsWith("Bearer ")
    ? authorization.slice(7) : "";
  if (!sameSecret(token, secret)) return null;
  const parts = pathname.split("/").filter(Boolean);
  if (method === "POST" && parts.length === 2 && parts[0] === "ping" && Object.hasOwn(CHECKS, parts[1])) {
    return { kind: "ping", check: parts[1] };
  }
  if (method === "GET" && parts.length === 1 && parts[0] === "status") return { kind: "status" };
  return null;
}

// Constant-time string compare (for the secret).
export function sameSecret(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || !b) return false;
  const enc = new TextEncoder();
  const x = enc.encode(a);
  const y = enc.encode(b);
  let diff = x.length ^ y.length;
  for (let i = 0; i < y.length; i++) diff |= (x[i] ?? 0) ^ y[i];
  return diff === 0;
}
