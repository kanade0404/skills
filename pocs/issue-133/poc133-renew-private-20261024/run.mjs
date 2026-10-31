import assert from 'node:assert/strict';
import fs from 'node:fs';
import { spawnSync } from 'node:child_process';
import { X509Certificate, createPrivateKey } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { checkRenewalPlan } from './guard.mjs';
import { checkPostDeploymentDrift } from '../poc133-patched-private-apply-20261017/guard.mjs';
import { hash, read, canonical as tf, fix } from '../poc133-atomic-deploy-20261017/runtime.mjs';
import { checkRoutes } from '../poc133-live-do-apply-20261011/safety.mjs';

process.umask(0o077);
assert.equal(process.argv.length, 3);
assert.ok(['--plan', '--apply-approved', '--resume-approved'].includes(process.argv[2]));
const resume = process.argv[2] === '--resume-approved';
const apply = process.argv[2] !== '--plan';
const here = fileURLToPath(new URL('.', import.meta.url));
const out = here + (apply ? 'apply-' : 'plan-') + Date.now(); fs.mkdirSync(out, { mode: 0o700 });
const secret = tf + '/.cloudflare-token';
const account = '00000000000000000000000000000001', zone = '00000000000000000000000000000002';
const names = { authority: 'poc133-example-authority', edge: 'poc133-example-edge' };
const hostname = 'poc133-control.example.invalid';
const snapshotPath = here + (resume ? 'apply-1792844918945/partial-or-final.tfstate' : 'before-terraform.tfstate');
if (resume) assert.equal(hash(snapshotPath), 'PRIVATE_CHECKPOINT_SHA256_REQUIRED', 'Failure checkpoint changed');
const snapshot = fs.readFileSync(snapshotPath);
const entries = s => s.resources.filter(r => r.mode === 'managed').flatMap(r => r.instances.map(i => ({ address: (r.module ? r.module + '.' : '') + r.type + '.' + r.name + (i.index_key === undefined ? '' : '[' + JSON.stringify(i.index_key) + ']'), values: i.attributes })));
const existing = entries(JSON.parse(snapshot));
assert.deepEqual(existing, entries(read(here + 'before-terraform.tfstate')), 'Managed resources changed at recovery checkpoint');
const old = role => existing.find(r => r.address === `module.poc133_do_trial.cloudflare_worker.trial["${role}"]`).values;
let token = '', tokenStat, started = false, completed = false, status = 'PRECHECK';
const redact = s => (token ? String(s ?? '').replaceAll(token, '[REDACTED]') : String(s ?? '')).replace(/-----BEGIN [^-]+-----[\s\S]*?-----END [^-]+-----/g, '[PEM OMITTED]');
const write = (n, value) => fs.writeFileSync(out + '/' + n, JSON.stringify(value, null, 2) + '\n');
const audit = [];
let env;
function sealed() {
  for (const [p, sha] of Object.entries(read(here + 'source-seal.json'))) assert.equal(hash(p), sha, 'Source changed: ' + p);
}
function terraform(args, name, allowed = [0]) {
  assert.ok(['init', 'validate', 'plan', 'show', 'apply'].includes(args[0]));
  if (args[0] === 'apply') { assert.ok(apply && started); assert.deepEqual(args, ['apply', '-input=false', '-no-color', '-lock-timeout=10s', out + '/fresh.tfplan']); }
  sealed();
  const r = spawnSync(tf + '/bin/terraform', args, { cwd: tf, env, encoding: 'utf8', timeout: 240000, maxBuffer: 32e6 });
  fs.writeFileSync(out + '/' + name + '.log', redact(r.stdout) + redact(r.stderr));
  assert.ok(allowed.includes(r.status), name + ' failed; inspect log');
  return r.stdout;
}
async function get(path) {
  assert.ok(path.startsWith(`/accounts/${account}/`) || path.startsWith(`/zones/${zone}/`));
  const r = await fetch('https://api.cloudflare.com/client/v4' + path, { method: 'GET', redirect: 'error', headers: { authorization: 'Bearer ' + token }, signal: AbortSignal.timeout(30000) });
  const b = await r.json(); audit.push({ path, status: r.status, success: b.success, errors: b.errors?.map(e => ({ code: e.code, message: redact(e.message) })) }); write('read-audit.json', audit);
  assert.ok(r.ok && b.success === true, 'GET failed ' + r.status + ' ' + path); return b;
}
async function list(path) {
  const all = [];
  for (let page = 1; page <= 100; page++) {
    const b = await get(path + (path.includes('?') ? '&' : '?') + `page=${page}&per_page=100`);
    assert.ok(Array.isArray(b.result)); all.push(...b.result);
    if (b.result_info?.total_pages !== undefined ? page >= b.result_info.total_pages : b.result.length < 100) return all;
  }
  throw Error('Pagination incomplete');
}
async function inventory() {
  checkRoutes((await get(`/zones/${zone}/workers/routes`)).result, Object.values(names));
  const domains = (await list(`/accounts/${account}/workers/domains`)).filter(d => d.hostname === hostname || Object.values(names).includes(d.service));
  const dns = (await list(`/zones/${zone}/dns_records?name=${hostname}`)).filter(d => d.name === hostname);
  assert.equal(domains.length + dns.length, 0, 'Unexpected publication');
  const workers = {};
  for (const [role, name] of Object.entries(names)) {
    const w = (await get(`/accounts/${account}/workers/workers/${old(role).id}`)).result;
    assert.equal(w.id, old(role).id); assert.equal(w.name, name);
    assert.equal(w.subdomain.enabled, false); assert.equal(w.subdomain.previews_enabled, false);
    const versions = await list(`/accounts/${account}/workers/workers/${w.id}/versions`);
    const deployments = (await get(`/accounts/${account}/workers/scripts/${name}/deployments`)).result.deployments;
    assert.ok(Array.isArray(deployments)); workers[role] = { id: w.id, name, subdomain: w.subdomain, deployedOn: w.deployed_on, versions, deployments };
  }
  const namespaces = (await list(`/accounts/${account}/workers/durable_objects/namespaces`)).filter(n => Object.values(names).includes(n.script));
  return { workers, namespaces, publicRoutes: 0, customDomains: 0, targetDns: 0 };
}
try {
  sealed(); assert.ok(fs.readFileSync(tf + '/terraform.tfstate').equals(snapshot), 'Canonical state changed; reconcile before retry');
  assert.equal(existing.length, 13);
  tokenStat = fs.lstatSync(secret);
  assert.ok(tokenStat.isFile() && !tokenStat.isSymbolicLink() && tokenStat.uid === process.getuid() && (tokenStat.mode & 0o077) === 0);
  token = fs.readFileSync(secret, 'utf8').trim(); assert.ok(token);
  env = { PATH: process.env.PATH, HOME: here + 'home', TF_CLI_CONFIG_FILE: here + 'provider.tfrc', TF_INPUT: '0', TF_IN_AUTOMATION: '1', CHECKPOINT_DISABLE: '1', CLOUDFLARE_API_TOKEN: token };
  terraform(['init', '-backend=false', '-input=false', '-no-color', '-lockfile=readonly', '-plugin-dir=' + tf + '/.terraform/providers'], '01-init');
  terraform(['validate', '-no-color'], '02-validate');
  const before = await inventory(); write('preflight.json', before);
  for (const w of Object.values(before.workers)) assert.equal(w.versions.length + w.deployments.length, 0, 'Unexpected prior deployment; do not retry');
  assert.equal(before.namespaces.length, 0);
  if (resume) {
    const csrs = Object.values(read(here + 'renewal-inputs.json').do_trial_renewal_csrs).map(s => s.replace(/\s/g, ''));
    const matches = (await list(`/zones/${zone}/client_certificates`)).filter(c => csrs.includes(String(c.csr ?? '').replace(/\s/g, '')));
    write('resume-certificate-inventory.json', matches.map(c => ({ id: c.id, status: c.status })));
    assert.equal(matches.length, 0, 'Certificate from previous attempt exists outside state; reconcile before issuance');
  }
  terraform(['plan', '-input=false', '-no-color', '-lock-timeout=10s', '-detailed-exitcode', '-out=' + out + '/fresh.tfplan'], '03-plan', [2]);
  const plan = JSON.parse(terraform(['show', '-json', out + '/fresh.tfplan'], '04-show'));
  assert.ok(!JSON.stringify(plan).includes(token)); write('plan.json', plan);
  status = 'PLAN_CAPTURED';
  if (apply) {
    const review = checkRenewalPlan(plan, read(here + '../poc133-atomic-deploy-20261017/offline-1792231030539/plan.json'), snapshot, read(here + 'renewal-inputs.json').do_trial_renewal_csrs);
    write('review.json', review);
    assert.ok(fs.readFileSync(tf + '/terraform.tfstate').equals(snapshot));
    sealed();
    const startedAt = Date.now(); started = true; status = 'APPLY_STARTED';
    write('apply-started.json', { at: new Date(startedAt).toISOString(), planSha256: hash(out + '/fresh.tfplan'), scope: 'five adds only, thirteen unchanged; no publication or acceptance' });
    terraform(['apply', '-input=false', '-no-color', '-lock-timeout=10s', out + '/fresh.tfplan'], '05-apply');
    completed = true; status = 'APPLIED_UNVERIFIED';
    const current = entries(read(tf + '/terraform.tfstate')); assert.equal(current.length, 18);
    const value = suffix => current.find(r => r.address === 'module.poc133_do_trial.' + suffix).values;
    for (const e of existing) assert.deepEqual(current.find(r => r.address === e.address).values, e.values, 'Existing resource changed during apply: ' + e.address);
    const certs = {};
    for (const role of ['registered', 'unregistered']) {
      const c = value(`cloudflare_client_certificate.renewal["${role}"]`);
      const liveCert = (await get(`/zones/${zone}/client_certificates/${c.id}`)).result;
      assert.equal(liveCert.certificate, c.certificate); assert.equal(liveCert.status, 'active');
      const cert = new X509Certificate(c.certificate);
      assert.ok(cert.checkPrivateKey(createPrivateKey(fs.readFileSync(here + 'private/' + role + '.key'))));
      assert.ok(Date.parse(cert.validTo) > Date.now());
      assert.equal(cert.fingerprint256.replaceAll(':', '').toLowerCase(), c.fingerprint_sha256.toLowerCase());
      certs[role] = { id: c.id, fingerprint: c.fingerprint_sha256.toLowerCase(), validTo: cert.validTo, keyMatches: true };
    }
    const after = await inventory(); write('post-inventory.json', after);
    assert.equal(after.namespaces.length, 1);
    assert.equal(after.namespaces[0].script, names.authority); assert.equal(after.namespaces[0].class, 'IsolatedCoordinator'); assert.equal(after.namespaces[0].use_sqlite, true);
    const receipts = {};
    for (const [role, name] of Object.entries(names)) {
      const version = value(`cloudflare_worker_version.${role}[0]`);
      const liveVersion = (await get(`/accounts/${account}/workers/workers/${old(role).id}/versions/${version.id}`)).result;
      const bindings = Object.fromEntries(liveVersion.bindings.map(b => [b.name, b]));
      assert.equal(bindings[role === 'authority' ? 'CONTROL_ENABLED' : 'ENABLED'].text, 'false');
      assert.equal(bindings.TEST_STARTED_AT.text, ''); assert.equal(bindings.TEST_UNTIL.text, '');
      if (role === 'edge') { assert.equal(bindings.FINGERPRINT.text, certs.registered.fingerprint); assert.equal(bindings.RESERVATIONS.service, names.authority); assert.equal(bindings.RESERVATIONS.entrypoint, 'Reservations'); assert.equal(liveVersion.bindings.some(b => b.type === 'durable_object_namespace'), false); }
      const ds = after.workers[role].deployments; assert.equal(ds.length, 1);
      assert.equal(ds[0].versions.length, 1); assert.equal(ds[0].versions[0].version_id, version.id); assert.equal(ds[0].versions[0].percentage, 100);
      if (role === 'edge') assert.equal(ds[0].id, value('cloudflare_workers_deployment.edge[0]').id);
      receipts[`module.poc133_do_trial.cloudflare_worker.trial["${role}"]`] = { id: old(role).id, deployedOn: after.workers[role].deployedOn, deploymentCreatedOn: ds[0].created_on };
    }
    write('private-verification.json', { ...after, certs, receipts, enableEdge: false, enableControl: false });
    terraform(['plan', '-input=false', '-no-color', '-lock-timeout=10s', '-detailed-exitcode', '-out=' + out + '/final.tfplan'], '06-final-plan');
    const final = JSON.parse(terraform(['show', '-json', out + '/final.tfplan'], '07-final-show'));
    assert.equal(final.complete, true); assert.equal(final.errored, false); assert.equal((final.deferred_changes ?? []).length, 0);
    const managed = final.resource_changes.filter(r => r.mode === 'managed'); assert.equal(managed.length, 18);
    assert.deepEqual(managed.map(r => r.address).sort(), current.map(r => r.address).sort());
    for (const r of managed) assert.deepEqual(r.change.actions, ['no-op']);
    for (const c of final.checks ?? []) assert.equal(c.status, 'pass');
    const computedDrift = checkPostDeploymentDrift(final, receipts, startedAt, Date.now());
    write('final-review.json', { noOps: 18, computedDrift, resourceDrift: final.resource_drift ?? [] });
    sealed(); status = 'PRIVATE_PROVISION_VERIFIED';
    write('summary.json', { status, created: 5, existingUnchanged: 13, finalNoOps: 18, certificatesIssued: 2, certs, sqliteNamespaces: 1, publication: false, acceptance: false });
  }
} catch (error) {
  write('error.json', { status, message: redact(error.message), started, completed }); process.exitCode = 1;
  if (started) {
    try { write('failure-inventory.json', await inventory()); } catch (e) { write('failure-inventory-error.json', { message: redact(e.message) }); }
  }
} finally {
  if (started) fs.copyFileSync(tf + '/terraform.tfstate', out + '/partial-or-final.tfstate');
  if (apply && token && fs.existsSync(secret)) {
    const st = fs.lstatSync(secret);
    if (st.dev === tokenStat.dev && st.ino === tokenStat.ino && st.mtimeMs === tokenStat.mtimeMs) fs.unlinkSync(secret);
  }
  write('completion.json', { status, started, completed, stateSha256: hash(tf + '/terraform.tfstate'), tokenFilePresent: fs.existsSync(secret) });
  console.log(JSON.stringify({ out, status, started, completed }));
}
