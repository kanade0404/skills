import assert from 'node:assert/strict';
import fs from 'node:fs';
import { spawnSync } from 'node:child_process';
import { createPublicKey } from 'node:crypto';
import { fileURLToPath } from 'node:url';
const here = fileURLToPath(new URL('.', import.meta.url));
const base = fileURLToPath(new URL('..', import.meta.url));
process.umask(0o077);
const csrs = {};
for (const role of ['registered', 'unregistered']) {
  const key = here + 'private/' + role + '.key', csr = here + role + '.csr';
  assert.equal(fs.existsSync(key), false, 'Do not overwrite keys');
  const result = spawnSync('/usr/bin/openssl', ['req', '-new', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', csr, '-subj', '/CN=poc133-renew-20261024-' + role], { encoding: 'utf8' });
  assert.equal(result.status, 0, 'CSR generation failed');
  const verify = spawnSync('/usr/bin/openssl', ['req', '-in', csr, '-verify', '-noout'], { encoding: 'utf8' });
  assert.equal(verify.status, 0); fs.writeFileSync(here + role + '-csr-check.log', verify.stdout + verify.stderr);
  const pub = spawnSync('/usr/bin/openssl', ['req', '-in', csr, '-pubkey', '-noout'], { encoding: 'utf8' });
  assert.equal(pub.status, 0);
  const options = { type: 'spki', format: 'der' };
  assert.ok(createPublicKey(pub.stdout).export(options).equals(createPublicKey(fs.readFileSync(key)).export(options)));
  csrs[role] = fs.readFileSync(csr, 'utf8');
}
fs.writeFileSync(here + 'renewal-inputs.json', JSON.stringify({ do_trial_renewal_csrs: csrs }, null, 2) + '\n');
fs.mkdirSync(here + 'home', { recursive: true, mode: 0o700 });
fs.writeFileSync(here + 'provider.tfrc', `provider_installation {\n dev_overrides { "cloudflare/cloudflare" = ${JSON.stringify(base + '/poc133-provider-workaround-20261017/patched-bin')} }\n direct {}\n}\n`);
const env = { PATH: process.env.PATH, HOME: here + 'home', TF_CLI_CONFIG_FILE: here + 'provider.tfrc', TF_IN_AUTOMATION: '1', CHECKPOINT_DISABLE: '1' };
for (const args of [['init', '-backend=false', '-input=false', '-no-color', '-lockfile=readonly', '-plugin-dir=' + base + '/poc133-terraform-import-20261008/.terraform/providers'], ['test', '-no-color']]) {
  const r = spawnSync(base + '/poc133-terraform-import-20261008/bin/terraform', args, { cwd: here + 'terraform', env, encoding: 'utf8', timeout: 120000 });
  fs.writeFileSync(here + args[0] + '.log', r.stdout + r.stderr);
  assert.equal(r.status, 0, args[0] + ' failed; see log');
}
console.log('CSR/key matching and module tests passed; no Cloudflare requests');
