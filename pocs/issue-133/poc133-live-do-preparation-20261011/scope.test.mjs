import test from 'node:test';
import assert from 'node:assert/strict';
import { readConfig, scopeFor } from './scope.mjs';

const now = Date.parse('2026-10-11T00:00:00Z');
const env = {
  RUN_ID:'poc133-local-check', TEST_STARTED_AT:'2026-10-11T00:00:00Z',
  TEST_UNTIL:'2026-10-11T01:00:00Z', LEASE_TTL_MS:'60000'
};
test('configuration accepts the first valid instant', () => {
  assert.deepEqual(readConfig(env,now), {run:'poc133-local-check',start:now,end:now+3600000,ttl:60000});
});
test('configuration enforces both time boundaries', () => {
  assert.equal(readConfig(env,now-1),null);
  assert.ok(readConfig(env,now));
  assert.ok(readConfig(env,now+3599999));
  assert.equal(readConfig(env,now+3600000),null);
});
for (const [name,patch] of [
  ['unknown run prefix',{RUN_ID:'production'}], ['run path traversal',{RUN_ID:'poc133-../task'}],
  ['missing time',{TEST_UNTIL:''}], ['invalid time',{TEST_UNTIL:'tomorrow'}],
  ['excess duration',{TEST_UNTIL:'2026-10-11T01:00:00.001Z'}],
  ['zero duration',{TEST_UNTIL:env.TEST_STARTED_AT}], ['zero ttl',{LEASE_TTL_MS:'0'}],
  ['negative ttl',{LEASE_TTL_MS:'-1'}], ['fractional ttl',{LEASE_TTL_MS:'1.5'}],
  ['excess ttl',{LEASE_TTL_MS:'60001'}], ['infinite ttl',{LEASE_TTL_MS:'Infinity'}],
]) test('invalid configuration closes: '+name, () => assert.equal(readConfig({...env,...patch},now),null));
test('TTL accepts its endpoints', () => {
  assert.equal(readConfig({...env,LEASE_TTL_MS:'1'},now).ttl,1);
  assert.equal(readConfig(env,now).ttl,60000);
});
test('scope is derived from trusted run and known scenario', () => {
  assert.deepEqual(scopeFor(env,'normal',now),{
    lane:'poc133-local-check:normal',digest:'grant:poc133-local-check:normal',
    owner:{worker:'poc-worker',incarnation:'poc133-local-check-a',epoch:1,revision:1},
    op:'poc-noop',payload:'poc133:no-external-effect',ttl:60000
  });
});
for (const scenario of ['stale','expiry','revoked','empty']) test('recognized scenario '+scenario,()=>assert.ok(scopeFor(env,scenario,now)));
for (const scenario of ['../normal','production','constructor','__proto__','',null]) test('unknown scenario denied '+scenario,()=>assert.equal(scopeFor(env,scenario,now),null));
test('missing environment and invalid clock close',()=>{
  assert.equal(readConfig(null,now),null);
  assert.equal(readConfig(env,NaN),null);
  assert.equal(scopeFor(env,'normal',now+3600000),null);
});
