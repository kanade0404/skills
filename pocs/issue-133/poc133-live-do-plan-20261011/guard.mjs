export const expectedAddresses=[
  'module.poc133_do_trial.cloudflare_client_certificate.trial["registered"]',
  'module.poc133_do_trial.cloudflare_client_certificate.trial["unregistered"]',
  'module.poc133_do_trial.cloudflare_worker.trial["authority"]',
  'module.poc133_do_trial.cloudflare_worker.trial["edge"]',
  'module.poc133_do_trial.cloudflare_worker_version.authority[0]',
  'module.poc133_do_trial.cloudflare_worker_version.edge[0]',
  'module.poc133_do_trial.cloudflare_workers_deployment.authority[0]',
  'module.poc133_do_trial.cloudflare_workers_deployment.edge[0]',
];
export function guard(plan,existing,provision=true){
  const changes=(plan.resource_changes??[]).filter(r=>r.mode==='managed');
  const expected=new Map([...existing.map(a=>[a,'no-op']),...(provision?expectedAddresses.map(a=>[a,'create']):[])]);
  if(expected.size!==existing.length+(provision?8:0))throw Error('Existing state overlaps the new trial');
  const seen=new Set();
  for(const r of changes){
    if(seen.has(r.address)||!expected.has(r.address)||JSON.stringify(r.change.actions)!==JSON.stringify([expected.get(r.address)]))throw Error('Unexpected action: '+r.address);
    seen.add(r.address);
  }
  if(seen.size!==expected.size)throw Error('Missing resources in plan');
  return {creates:changes.filter(r=>r.change.actions[0]==='create').map(r=>r.address),unchanged:existing.length};
}
