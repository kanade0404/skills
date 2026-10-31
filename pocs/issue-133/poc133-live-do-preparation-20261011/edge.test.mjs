import test from 'node:test';
import assert from 'node:assert/strict';
import edge from './edge.mjs';
const fingerprint='ab'.repeat(32);
const settings=()=>({RUN_ID:'poc133-edge-unit',TEST_STARTED_AT:new Date(Date.now()-1000).toISOString(),
  TEST_UNTIL:new Date(Date.now()+30000).toISOString(),LEASE_TTL_MS:'60000',ENABLED:'true',
  PHASE:'normal',FINGERPRINT:fingerprint,RESERVATIONS:{reserveGrant(){assert.fail('Authority must not be contacted');}}});
const request=()=>new Request('https://poc133-control.example.invalid/operation',{method:'POST',
  headers:{'content-type':'application/json'},body:JSON.stringify({op:'poc-noop',payload:'poc133:no-external-effect'})});
for(const patch of [{FINGERPRINT:''},{FINGERPRINT:'wrong'},{PHASE:'production'},{RUN_ID:'production'},{ENABLED:'false'}]) {
  test('invalid edge configuration closes '+JSON.stringify(patch),async()=>{
    assert.equal((await edge.fetch(request(),{...settings(),...patch})).status,503);
  });
}
test('deadline is rechecked after the body has been consumed',async()=>{
  const env=settings();
  const r=request();
  r.cf={tlsClientAuth:{certPresented:'1',certVerified:'SUCCESS',certRevoked:'0',certFingerprintSHA256:fingerprint,
    certNotBefore:'Jan 01 00:00:00 2020 GMT',certNotAfter:'Jan 01 00:00:00 2030 GMT'}};
  // Close the time window exactly when the gateway consumes the final body chunk.
  const body=r.body;
  const reader=body.getReader();
  Object.defineProperty(r,'body',{value:new ReadableStream({async pull(controller){
    const chunk=await reader.read();
    if(chunk.done){env.TEST_UNTIL=new Date(Date.now()-1).toISOString();controller.close();}
    else controller.enqueue(chunk.value);
  }})});
  assert.equal((await edge.fetch(r,env)).status,503);
});
