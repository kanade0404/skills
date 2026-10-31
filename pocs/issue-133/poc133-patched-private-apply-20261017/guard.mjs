import assert from 'node:assert/strict';
import { expectedAddresses } from '../poc133-live-do-plan-20261011/guard.mjs';
import { verifySnapshot } from '../poc133-provider-workaround-20261017/snapshot-guard.mjs';

export function checkPostDeploymentDrift(plan, receipts, started, finished) {
  const drift = plan.resource_drift ?? [];
  const allowed = expectedAddresses.slice(2, 4);
  const seen = new Set();
  for (const r of drift) {
    assert.ok(allowed.includes(r.address) && !seen.has(r.address), 'Unexpected drift identity');
    seen.add(r.address);
    assert.deepEqual(r.change.actions, ['update']);
    const proof = receipts[r.address]; assert.ok(proof, 'No observed deployment');
    const { deployed_on: before, ...beforeRest } = r.change.before;
    const { deployed_on: after, ...afterRest } = r.change.after;
    assert.equal(before, null); assert.equal(after, proof.deployedOn);
    assert.equal(afterRest.id, proof.id); assert.deepEqual(afterRest, beforeRest);
    for (const value of [after, proof.deploymentCreatedOn]) {
      const time = Date.parse(value);
      assert.ok(time >= started && time <= finished, 'Timestamp outside this apply');
    }
  }
  return drift.length;
}

// Module paths and provider endpoints differ in the offline comparison fixture;
// runtime file hashes guard those separately. Preserve every value reference.
function references(module) {
  return { resources: module.resources ?? [], calls: Object.fromEntries(
    Object.entries(module.module_calls ?? {}).map(([name, call]) => [name, {
      expressions: call.expressions, module: references(call.module),
    }]),
  ) };
}

export function checkRemainingPlan(plan, reference, snapshot) {
  assert.equal(plan.errored, false, 'Errored plan');
  assert.equal(plan.complete, true, 'Incomplete plan');
  assert.equal(plan.applyable, true, 'Unapplyable plan');
  assert.equal((plan.deferred_changes ?? []).length, 0, 'Deferred changes');
  assert.equal((plan.resource_drift ?? []).length, 0, 'Remote drift');
  assert.deepEqual(references(plan.configuration.root_module), references(reference.configuration.root_module), 'Configuration references changed');
  const proof = verifySnapshot(snapshot, snapshot, plan);
  assert.equal(proof.serial, 44, 'Not the approved partial state');
  assert.equal(proof.addresses.length, 13);
  const wanted = expectedAddresses.slice(4).sort();
  const changes = plan.resource_changes.filter(r => r.mode === 'managed');
  assert.equal(changes.length, 17);
  assert.equal(new Set(changes.map(r => r.address)).size, 17, 'Duplicate identity');
  assert.deepEqual(changes.filter(r => r.change.actions[0] === 'create').map(r => r.address).sort(), wanted);
  for (const r of changes) {
    assert.deepEqual(r.change.actions, [wanted.includes(r.address) ? 'create' : 'no-op']);
    const original = reference.resource_changes.find(v => v.address === r.address);
    assert.ok(original, 'Unknown identity: ' + r.address);
    // The sealed reference is comparison data only, never the plan to apply.
    assert.deepEqual(r.change.after, original.change.after, 'Planned values changed: ' + r.address);
    assert.deepEqual(r.change.after_unknown, original.change.after_unknown, 'Unknown values changed: ' + r.address);
    if (!wanted.includes(r.address)) assert.deepEqual(r.change.before, r.change.after, 'No-op contains a change');
  }
  assert.deepEqual(plan.output_changes, reference.output_changes, 'Output changes');
  return { creates: 4, unchanged: 13, updates: 0, deletes: 0 };
}
