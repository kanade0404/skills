import assert from 'node:assert/strict';
import fs from 'node:fs';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

export const here = fileURLToPath(new URL('.', import.meta.url));
export const base = fileURLToPath(new URL('..', import.meta.url));
export const canonical = base + '/poc133-terraform-import-20261008';
export const fix = base + '/poc133-provider-workaround-20261017';
export const hash = p => createHash('sha256').update(fs.readFileSync(p)).digest('hex');
export const read = p => JSON.parse(fs.readFileSync(p, 'utf8'));
export function integrity() {
  for (const [n, h] of Object.entries(read(here + 'canonical-before.json'))) assert.equal(hash(canonical + '/' + n), h, 'Canonical changed: ' + n);
  for (const [n, h] of Object.entries(read(fix + '/final-checksums.json').sha256)) assert.equal(hash(fix + '/' + n), h, 'Provider evidence changed: ' + n);
  const prep = base + '/poc133-live-do-preparation-20261011';
  for (const [n, h] of Object.entries(read(prep + '/final-checksums.json'))) assert.equal(hash(prep + '/' + n), h, 'Sealed source changed: ' + n);
  for (const n of fs.readdirSync(here + 'dist')) assert.equal(hash(here + 'dist/' + n), hash(prep + '/dist/' + n));
}
export function session(prefix) {
  process.umask(0o077); integrity();
  const out = here + prefix + '-' + Date.now(); fs.mkdirSync(out, { mode: 0o700 }); fs.mkdirSync(out + '/home');
  fs.writeFileSync(out + '/local.tfrc', `provider_installation {\n  dev_overrides { "cloudflare/cloudflare" = ${JSON.stringify(fix + '/patched-bin')} }\n  direct {}\n}\n`);
  const write = (n, v) => fs.writeFileSync(out + '/' + n, JSON.stringify(v, null, 2) + '\n');
  async function run(cwd, args, label, allowed = [0]) {
    assert.ok(cwd.startsWith(here) && !cwd.startsWith(canonical));
    assert.ok(['init', 'validate', 'test', 'plan', 'show', 'apply'].includes(args[0]));
    // apply is permitted only for an isolated loopback HTTP fixture, never the copied real state.
    if (args[0] === 'apply') assert.equal(cwd, out + '/fixture');
    const env = { PATH: process.env.PATH, HOME: out + '/home', TF_CLI_CONFIG_FILE: out + '/local.tfrc', TF_INPUT: '0', TF_IN_AUTOMATION: '1', CHECKPOINT_DISABLE: '1' };
    const child = spawn(canonical + '/bin/terraform', args, { cwd, env });
    const stdout = [], stderr = []; child.stdout.on('data', b => stdout.push(b)); child.stderr.on('data', b => stderr.push(b));
    const timer = setTimeout(() => child.kill('SIGKILL'), 120000);
    const code = await new Promise((resolve, reject) => { child.on('error', reject); child.on('close', resolve); }).finally(() => clearTimeout(timer));
    const text = Buffer.concat(stdout).toString(), errors = Buffer.concat(stderr).toString();
    fs.writeFileSync(out + '/' + label + '.log', text + errors);
    assert.ok(allowed.includes(code), label + ' failed; inspect log in ' + out);
    return { code, text };
  }
  const init = cwd => run(cwd, ['init', '-backend=false', '-input=false', '-no-color', '-lockfile=readonly', '-plugin-dir=' + canonical + '/.terraform/providers'], 'init');
  return { out, write, run, init };
}
