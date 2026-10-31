import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { checkRenewalPlan } from './guard.mjs';
const read = p => JSON.parse(fs.readFileSync(new URL(p, import.meta.url)));
const plan = read('plan-1792844742272/plan.json');
const reference = read('../poc133-atomic-deploy-20261017/offline-1792231030539/plan.json');
const snapshot = fs.readFileSync(new URL('before-terraform.tfstate', import.meta.url));
const csrs = read('renewal-inputs.json').do_trial_renewal_csrs;
const find = (p, suffix) => p.resource_changes.find(r => r.address.endsWith(suffix));
test('accepts two new certificates and three provisioning creates only', () => {
  assert.deepEqual(checkRenewalPlan(plan, reference, snapshot, csrs), { creates: 5, unchanged: 13, updates: 0, destroys: 0 });
});
for (const [name, mutate] of [
  ['existing destruction', p => { find(p, 'cloudflare_client_certificate.trial["registered"]').change.actions = ['delete']; }],
  ['unapproved resource', p => { p.resource_changes.push({ ...p.resource_changes[0], address: 'unexpected' }); }],
  ['certificate CSR mismatch', p => { find(p, 'cloudflare_client_certificate.renewal["registered"]').change.after.csr = csrs.unregistered; }],
  ['certificate scope mismatch', p => { find(p, 'cloudflare_client_certificate.renewal["registered"]').change.after.zone_id = 'wrong-zone'; }],
  ['certificate lifetime expansion', p => { find(p, 'cloudflare_client_certificate.renewal["registered"]').change.after.validity_days = 365; }],
  ['edge gate enabled', p => { find(p, 'cloudflare_worker_version.edge[0]').change.after.bindings.find(b => b.name === 'ENABLED').text = 'true'; }],
  ['unknown gate value', p => { const e = find(p, 'cloudflare_worker_version.edge[0]'); e.change.after_unknown.bindings[1].text = true; }],
  ['authority not deployed', p => { find(p, 'cloudflare_worker_version.authority[0]').change.after.deploy = false; }],
  ['changed bundle', p => { find(p, 'cloudflare_worker_version.authority[0]').change.after.modules[0].content_base64 = 'Yg=='; }],
  ['changed percentage', p => { find(p, 'cloudflare_workers_deployment.edge[0]').change.after.versions[0].percentage = 50; }],
  ['drift', p => { p.resource_drift = [{}]; }],
  ['deferred', p => { p.deferred_changes = [{}]; }],
  ['errored', p => { p.errored = true; }],
  ['incomplete', p => { p.complete = false; }],
  ['check failed', p => { p.checks[0].status = 'fail'; }],
]) test('rejects ' + name, () => {
  const changed = structuredClone(plan); mutate(changed);
  assert.throws(() => checkRenewalPlan(changed, reference, snapshot, csrs));
});
