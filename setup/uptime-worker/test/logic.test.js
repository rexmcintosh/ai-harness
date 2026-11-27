import { test } from "node:test";
import assert from "node:assert/strict";
import { CHECKS, recordPing, evaluate, applyChanges, sameSecret, route, runTick } from "../src/logic.js";

const T0 = 1_800_000_000;
const MIN = 60;

function run(state, now) {
  const { changes, text } = evaluate(state, now);
  return { state: applyChanges(state, changes, now), changes, text };
}

test("nothing has pinged yet: no alarm on first deploy", () => {
  assert.deepEqual(evaluate({}, T0), { changes: [], text: null });
});

test("first ping starts silently as up", () => {
  const s = recordPing({}, "mini-guard", T0);
  assert.equal(s.checks["mini-guard"].announced, "up");
  assert.equal(evaluate(s, T0 + 5 * MIN).text, null);
});

test("late mini-guard: one DOWN, no repeat, then one UP", () => {
  let s = recordPing(recordPing({}, "mini-guard", T0), "bebop-vm", T0);
  s = recordPing(s, "bebop-vm", T0 + 18 * MIN);           // the VM keeps pinging
  let r = run(s, T0 + 18 * MIN);
  assert.deepEqual(r.changes, [{ name: "mini-guard", to: "down" }]);
  assert.match(r.text, /^DOWN: no check-in from the Mac mini guard for 18 min/);
  assert.doesNotMatch(r.text, /house power/);
  r = run(r.state, T0 + 23 * MIN);
  assert.equal(r.text, null);                              // no repeat
  s = recordPing(r.state, "mini-guard", T0 + 25 * MIN);
  r = run(s, T0 + 25 * MIN);
  assert.deepEqual(r.changes, [{ name: "mini-guard", to: "up" }]);
  assert.equal(r.text, "UP: the Mac mini guard checks in again.");
});

test("not late inside the grace time", () => {
  const s = recordPing({}, "bebop-vm", T0);
  assert.equal(evaluate(s, T0 + 20 * MIN).text, null);     // exactly at the limit
  assert.match(evaluate(s, T0 + 20 * MIN + 1).text, /server \(bebop-vm\)/);
});

test("both silent in one pass: one message with the house hint", () => {
  const s = recordPing(recordPing({}, "mini-guard", T0), "bebop-vm", T0);
  const r = run(s, T0 + 21 * MIN);
  assert.equal(r.changes.length, 2);
  assert.equal(r.text.split("\n").length, 3);
  assert.match(r.text, /house power or internet/);
});

test("second check going down after the first adds the house hint", () => {
  let s = recordPing(recordPing({}, "mini-guard", T0), "bebop-vm", T0 + 10 * MIN);
  let r = run(s, T0 + 18 * MIN);                            // mini late, VM fine
  assert.doesNotMatch(r.text, /house power/);
  r = run(r.state, T0 + 31 * MIN);                          // now the VM is late too
  assert.deepEqual(r.changes, [{ name: "bebop-vm", to: "down" }]);
  assert.match(r.text, /house power or internet/);
});

test("only one check ever pinged: no house hint", () => {
  const s = recordPing({}, "mini-guard", T0);
  assert.doesNotMatch(evaluate(s, T0 + 30 * MIN).text, /house power/);
});

test("a change not applied (send failed) is offered again next pass", () => {
  const s = recordPing({}, "mini-guard", T0);
  const first = evaluate(s, T0 + 18 * MIN);
  const again = evaluate(s, T0 + 23 * MIN);                 // state unchanged after the failure
  assert.deepEqual(first.changes, again.changes);
});

test("event log is kept short", () => {
  let s = recordPing({}, "mini-guard", T0);
  for (let i = 0; i < 40; i++) s = applyChanges(s, [{ name: "mini-guard", to: i % 2 ? "up" : "down" }], T0 + i);
  assert.equal(s.log.length, 30);
});

test("secret compare", () => {
  assert.equal(sameSecret("abc", "abc"), true);
  assert.equal(sameSecret("abd", "abc"), false);
  assert.equal(sameSecret("abcd", "abc"), false);
  assert.equal(sameSecret("", ""), false);                 // an unset secret never matches
  assert.equal(sameSecret("x", undefined), false);
});

test("the two checks match the senders", () => {
  assert.deepEqual(Object.keys(CHECKS).sort(), ["bebop-vm", "mini-guard"]);
});

test("routes: secret only in the Authorization header, pings POST only", () => {
  const K = "a1".repeat(32);
  const ok = `Bearer ${K}`;
  assert.deepEqual(route("POST", "/ping/mini-guard", ok, K), { kind: "ping", check: "mini-guard" });
  assert.deepEqual(route("POST", "/ping/bebop-vm/", ok, K), { kind: "ping", check: "bebop-vm" });
  assert.deepEqual(route("GET", "/status", ok, K), { kind: "status" });
  assert.equal(route("GET", "/ping/mini-guard", ok, K), null);      // GET never changes state
  assert.equal(route("HEAD", "/ping/mini-guard", ok, K), null);
  assert.equal(route("POST", "/status", ok, K), null);
  assert.equal(route("POST", "/ping/other", ok, K), null);          // unknown check
  assert.equal(route("POST", "/ping/__proto__", ok, K), null);
  assert.equal(route("POST", `/ping/mini-guard/${K}`, null, K), null); // secret in the path: refused
  assert.equal(route("POST", "/ping/mini-guard", "Bearer nope", K), null);
  assert.equal(route("POST", "/ping/mini-guard", K, K), null);  // no "Bearer "
  assert.equal(route("GET", "/status", "Bearer ", ""), null);          // unset secret never matches
  assert.equal(route("GET", "/status", ok, undefined), null);
  assert.equal(route("GET", "/status", "Bearer short", "short"), null); // only the 64-hex format works
});

function store(initial) {
  let state = structuredClone(initial);
  return { load: async () => structuredClone(state), save: async (x) => { state = structuredClone(x); }, get: () => state };
}
const late = () => recordPing(recordPing({}, "mini-guard", T0), "bebop-vm", T0 + 15 * MIN);

test("tick: failed send is kept, retried, then recorded once", async () => {
  const db = store(late());
  const sent = [];
  let up = false;
  const send = async (t) => { if (up) sent.push(t); return up; };
  assert.equal(await runTick({ ...db, send, alertsOn: true, nowS: T0 + 18 * MIN }), "failed");
  assert.equal(db.get().lastSendError, T0 + 18 * MIN);
  assert.equal(db.get().checks["mini-guard"].announced, "up");
  up = true;
  assert.equal(await runTick({ ...db, send, alertsOn: true, nowS: T0 + 23 * MIN }), "sent");
  assert.equal(db.get().lastSendError, null);
  assert.equal(db.get().checks["mini-guard"].announced, "down");
  assert.equal(await runTick({ ...db, send, alertsOn: true, nowS: T0 + 28 * MIN }), "nothing");
  assert.equal(sent.length, 1);
});

test("tick: a failure that clears before the retry leaves no stale error", async () => {
  const db = store(late());
  await runTick({ ...db, send: async () => false, alertsOn: true, nowS: T0 + 18 * MIN });
  await db.save(recordPing(await db.load(), "mini-guard", T0 + 19 * MIN));   // back before the retry
  assert.equal(await runTick({ ...db, send: async () => true, alertsOn: true, nowS: T0 + 23 * MIN }), "nothing");
  assert.equal(db.get().lastSendError, null);
  assert.equal(db.get().lastCron, T0 + 23 * MIN);
});

test("tick: a ping that lands during the send is kept", async () => {
  const db = store(late());
  const send = async () => {
    await db.save(recordPing(await db.load(), "bebop-vm", T0 + 18 * MIN));   // arrives mid-send
    return true;
  };
  await runTick({ ...db, send, alertsOn: true, nowS: T0 + 18 * MIN });
  assert.equal(db.get().checks["bebop-vm"].lastSeen, T0 + 18 * MIN);
  assert.equal(db.get().checks["mini-guard"].announced, "down");
});

test("tick: alerts off records changes without sending", async () => {
  const db = store(late());
  const out = await runTick({ ...db, send: async () => assert.fail("no send"), alertsOn: false, nowS: T0 + 18 * MIN });
  assert.equal(out, "alerts off");
  assert.equal(db.get().log.at(-1).note, "alerts off");
});
