import test from 'node:test';
import assert from 'node:assert/strict';
import { handle } from './gateway.mjs';
function setup(body,payload='p') {
  const request=new Request('https://poc133-control.example.invalid/operation',{method:'POST',headers:{'content-type':'application/json'},body,duplex:'half'});
  Object.defineProperty(request,'cf',{value:{tlsClientAuth:{certPresented:'1',certVerified:'SUCCESS',certRevoked:'0',
    certFingerprintSHA256:'ab'.repeat(32),certNotBefore:'Jan 01 00:00:00 2020 GMT',certNotAfter:'Jan 01 00:00:00 2030 GMT'}}});
  let calls=0;
  const env={AUTH_HOST:'poc133-control.example.invalid',AUTH_PRINCIPALS:JSON.stringify([{fingerprint:'ab'.repeat(32),enabled:true,
    role:'worker',lane:'a',digest:'d',op:'o',payload}]),COORD:{getByName(){calls++;return {reserveGrant:async()=>({status:'reserved'})};}}};
  return {run:()=>handle(request,env,{log:()=>{}}),calls:()=>calls};
}
test('stalled body times out without reaching authority',{timeout:5000},async()=>{
  let cancelled=false;
  const f=setup(new ReadableStream({cancel(){cancelled=true;}}));
  assert.equal((await f.run()).status,408,'bounded body wait');
  assert.equal(f.calls(),0,'no authority request');
  assert.equal(cancelled,true,'reader cancelled');
});
test('malformed UTF-8 is rejected before authority',async()=>{
  const f=setup(new Uint8Array([0xff]));
  assert.equal((await f.run()).status,400);
  assert.equal(f.calls(),0);
});
test('invalid UTF-8 inside otherwise valid JSON cannot match replacement-character payload',async()=>{
  const prefix=new TextEncoder().encode('{"op":"o","payload":"');
  const suffix=new TextEncoder().encode('"}');
  const f=setup(new Uint8Array([...prefix,0xff,...suffix]),'\uFFFD');
  assert.equal((await f.run()).status,400,'fatal decoder rejects invalid bytes, not just invalid JSON');
  assert.equal(f.calls(),0,'no lossy decoding can grant permission');
});
test('valid multibyte payload split across chunks retains exact content',async()=>{
  const bytes=new TextEncoder().encode('{"op":"o","payload":"あ"}');
  const split=bytes.indexOf(0xe3)+1;
  const body=new ReadableStream({start(c){c.enqueue(bytes.slice(0,split));c.enqueue(bytes.slice(split));c.close();}});
  const f=setup(body,'あ');
  assert.equal((await f.run()).status,200,'stream decoder preserves codepoint across chunks');
  assert.equal(f.calls(),1,'valid payload reaches authority once');
});
