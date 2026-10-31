import { RecoveryCoordinator } from "../poc133-capability-recovery-20261008/coordinator.js";
import { renewalReason, takeoverReason, ownerMatches } from "./policy.mjs";
export class LifecycleCoordinator extends RecoveryCoordinator {
  constructor(ctx, env) {
    super(ctx, env);
    ctx.blockConcurrencyWhile(async () => {
      this.sql.exec("CREATE TABLE IF NOT EXISTS lifecycle (id INTEGER PRIMARY KEY, draining INTEGER, proof TEXT, clock INTEGER)");
      this.sql.exec("INSERT OR IGNORE INTO lifecycle VALUES (1, 0, NULL, NULL)");
      this.sql.exec("CREATE TABLE IF NOT EXISTS grants (digest TEXT PRIMARY KEY, binding TEXT, revoked INTEGER)");
    });
  }
  lease() { return this.sql.exec("SELECT * FROM lease WHERE id=1").toArray()[0]; }
  lifecycle() { return this.sql.exec("SELECT * FROM lifecycle WHERE id=1").one(); }
  now() { return this.lifecycle().clock ?? Date.now(); }
  // Test-only authority clock, never supplied through the worker gateway.
  setClock({ now }) {
    if (!Number.isSafeInteger(now) || now < 0 || (this.lifecycle().clock !== null && now < this.now())) throw Error("invalid clock");
    this.sql.exec("UPDATE lifecycle SET clock=? WHERE id=1", now);
    return { status: "ok" };
  }
  authorize(input, checkRevision = true) {
    const lease = this.lease();
    if (!ownerMatches(lease, input)) return "generation";
    if (this.now() >= lease.expires) return "expired";
    if (this.lifecycle().draining) return "draining";
    if (checkRevision && input.revision !== lease.revision) return "revision";
    return null;
  }
  renew(input) {
    return this.ctx.storage.transactionSync(() => {
      const now = this.now(), reason = renewalReason(this.lease(), input, now, this.lifecycle().draining);
      if (reason) return { status: "rejected", reason };
      this.sql.exec("UPDATE lease SET expires=?, revision=revision+1 WHERE id=1", now + input.ttl);
      return { status: "ok", expires: now + input.ttl, revision: input.revision + 1 };
    });
  }
  seal(input) {
    if (!ownerMatches(this.lease(), input)) return { status: "rejected", reason: "generation" };
    this.sql.exec("UPDATE lifecycle SET draining=1 WHERE id=1");
    return { status: "ok" };
  }
  // Controller fixture accepts a proof only after the harness observes OS exit.
  confirmStopped(input) {
    if (!ownerMatches(this.lease(), input) || !this.lifecycle().draining || !input.run) return { status: "rejected" };
    this.sql.exec("UPDATE lifecycle SET proof=? WHERE id=1", JSON.stringify(input));
    return { status: "ok" };
  }
  takeOver(input) {
    return this.ctx.storage.transactionSync(() => {
      const life = this.lifecycle();
      const pending = this.sql.exec("SELECT op FROM operations WHERE status='pending' LIMIT 1").toArray().length;
      const reason = takeoverReason(this.lease(), input, life.draining, JSON.parse(life.proof), pending);
      if (reason) return { status: "rejected", reason };
      this.sql.exec("UPDATE lease SET incarnation=?, epoch=epoch+1, revision=revision+1, expires=? WHERE id=1", input.nextIncarnation, this.now() + input.ttl);
      this.sql.exec("UPDATE lifecycle SET draining=0, proof=NULL WHERE id=1");
      return { status: "ok", epoch: input.epoch + 1, revision: input.revision + 1 };
    });
  }
  grant({ digest, binding }) {
    if (this.authorize(binding, false)) return { status: "rejected" };
    this.sql.exec("INSERT OR IGNORE INTO grants VALUES (?, ?, 0)", digest, JSON.stringify(binding));
    return { status: "ok" };
  }
  revoke({ digest }) {
    this.sql.exec("UPDATE grants SET revoked=1 WHERE digest=?", digest);
    return { status: "ok" };
  }
  reserveGrant({ digest, request }) {
    return this.ctx.storage.transactionSync(() => {
      const row = this.sql.exec("SELECT * FROM grants WHERE digest=?", digest).toArray()[0];
      if (!row || row.revoked) return { status: "denied" };
      const binding = JSON.parse(row.binding);
      if (!request || Object.keys(request).sort().join(",") !== "op,payload" || request.op !== binding.op || request.payload !== binding.payload) return { status: "denied" };
      return super.reserve(binding);
    });
  }
  inspect() {
    return { ...super.snapshot(), lifecycle: this.lifecycle(), grants: this.sql.exec("SELECT digest, revoked FROM grants ORDER BY digest").toArray() };
  }
}
