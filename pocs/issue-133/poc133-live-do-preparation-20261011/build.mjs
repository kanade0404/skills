import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('.',import.meta.url));
await build({entryPoints:[root+'authority.mjs',root+'edge.mjs'],bundle:true,format:'esm',platform:'neutral',
  external:['cloudflare:workers'],outdir:root+'dist',outExtension:{'.js':'.mjs'},sourcemap:false});
for(const file of ['authority.mjs','edge.mjs']) {
  assert.doesNotMatch(readFileSync(root+'dist/'+file,'utf8'),/DeadlineFixture|armDeadline|rpc-unavailable|fixture\.invalid/);
}
console.log('Built only authority.mjs and edge.mjs. No deployment performed.');
