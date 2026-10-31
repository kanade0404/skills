import { GuardedCoordinator } from "../poc133-broker-boundary-20261008/guarded.js";
export class RecoveryCoordinator extends GuardedCoordinator {
  // ローカルfixtureの管理経路専用。worker向けgatewayからは到達不可。
  reconcileComplete(input) {
    return this.ctx.storage.transactionSync(() => {
      const row = this.sql.exec("SELECT * FROM operations WHERE op=?", input.op).toArray()[0];
      if (!row) return { status: "rejected", reason: "unknown-operation" };
      if (["payload", "worker", "incarnation", "epoch"].some(key => row[key] !== input[key])) {
        return { status: "rejected", reason: "operation-mismatch" };
      }
      if (row.status === "complete") {
        return row.result === input.result ? { status: "complete", result: row.result }
          : { status: "rejected", reason: "receipt-mismatch" };
      }
      const lease = this.sql.exec("SELECT * FROM lease WHERE id=1").toArray()[0];
      if (["worker", "incarnation", "epoch"].some(key => lease[key] !== input[key])) {
        return { status: "rejected", reason: "generation" };
      }
      if (typeof input.result !== "string" || !input.result) return { status: "rejected", reason: "receipt" };
      this.sql.exec("UPDATE operations SET status='complete', result=? WHERE op=?", input.result, input.op);
      return { status: "complete", result: input.result };
    });
  }
}
