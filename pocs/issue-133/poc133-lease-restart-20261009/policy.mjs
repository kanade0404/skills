// Throwaway policy probe, not production authorization.
export function ownerMatches(lease, input) {
  return !!lease && ["worker", "incarnation", "epoch"].every(k => lease[k] === input[k]);
}
export function renewalReason(lease, input, now, draining) {
  if (!ownerMatches(lease, input)) return "generation";
  if (now >= lease.expires) return "expired";
  if (draining) return "draining";
  if (input.revision !== lease.revision) return "revision";
  if (!Number.isSafeInteger(input.ttl) || input.ttl < 1 || input.ttl > 60000) return "ttl";
  if (now + input.ttl <= lease.expires) return "not-extension";
  return null;
}
export function takeoverReason(lease, input, draining, proof, pending) {
  if (!ownerMatches(lease, input)) return "generation";
  if (input.revision !== lease.revision) return "revision";
  if (!draining) return "admission-open";
  if (!proof || !ownerMatches(lease, proof) || !proof.run) return "stop-unconfirmed";
  if (pending) return "pending-operation";
  if (!input.nextIncarnation || input.nextIncarnation === lease.incarnation) return "incarnation";
  if (!Number.isSafeInteger(input.ttl) || input.ttl < 1 || input.ttl > 60000) return "ttl";
  return null;
}
export class Watchdog {
  constructor(deadline) { this.deadline = deadline; this.halted = false; }
  observe(now, reply) {
    if (this.halted || now >= this.deadline || reply?.status !== "ok") this.halted = true;
    return this.halted ? "stop" : "run";
  }
}
