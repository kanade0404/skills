import test from 'node:test';
import assert from 'node:assert/strict';
import { handle } from './gateway.mjs';
const now = Date.parse('Oct 10 10:00:00 2026 GMT');
const fingerprint = 'ab'.repeat(32);
const cert = () => ({certPresented:'1',certVerified:'SUCCESS',certRevoked:'0',certFingerprintSHA256:fingerprint,
  certNotBefore:'Oct 10 09:00:00 2026 GMT',certNotAfter:'Oct 10 11:00:00 2026 GMT'});
function fixture(options={}) {
  const entries=[], journal=[], logs=[];
  const env = { AUTH_HOST:'poc133-control.example.invalid',
    AUTH_PRINCIPALS:JSON.stringify([{fingerprint,enabled:true,role:'worker',lane:'lane-a',digest:'grant-a',op:'op-a',payload:'body-a'}]),
    COORD:{getByName(lane) { entries.push(lane); return {async reserveGrant(input) {
      if(options.error) throw Error('private backend details');
      const result=options.result ?? {status:'reserved'};
      if(result.status==='reserved') journal.push(input);
      return result;
    }}}},
  };
  const req = new Request(options.url ?? 'https://poc133-control.example.invalid/operation',{
    method:options.method ?? 'POST',headers:{'content-type':'application/json',...options.headers},
    ...((options.method??'POST')==='GET'?{}:{body:options.raw ?? JSON.stringify(options.body ?? {op:'op-a',payload:'body-a'})}),
  });
  Object.defineProperty(req,'cf',{value:options.cf ?? {tlsClientAuth:cert()}});
  return {req,env,entries,journal,logs,run:()=>handle(req,env,{now:()=>now,log:e=>logs.push(e)})};
}
test('valid operation reserves the server-bound lane and grant',async()=>{
  const f=fixture(); const r=await f.run();
  assert.equal(r.status,200,'success response');
  assert.deepEqual(f.entries,['lane-a'],'only bound lane');
  assert.deepEqual(f.journal,[{digest:'grant-a',request:{op:'op-a',payload:'body-a'}}],'server scope reaches authority');
});
for(const [name,options,status] of [
  ['no cf',{cf:{}},403],
  ['spoof headers',{cf:{},headers:{'X-CERT-VERIFY':'SUCCESS','X-CERT-FINGER':fingerprint,'x-role':'controller'}},403],
  ['http',{url:'http://poc133-control.example.invalid/operation'},404],
  ['other host',{url:'https://other.invalid/operation',headers:{'x-forwarded-host':'poc133-control.example.invalid'}},404],
  ['internal path',{url:'https://poc133-control.example.invalid/internal'},404],
  ['control path',{url:'https://poc133-control.example.invalid/control'},404],
  ['query',{url:'https://poc133-control.example.invalid/operation?method=seed'},404],
  ['get',{method:'GET'},404],
  ['invalid json',{raw:'{'},400],
  ['null json',{raw:'null'},403],
  ['array json',{raw:'[]'},403],
  ['wrong type',{headers:{'content-type':'text/plain'}},415],
  ['large body',{raw:' '.repeat(8193)},413],
  ['other operation',{body:{op:'op-b',payload:'body-a'}},403],
  ['other payload',{body:{op:'op-a',payload:'body-b'}},403],
]) test('deny '+name+' before authority',async()=>{
  const f=fixture(options); const r=await f.run();
  assert.equal(r.status,status,'expected rejection');
  assert.deepEqual(f.entries,[],'no authority access');
});
for(const key of ['lane','digest','worker','incarnation','epoch','revision','method','stopped','certFingerprintSHA256','__proto__']) {
  test('deny client authority field '+key,async()=>{
    const f=fixture({raw:JSON.stringify({op:'op-a',payload:'body-a',[key]:'forged'})});
    assert.equal((await f.run()).status,403,'reject scope injection');
    assert.deepEqual(f.journal,[],'no operation saved');
  });
}
test('body limit includes exactly 8192 bytes',async()=>{
  const raw=JSON.stringify({op:'op-a',payload:'body-a'});
  const f=fixture({raw:raw+' '.repeat(8192-Buffer.byteLength(raw))});
  assert.equal((await f.run()).status,200);
});
test('body limit measures UTF-8 bytes',async()=>{
  const f=fixture({raw:'あ'.repeat(2731)});
  assert.equal((await f.run()).status,413);
});
for(const [result,status] of [[{status:'denied'},403],[{status:'rejected',reason:'generation'},403],
  [{status:'needs-reconcile'},409],[{status:'complete',result:'receipt'},200],[{status:'unexpected'},503],[null,503]]) {
  test('authority result '+JSON.stringify(result),async()=>{
    const f=fixture(); f.env.COORD.getByName=()=>({reserveGrant:async()=>result});
    const response=await f.run();
    assert.equal(response.status,status,'fail closed unless known result');
  });
}
test('backend failure does not reveal details or grant a permit',async()=>{
  const f=fixture({error:true}); const r=await f.run();
  assert.equal(r.status,503); assert.equal((await r.text()).includes('private backend'),false);
});
test('certificate expiry while reading body is checked again',async()=>{
  const f=fixture(); let ticks=0;
  const r=await handle(f.req,f.env,{now:()=>ticks++===0?now:Date.parse('Oct 10 11:00:00 2026 GMT'),log:()=>{}});
  assert.equal(r.status,403); assert.deepEqual(f.entries,[]);
});
test('invalid registry configuration fails closed',async()=>{
  const f=fixture(); f.env.AUTH_PRINCIPALS='{';
  assert.equal((await f.run()).status,503); assert.deepEqual(f.entries,[]);
});
test('registry disabling during body read is rechecked',async()=>{
  const f=fixture(); let ticks=0;
  const response=await handle(f.req,f.env,{now:()=>{
    if(ticks++===0) f.env.AUTH_PRINCIPALS=JSON.stringify([]);
    return now;
  },log:()=>{}});
  assert.equal(response.status,403,'changed registry denies before reserve');
  assert.deepEqual(f.entries,[],'no authority access after registry removal');
});
test('structured audit excludes payload and grant identifiers',async()=>{
  const f=fixture(); await f.run();
  assert.equal(f.logs.length,1);
  assert.deepEqual(Object.keys(f.logs[0]).sort(),['event','status']);
});
