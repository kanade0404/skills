import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

export function verifySnapshot(canonicalBytes, copiedBytes, plan) {
  assert.ok(canonicalBytes.equals(copiedBytes), 'State copy differs from canonical snapshot');
  const state = JSON.parse(canonicalBytes);
  const addresses = state.resources.filter(r => r.mode === 'managed').flatMap(r => r.instances.map(i =>
    (r.module ? r.module + '.' : '') + r.type + '.' + r.name +
    (i.index_key === undefined ? '' : '[' + JSON.stringify(i.index_key) + ']'))).sort();
  const noOps = plan.resource_changes.filter(r => r.mode === 'managed' &&
    JSON.stringify(r.change.actions) === '["no-op"]').map(r => r.address).sort();
  assert.deepEqual(noOps, addresses, 'Existing no-op identities differ from snapshot');
  return { serial: state.serial, sha256: createHash('sha256').update(canonicalBytes).digest('hex'), addresses };
}
