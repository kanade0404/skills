import test from 'node:test';
import assert from 'node:assert/strict';
import { appendFileSync, mkdirSync, mkdtempSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';

const root=fileURLToPath(new URL('.',import.meta.url));
const out=process.env.EVIDENCE_DIR;
assert.ok(out,'EVIDENCE_DIR required');
mkdirSync(out,{recursive:true});
const record=e=>appendFileSync(out+'/events.jsonl',JSON.stringify(e)+'\n',{mode:0o600});
const bundle=async file=>(await build({entryPoints:[root+file],bundle:true,format:'esm',platform:'neutral',
  external:['cloudflare:workers'],write:false})).outputFiles[0].text;
const authority=await bundle('authority.mjs'),edge=await bundle('edge.mjs');
const deadlineAuthority=await bundle('deadline-fixture.mjs');
const client=readFileSync(root+'test-client.mjs','utf8');
const fingerprint='ab'.repeat(32),host='poc133-control.example.invalid';
const payload={op:'poc-noop',payload:'poc133:no-external-effect'};
const tls={certPresented:'1',certVerified:'SUCCESS',certRevoked:'0',certFingerprintSHA256:fingerprint,
  certNotBefore:'Jan 01 00:00:00 2020 GMT',certNotAfter:'Jan 01 00:00:00 2030 GMT'};
function settings(name,overrides={}) {
  const now=Date.now();
  return {RUN_ID:'poc133-local-'+name,TEST_STARTED_AT:new Date(now-1000).toISOString(),
    TEST_UNTIL:new Date(now+120000).toISOString(),LEASE_TTL_MS:'60000',CONTROL_ENABLED:'true',
    ENABLED:'true',FINGERPRINT:fingerprint,...overrides};
}
async function start(env,persist,deadlineFixture=false) {
  const className=deadlineFixture?'DeadlineFixture':'IsolatedCoordinator';
  const mf=new Miniflare({...convertV4MiniflareOptions({host:'127.0.0.1',cf:false,
    ...(persist?{resourcePersistencePath:persist}:{}),workers:[
      {name:'authority',compatibilityDate:'2026-10-01',modules:true,script:deadlineFixture?deadlineAuthority:authority,bindings:env,
        durableObjects:{COORD:{className,useSQLite:true}}},
      {name:'client',compatibilityDate:'2026-10-01',modules:true,script:client,
        serviceBindings:{CONTROL:{name:'authority',entrypoint:'Control'}}},
      {name:'audit',compatibilityDate:'2026-10-01',modules:true,
        script:`export default { async fetch(request,env) { const {lane,method,input}=await request.json();
          return Response.json(await env.COORD.getByName(lane)[method](input)); } };`,
        durableObjects:{COORD:{className,scriptName:'authority',useSQLite:true}}},
      {name:'reservation-audit',compatibilityDate:'2026-10-01',modules:true,
        script:`export default { async fetch(request,env) { const {method,input}=await request.json();
          try { return Response.json(await env.RESERVATIONS[method](input)); }
          catch { return Response.json({status:'rpc-unavailable'},{status:400}); } } };`,
        serviceBindings:{RESERVATIONS:{name:'authority',entrypoint:'Reservations'}}},
      ...['normal','stale','expiry','revoked','empty'].map(scenario=>({
        name:'edge-'+scenario,compatibilityDate:'2026-10-01',modules:true,script:edge,
        bindings:{...env,PHASE:scenario},serviceBindings:{RESERVATIONS:{name:'authority',entrypoint:'Reservations'}}
      }))
    ]}),telemetry:{enabled:false}});
  const transport=await mf.getWorker('client');
  const control=async(action,scenario='normal',extra={})=>{
    const r=await transport.fetch('http://fixture.invalid',{method:'POST',body:JSON.stringify({action,scenario,...extra})});
    assert.equal(r.status,200);return r.json();
  };
  const request=async(scenario='normal',options={})=>{
    const worker=await mf.getWorker('edge-'+scenario);
    const r=await worker.fetch('https://'+host+(options.path??'/operation'),{
      method:options.method??'POST',cf:options.cf??{tlsClientAuth:tls},
      headers:{'content-type':'application/json',...options.headers},body:JSON.stringify(options.body??payload)});
    return {status:r.status,body:await r.text()};
  };
  const audit=async(method,input,scenario='normal')=>{
    const worker=await mf.getWorker('audit');
    const r=await worker.fetch('http://fixture.invalid',{method:'POST',body:JSON.stringify({lane:env.RUN_ID+':'+scenario,method,input})});
    assert.equal(r.status,200);return r.json();
  };
  return {mf,control,request,audit};
}
const stable=s=>{const {instance,...rest}=s;return rest;};
const run=async(name,fn,overrides={})=>{
  const app=await start(settings(name,overrides));
  try {await fn(app);record({scenario:name,pass:true});} finally {await app.mf.dispose();}
};
test('initialization never resets durable authority',()=>run('init',async({control})=>{
  assert.equal((await control('initialize')).status,'ok');
  const before=await control('inspect');
  assert.deepEqual(await control('initialize'),{status:'rejected',reason:'already-initialized'});
  assert.deepEqual(await control('inspect'),before);
}));
test('concurrent repeats reserve exactly one journal entry',()=>run('normal',async({control,request})=>{
  assert.equal((await control('initialize')).status,'ok');
  const responses=await Promise.all(Array.from({length:8},()=>request()));
  assert.equal(responses.filter(r=>r.status===200).length,1);
  assert.equal(responses.filter(r=>r.status===409).length,7);
  const before=await control('inspect');
  assert.equal(before.operations.length,1);
  assert.equal(before.operations[0].status,'pending');
  assert.equal((await request()).status,409);
  assert.deepEqual(await control('inspect'),before);
}));
test('public authorization denials leave SQLite authority unchanged',()=>run('denials',async({control,request})=>{
  assert.equal((await control('initialize')).status,'ok');
  const before=await control('inspect');
  for(const options of [
    {cf:{}},{cf:{tlsClientAuth:{...tls,certRevoked:'1'}}},
    {cf:{tlsClientAuth:{...tls,certFingerprintSHA256:'cd'.repeat(32)}}},
    {cf:{},headers:{'cf-cert-verified':'SUCCESS'}},
    {body:{...payload,lane:'elsewhere'}},{body:{...payload,op:'different'}},
    {body:{...payload,payload:'changed'}}
  ]) {assert.equal((await request('normal',options)).status,403);assert.deepEqual(await control('inspect'),before);}
}));
test('management is unavailable through HTTP and disallowed RPC inputs',()=>run('management',async({mf,control,request})=>{
  assert.equal((await control('initialize')).status,'ok');
  const before=await control('inspect');
  for(const path of ['/control','/internal','/rpc','/inspect']) {
    assert.equal((await request('normal',{path})).status,404);
    assert.equal((await (await mf.getWorker('authority')).fetch('https://example.invalid'+path,{method:'POST',body:'{}'})).status,404);
  }
  for(const [action,scenario,extra] of [
    ['setClock','normal',{}],['seed','normal',{}],['initialize','../normal',{}],
    ['inspect','normal',{lane:'production'}],['takeover','normal',{}],['initialize','empty',{}]
  ]) assert.deepEqual(await control(action,scenario,extra),{status:'denied'});
  assert.deepEqual(await control('inspect'),before);
}));
test('draining and obsolete generation cannot reserve',()=>run('stale',async({control,request})=>{
  assert.equal((await control('initialize','stale')).status,'ok');
  assert.equal((await control('drain','stale')).status,'ok');
  const drained=await control('inspect','stale');
  assert.equal((await request('stale')).status,403);
  assert.deepEqual(await control('inspect','stale'),drained);
  assert.equal((await control('takeover','stale')).status,'ok');
  const changed=await control('inspect','stale');
  assert.equal(changed.epoch,2);
  assert.equal(changed.operations.length,0);
  assert.equal((await request('stale')).status,403);
  assert.deepEqual(await control('inspect','stale'),changed);
}));
test('grant revocation denies an otherwise valid certificate',()=>run('revoked',async({control,request})=>{
  assert.equal((await control('initialize','revoked')).status,'ok');
  assert.equal((await control('revoke','revoked')).status,'ok');
  const before=await control('inspect','revoked');
  assert.equal((await request('revoked')).status,403);
  assert.deepEqual(await control('inspect','revoked'),before);
}));
test('real authority clock expires a lease without a test clock',()=>run('expiry',async({control,request})=>{
  assert.equal((await control('initialize','expiry')).status,'ok');
  assert.equal((await request('expiry')).status,200);
  const before=await control('inspect','expiry');
  assert.equal(before.lifecycle.clock,null);
  await new Promise(r=>setTimeout(r,Math.max(0,before.expires-Date.now())+100));
  assert.equal((await request('expiry')).status,403);
  assert.deepEqual(await control('inspect','expiry'),before);
},{LEASE_TTL_MS:'600'}));
test('uninitialized lane denies instead of auto-initializing',()=>run('empty',async({control,request})=>{
  const before=await control('inspect','empty');
  assert.equal((await request('empty')).status,403);
  assert.deepEqual(await control('inspect','empty'),before);
  assert.equal(before.operations.length,0);
}));
test('different scenarios cannot share reservations',()=>run('isolation',async({control,request})=>{
  assert.equal((await control('initialize','normal')).status,'ok');
  assert.equal((await control('initialize','revoked')).status,'ok');
  assert.equal((await request()).status,200);
  assert.equal((await control('inspect','normal')).operations.length,1);
  assert.equal((await control('inspect','revoked')).operations.length,0);
}));
test('disabled edge and management both close',()=>run('closed',async({control,request})=>{
  assert.deepEqual(await control('initialize'),{status:'denied'});
  assert.equal((await request()).status,503);
},{ENABLED:'false',CONTROL_ENABLED:'false'}));
test('edge can close while private management remains available',()=>run('edge-closed',async({control,request})=>{
  assert.equal((await control('initialize')).status,'ok');
  const before=await control('inspect');
  assert.equal((await request()).status,503);
  assert.deepEqual(await control('inspect'),before);
},{ENABLED:'false'}));
test('management can close without changing existing edge reservations',async()=>{
  const persist=mkdtempSync(out+'/gate-persist-'),env=settings('control-closed');
  let app=await start(env,persist);
  try {assert.equal((await app.control('initialize')).status,'ok');}
  finally {await app.mf.dispose();}
  app=await start({...env,CONTROL_ENABLED:'false'},persist);
  try {
    assert.deepEqual(await app.control('inspect'),{status:'denied'});
    assert.equal((await app.request()).status,200);
    assert.equal((await app.request()).status,409);
  } finally {await app.mf.dispose();}
  record({scenario:'control-closed',pass:true});
});
test('pending journal prevents generation takeover',()=>run('pending-takeover',async({control,request})=>{
  assert.equal((await control('initialize','stale')).status,'ok');
  assert.equal((await request('stale')).status,200);
  assert.equal((await control('drain','stale')).status,'ok');
  const before=await control('inspect','stale');
  assert.equal((await control('takeover','stale')).status,'rejected');
  const after=await control('inspect','stale');
  assert.equal(after.epoch,before.epoch);
  assert.deepEqual(after.operations,before.operations);
  assert.equal((await request('stale')).status,403);
}));
test('expired trial closes before authority operations',()=>run('deadline',async({control,request})=>{
  assert.deepEqual(await control('initialize'),{status:'denied'});
  assert.equal((await request()).status,503);
},{TEST_STARTED_AT:new Date(Date.now()-10000).toISOString(),TEST_UNTIL:new Date(Date.now()-1000).toISOString()}));
test('unused inherited mutation methods cannot bypass the management gate',()=>run('rpc-denials',async({audit})=>{
  const before=await audit('inspect');
  const input={worker:'poc-worker',incarnation:'poc133-local-rpc-denials-a',epoch:1,revision:1,
    expires:Date.now()+60000,value:'must-not-write',digest:'invalid',binding:{},op:'poc-noop'};
  for(const method of ['seed','grant','renew','write','finish','reconcileComplete','crash','setClock','reserve','seal','confirmStopped','takeOver','revoke']) {
    assert.deepEqual(await audit(method,input),{status:'denied'},method);
  }
  assert.deepEqual(await audit('inspect'),before);
},{CONTROL_ENABLED:'false'}));
test('reservation capability exposes no management RPC or caller-selected lane',()=>run('capability',async({mf,control})=>{
  assert.equal((await control('initialize')).status,'ok');
  const before=await control('inspect');
  const caller=await mf.getWorker('reservation-audit');
  const call=(method,input)=>caller.fetch('http://fixture.invalid',{method:'POST',body:JSON.stringify({method,input})});
  for(const method of ['invoke','seed','prepare','takeOver','revoke','crash','inspect']) {
    assert.equal((await call(method,{})).status,400,method);
  }
  for(const input of [null,{},
    {scenario:'production',digest:'grant:poc133-local-capability:normal',request:payload},
    {scenario:'normal',digest:'wrong',request:payload},
    {scenario:'normal',digest:'grant:poc133-local-capability:normal',request:payload,lane:'elsewhere'}]) {
    const r=await call('reserveGrant',input);
    assert.deepEqual(await r.json(),{status:'denied'});
  }
  assert.deepEqual(await control('inspect'),before);
}));
test('DO rejects takeover queued past the trial deadline after stop proof',async()=>{
  const env=settings('late-takeover');
  const app=await start(env,undefined,true);
  const owner={worker:'poc-worker',incarnation:env.RUN_ID+'-a',epoch:1,revision:1};
  try {
    assert.equal((await app.control('initialize','stale')).status,'ok');
    assert.equal((await app.control('drain','stale')).status,'ok');
    assert.equal((await app.audit('confirmStopped',{...owner,run:'fixture:no-process'},'stale')).status,'ok');
    const before=await app.audit('inspect',{},'stale');
    const {until}=await app.audit('armDeadline',{},'stale');
    await new Promise(r=>setTimeout(r,Math.max(0,until-Date.now())+50));
    assert.deepEqual(await app.audit('takeOver',{...owner,nextIncarnation:env.RUN_ID+'-b',ttl:60000},'stale'),{status:'denied'});
    assert.deepEqual(await app.audit('inspect',{},'stale'),before);
  } finally {await app.mf.dispose();}
  record({scenario:'late-takeover',pass:true});
});
test('SQLite reservation survives runtime recreation and a separate run remains empty',async()=>{
  const persist=mkdtempSync(out+'/persist-'),env=settings('restart');
  let app=await start(env,persist),before;
  try {
    assert.equal((await app.control('initialize')).status,'ok');
    assert.equal((await app.request()).status,200);
    before=await app.control('inspect');
  } finally {await app.mf.dispose();}
  app=await start(env,persist);
  try {
    const after=await app.control('inspect');
    assert.notEqual(after.instance,before.instance);
    assert.deepEqual(stable(after),stable(before));
    assert.equal((await app.request()).status,409);
    assert.deepEqual(stable(await app.control('inspect')),stable(before));
    assert.equal((await app.control('initialize')).status,'rejected');
  } finally {await app.mf.dispose();}
  app=await start({...env,RUN_ID:'poc133-local-second-run'},persist);
  try {assert.equal((await app.control('inspect')).operations.length,0);}
  finally {await app.mf.dispose();}
  record({scenario:'recreation',pass:true});
});
