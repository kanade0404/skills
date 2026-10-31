import assert from 'node:assert/strict';
import { verifySnapshot } from '../poc133-provider-workaround-20261017/snapshot-guard.mjs';
export function checkRenewalPlan(plan, reference, snapshot, csrs) {
  assert.equal(plan.complete, true); assert.equal(plan.errored, false); assert.equal(plan.applyable, true);
  assert.equal((plan.resource_drift ?? []).length, 0); assert.equal((plan.deferred_changes ?? []).length, 0);
  for (const c of plan.checks ?? []) assert.equal(c.status, 'pass');
  assert.equal(verifySnapshot(snapshot, snapshot, plan).addresses.length, 13);
  const managed = plan.resource_changes.filter(r => r.mode === 'managed');
  const prefix = 'module.poc133_do_trial.';
  const expected = reference.resource_changes.filter(r => r.mode === 'managed').map(r => r.address).concat(['registered', 'unregistered'].map(role => prefix + `cloudflare_client_certificate.renewal["${role}"]`));
  assert.deepEqual(managed.map(r => r.address).sort(), expected.sort());
  for (const r of managed) {
    if (r.address.startsWith(prefix + 'cloudflare_client_certificate.renewal[')) {
      const role = r.index;
      assert.ok(['registered', 'unregistered'].includes(role));
      assert.deepEqual(r.change.actions, ['create']); assert.equal(r.change.before, null);
      assert.deepEqual(r.change.after, { csr: csrs[role], reactivate: null, validity_days: 1, zone_id: '00000000000000000000000000000002' });
      assert.deepEqual(r.change.after_unknown, Object.fromEntries(['certificate','certificate_authority','common_name','country','expires_on','fingerprint_sha256','id','issued_on','location','organization','organizational_unit','serial_number','signature','ski','state','status'].map(k => [k, true])));
    } else {
      const change = structuredClone(reference.resource_changes.find(v => v.address === r.address).change);
      if (r.address === prefix + 'cloudflare_worker_version.edge[0]') {
        const i = change.after.bindings.findIndex(b => b.name === 'FINGERPRINT');
        delete change.after.bindings[i].text;
        change.after_unknown.bindings[i].text = true;
      }
      assert.deepEqual(r.change, change, 'Unexpected change: ' + r.address);
    }
  }
  assert.deepEqual(plan.output_changes, reference.output_changes);
  const config = plan.configuration.root_module.module_calls.poc133_do_trial;
  for (const key of ['publish','enable_edge','enable_control']) assert.deepEqual(config.expressions[key], { constant_value: false });
  assert.deepEqual(config.expressions.renewal_csrs, { references: ['var.do_trial_renewal_csrs'] });
  assert.equal(managed.filter(r => r.change.actions[0] === 'create').length, 5);
  return { creates: 5, unchanged: 13, updates: 0, destroys: 0 };
}
