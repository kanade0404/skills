import assert from 'node:assert/strict';
import { guard } from '../poc133-live-do-plan-20261011/guard.mjs';
import { createPublicKey } from 'node:crypto';
export function checkResume(original,current,retainedInputs,approvedInputs) {
  const instances=s=>s.resources.filter(r=>r.mode==='managed').flatMap(r=>r.instances.map(instance=>({module:r.module,type:r.type,name:r.name,instance}))).sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b)));
  assert.deepEqual(instances(current),instances(original),'Resources changed after rejected writes');
  assert.deepEqual(retainedInputs,approvedInputs,'Retained provisioning inputs changed');
  return true;
}
export function checkRoutes(routes,names) {
  assert.ok(Array.isArray(routes),'Expected routes array');
  const matches=routes.filter(r=>names.includes(r.script));
  assert.equal(matches.length,0,'Trial Worker has a regular public route');
  return matches.length;
}
export function checkCsrKey(publicPem,privateKey) {
  const options={type:'spki',format:'der'};
  assert.ok(createPublicKey(publicPem).export(options).equals(createPublicKey(privateKey).export(options)),'CSR and private key do not match');
  return true;
}

export function checkReviewed(approved,current,existing) {
  for(const plan of [approved,current]) {
    assert.notEqual(plan.errored,true,'Errored plan');
    assert.equal((plan.resource_drift??[]).length,0,'Unexpected remote drift');
    guard(plan,existing);
  }
  const changes=p=>p.resource_changes.filter(r=>r.mode==='managed').sort((a,b)=>a.address.localeCompare(b.address));
  assert.deepEqual(changes(current),changes(approved),'Resource plan differs from approval');
  assert.deepEqual(current.output_changes,approved.output_changes,'Outputs differ from approval');
  return guard(current,existing);
}
