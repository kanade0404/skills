import { Coordinator } from "../poc133-do-20261005/coordinator.js";
// 使い捨ての補完案。pendingの解消・復旧認証は別の検証対象。
export class GuardedCoordinator extends Coordinator {
  takeOver(input) {
    return this.ctx.storage.transactionSync(() => {
      const reason = this.authorize(input);
      if (reason) return { status: "rejected", reason };
      const pending = this.sql.exec("SELECT op FROM operations WHERE status='pending' LIMIT 1").toArray();
      if (pending.length) return { status: "needs-reconcile", reason: "pending-operation" };
      return super.takeOver(input);
    });
  }
}
