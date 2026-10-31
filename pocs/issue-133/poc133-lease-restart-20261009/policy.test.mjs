import test from "node:test";
import assert from "node:assert/strict";
import { renewalReason, takeoverReason, Watchdog } from "./policy.mjs";
const lease = { worker: "worker-a", incarnation: "run-a", epoch: 4, revision: 8, expires: 2000 };
const input = { ...lease, ttl: 2000, nextIncarnation: "run-b" };
test("renewal extends the live current owner only", () => assert.equal(renewalReason(lease, input, 1000, false), null));
for (const now of [1999, 2000, 2001]) test(`expiry boundary ${now}`, () => {
  assert.equal(renewalReason(lease, input, now, false), now === 1999 ? null : "expired");
});
for (const [key, value] of [["worker", "other"], ["incarnation", "other"], ["epoch", 3], ["epoch", 5]]) {
  test(`renewal rejects mismatched ${key} ${value}`, () => assert.equal(renewalReason(lease, { ...input, [key]: value }, 1000, false), "generation"));
}
for (const ttl of [0, -1, 1.5, 60001, null]) test(`invalid renewal ttl ${ttl}`, () => assert.equal(renewalReason(lease, { ...input, ttl }, 1000, false), "ttl"));
for (const ttl of [999, 1000]) test(`renewal must extend ${ttl}`, () => assert.equal(renewalReason(lease, { ...input, ttl }, 1000, false), "not-extension"));
for (const ttl of [1, 60000]) test(`ttl inclusive boundary ${ttl}`, () => assert.equal(renewalReason({ ...lease, expires: 1000 }, { ...input, ttl }, 999, false), ttl === 1 ? "not-extension" : null));
test("one millisecond extension is accepted", () => assert.equal(renewalReason(lease, { ...input, ttl: 1001 }, 1000, false), null));
test("draining renewal is denied", () => assert.equal(renewalReason(lease, input, 1000, true), "draining"));
test("delayed renewal revision is denied", () => assert.equal(renewalReason(lease, { ...input, revision: 7 }, 1000, false), "revision"));
test("uninitialized renewal is denied", () => assert.equal(renewalReason(null, input, 1000, false), "generation"));
const proof = { ...lease, run: "controller-observed-exit" };
test("sealed stopped reconciled owner can be replaced", () => assert.equal(takeoverReason(lease, input, true, proof, false), null));
for (const ttl of [1, 60000]) test(`takeover accepts ttl boundary ${ttl}`, () => assert.equal(takeoverReason(lease, { ...input, ttl }, true, proof, false), null));
for (const ttl of [-1, 1.5, null]) test(`takeover rejects malformed ttl ${ttl}`, () => assert.equal(takeoverReason(lease, { ...input, ttl }, true, proof, false), "ttl"));
for (const [name, changed, sealed, stopped, pending, expected] of [
  ["open", input, false, proof, false, "admission-open"],
  ["unknown stop", input, true, null, false, "stop-unconfirmed"],
  ["wrong stop generation", input, true, { ...proof, epoch: 3 }, false, "stop-unconfirmed"],
  ["missing run", input, true, { ...proof, run: "" }, false, "stop-unconfirmed"],
  ["pending", input, true, proof, true, "pending-operation"],
  ["stale caller", { ...input, epoch: 3 }, true, proof, false, "generation"],
  ["stale revision", { ...input, revision: 7 }, true, proof, false, "revision"],
  ["same incarnation", { ...input, nextIncarnation: "run-a" }, true, proof, false, "incarnation"],
  ["empty incarnation", { ...input, nextIncarnation: "" }, true, proof, false, "incarnation"],
  ["zero ttl", { ...input, ttl: 0 }, true, proof, false, "ttl"],
  ["over ttl", { ...input, ttl: 60001 }, true, proof, false, "ttl"],
]) test(`takeover holds ${name}`, () => assert.equal(takeoverReason(lease, changed, sealed, stopped, pending), expected));
for (const now of [99, 100, 101]) test(`local watchdog boundary ${now}`, () => {
  const watchdog = new Watchdog(100);
  assert.equal(watchdog.observe(now, { status: "ok" }), now === 99 ? "run" : "stop");
});
test("communication failure latches local stop even after a late successful reply", () => {
  const watchdog = new Watchdog(100);
  assert.equal(watchdog.observe(50, null), "stop", "missing ack closes local admission");
  assert.equal(watchdog.observe(60, { status: "ok" }), "stop", "late ack cannot resurrect stopped execution");
});
test("expired watchdog stays stopped even if clock moves backwards", () => {
  const watchdog = new Watchdog(100);
  assert.equal(watchdog.observe(100, { status: "ok" }), "stop", "deadline is exclusive");
  assert.equal(watchdog.observe(90, { status: "ok" }), "stop", "stop is latched");
});
