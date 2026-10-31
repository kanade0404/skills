import test from 'node:test';
import assert from 'node:assert/strict';
import { verifySnapshot } from './snapshot-guard.mjs';

const state = Buffer.from(JSON.stringify({ serial: 44, lineage: 'fixture', resources: [
  { mode: 'managed', module: 'module.trial', type: 'worker', name: 'trial', instances: [{ index_key: 'authority' }] },
] }));
const plan = { resource_changes: [
  { mode: 'managed', address: 'module.trial.worker.trial["authority"]', change: { actions: ['no-op'] } },
  { mode: 'managed', address: 'new.version', change: { actions: ['create'] } },
] };
test('binds no-op identities to the exact state bytes', () => {
  const result = verifySnapshot(state, state, plan);
  assert.equal(result.serial, 44);
  assert.deepEqual(result.addresses, ['module.trial.worker.trial["authority"]']);
  assert.match(result.sha256, /^[0-9a-f]{64}$/);
});
test('rejects a stale state copy', () => {
  assert.throws(() => verifySnapshot(state, Buffer.from(state.toString().replace('44', '43')), plan), /copy/);
});
test('rejects an equally sized but different no-op address set', () => {
  const other = structuredClone(plan);
  other.resource_changes[0].address = 'another.worker';
  assert.throws(() => verifySnapshot(state, state, other), /identities/);
});
test('rejects changed existing resources', () => {
  const other = structuredClone(plan);
  other.resource_changes[0].change.actions = ['update'];
  assert.throws(() => verifySnapshot(state, state, other), /identities/);
});
