import { DurableObject } from "cloudflare:workers";
export class Coordinator extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.instance = crypto.randomUUID();
    ctx.blockConcurrencyWhile(async () => {
      this.sql.exec("CREATE TABLE IF NOT EXISTS lease (id INTEGER PRIMARY KEY CHECK(id=1), worker TEXT, incarnation TEXT, epoch INTEGER, revision INTEGER, expires INTEGER)");
      this.sql.exec("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT)");
      this.sql.exec("CREATE TABLE IF NOT EXISTS operations (op TEXT PRIMARY KEY, payload TEXT, worker TEXT, incarnation TEXT, epoch INTEGER, status TEXT, result TEXT)");
    });
  }
  snapshot() {
    return {
      ...this.sql.exec("SELECT * FROM lease WHERE id=1").toArray()[0],
      value: this.sql.exec("SELECT value FROM state WHERE id=1").toArray()[0]?.value,
      operations: this.sql.exec("SELECT * FROM operations ORDER BY op").toArray(),
      instance: this.instance
    };
  }
  seed(input) {
    return this.ctx.storage.transactionSync(() => {
      if (this.sql.exec("SELECT id FROM lease").toArray().length) return { status: "rejected", reason: "already-initialized" };
      this.sql.exec("INSERT INTO lease VALUES (1, ?, ?, ?, ?, ?)", input.worker, input.incarnation, input.epoch, input.revision, input.expires);
      this.sql.exec("INSERT INTO state VALUES (1, ?)", input.value);
      return { status: "ok" };
    });
  }
  authorize(input, checkRevision = true) {
    const lease = this.sql.exec("SELECT * FROM lease WHERE id=1").toArray()[0];
    if (!lease) return "uninitialized";
    if (lease.worker !== input.worker || lease.incarnation !== input.incarnation || lease.epoch !== input.epoch) return "generation";
    if (Date.now() >= lease.expires) return "expired";
    if (checkRevision && input.revision !== lease.revision) return "revision";
    return null;
  }
  takeOver(input) {
    return this.ctx.storage.transactionSync(() => {
      const reason = this.authorize(input);
      if (reason) return { status: "rejected", reason };
      if (input.nextIncarnation === input.incarnation) return { status: "rejected", reason: "same-incarnation" };
      this.sql.exec("UPDATE lease SET incarnation=?, epoch=epoch+1, revision=revision+1, expires=? WHERE id=1", input.nextIncarnation, input.expires);
      return { status: "ok", epoch: input.epoch + 1, revision: input.revision + 1 };
    });
  }
  write(input) {
    return this.ctx.storage.transactionSync(() => {
      const reason = this.authorize(input);
      if (reason) return { status: "rejected", reason };
      this.sql.exec("UPDATE state SET value=? WHERE id=1", input.value);
      this.sql.exec("UPDATE lease SET revision=revision+1 WHERE id=1");
      return { status: "ok", revision: input.revision + 1 };
    });
  }
  reserve(input) {
    return this.ctx.storage.transactionSync(() => {
      const reason = this.authorize(input, false);
      if (reason) return { status: "rejected", reason };
      const existing = this.sql.exec("SELECT * FROM operations WHERE op=?", input.op).toArray()[0];
      if (existing) {
        if (existing.payload !== input.payload) return { status: "rejected", reason: "payload" };
        if (existing.status === "complete") return { status: "complete", result: existing.result };
        return { status: "needs-reconcile" };
      }
      this.sql.exec("INSERT INTO operations VALUES (?, ?, ?, ?, ?, 'pending', NULL)", input.op, input.payload, input.worker, input.incarnation, input.epoch);
      return { status: "reserved" };
    });
  }
  async finish(input) {
    const result = this.ctx.storage.transactionSync(() => {
      const reason = this.authorize(input, false);
      if (reason) return { status: "rejected", reason };
      const op = this.sql.exec("SELECT * FROM operations WHERE op=?", input.op).toArray()[0];
      if (!op) return { status: "rejected", reason: "unknown-operation" };
      if (op.payload !== input.payload) return { status: "rejected", reason: "payload" };
      if (op.incarnation !== input.incarnation || op.epoch !== input.epoch || op.worker !== input.worker) return { status: "needs-reconcile" };
      if (op.status === "complete") return { status: "complete", result: op.result };
      this.sql.exec("UPDATE operations SET status='complete', result=? WHERE op=?", input.result, input.op);
      return { status: "complete", result: input.result };
    });
    if (input.crashAfterCommit && result.status === "complete") await this.crash();
    return result;
  }
  async crash() {
    await this.ctx.storage.sync();
    this.ctx.abort("POC133: injected reset after durable commit");
  }
}
export default {
  async fetch() { return new Response("Not found", { status: 404 }); }
};
