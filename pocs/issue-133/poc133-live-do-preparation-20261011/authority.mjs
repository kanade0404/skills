import { WorkerEntrypoint } from 'cloudflare:workers';
import { LifecycleCoordinator } from '../poc133-lease-restart-20261009/coordinator.js';
import { readConfig, scopeFor } from './scope.mjs';
// Trial-only subclass. Never load this class into an existing namespace.
export class IsolatedCoordinator extends LifecycleCoordinator {
  now() { return Date.now(); }
  setClock() { return {status:'denied'}; }
  seed() { return {status:'denied'}; }
  grant() { return {status:'denied'}; }
  renew() { return {status:'denied'}; }
  write() { return {status:'denied'}; }
  finish() { return {status:'denied'}; }
  reconcileComplete() { return {status:'denied'}; }
  crash() { return {status:'denied'}; }
  reserve() { return {status:'denied'}; }
  managementOpen() { return this.env.CONTROL_ENABLED === 'true' && readConfig(this.env) !== null; }
  seal(input) { return this.managementOpen() ? super.seal(input) : {status:'denied'}; }
  confirmStopped(input) { return this.managementOpen() ? super.confirmStopped(input) : {status:'denied'}; }
  takeOver(input) { return this.managementOpen() ? super.takeOver(input) : {status:'denied'}; }
  revoke(input) { return this.managementOpen() ? super.revoke(input) : {status:'denied'}; }
  authorize(input, checkRevision = true) {
    if (!readConfig(this.env)) return 'trial-closed';
    return super.authorize(input,checkRevision);
  }
  prepare(scenario) {
    const scope = scopeFor(this.env,scenario);
    if (this.env.CONTROL_ENABLED !== 'true' || !scope || scenario === 'empty') return {status:'denied'};
    return this.ctx.storage.transactionSync(() => {
      const seed = super.seed({...scope.owner,expires:this.now()+scope.ttl,value:'fixture:no-external-effect'});
      if (seed.status !== 'ok') return seed;
      const grant = super.grant({digest:scope.digest,binding:{...scope.owner,op:scope.op,payload:scope.payload}});
      if (grant.status !== 'ok') throw Error('Fixture initialization failed');
      return {status:'ok'};
    });
  }
}
// The public edge receives this capability, never the DO namespace or Control.
export class Reservations extends WorkerEntrypoint {
  async reserveGrant(message) {
    if (!message || Array.isArray(message) || Object.keys(message).sort().join(',') !== 'digest,request,scenario') return {status:'denied'};
    const scope = scopeFor(this.env,message.scenario);
    if (!scope || message.digest !== scope.digest) return {status:'denied'};
    return this.env.COORD.getByName(scope.lane).reserveGrant({digest:scope.digest,request:message.request});
  }
  async fetch() { return new Response('Not found',{status:404}); }
}
const actions = new Set(['initialize','inspect','drain','takeover','revoke']);
export class Control extends WorkerEntrypoint {
  async invoke(message) {
    if (this.env.CONTROL_ENABLED !== 'true' || !message || Array.isArray(message) ||
        Object.keys(message).sort().join(',') !== 'action,scenario' || !actions.has(message.action)) return {status:'denied'};
    const {action,scenario} = message;
    const scope = scopeFor(this.env,scenario);
    if (!scope || (scenario === 'empty' && action !== 'inspect') || (action === 'takeover' && scenario !== 'stale')) return {status:'denied'};
    const stub = this.env.COORD.getByName(scope.lane);
    if (action === 'initialize') return stub.prepare(scenario);
    if (action === 'inspect') return stub.inspect();
    if (action === 'drain') return stub.seal(scope.owner);
    if (action === 'revoke') return stub.revoke({digest:scope.digest});
    // No process is running in this PoC. This is not an OS-stop attestation.
    const proof = await stub.confirmStopped({...scope.owner,run:'fixture:no-process'});
    if (proof.status !== 'ok') return proof;
    return stub.takeOver({...scope.owner,nextIncarnation:this.env.RUN_ID+'-b',ttl:scope.ttl});
  }
  async fetch() { return new Response('Not found',{status:404}); }
}
export default { fetch:() => new Response('Not found',{status:404}) };
