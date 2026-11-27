import { test } from "node:test";
import assert from "node:assert/strict";
import { CHECKS, recordPing, evaluate, applyChanges, sameSecret } from "../src/logic.js";

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
