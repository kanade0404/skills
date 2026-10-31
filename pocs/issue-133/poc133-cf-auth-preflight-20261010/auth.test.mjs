import test from 'node:test';
import assert from 'node:assert/strict';
import { authenticate } from './auth.mjs';

const fingerprint = 'ab'.repeat(32);
const now = Date.parse('Oct 10 10:00:00 2026 GMT');
const cert = () => ({ certPresented:'1', certVerified:'SUCCESS', certRevoked:'0',
  certFingerprintSHA256:fingerprint, certNotBefore:'Oct 10 09:00:00 2026 GMT', certNotAfter:'Oct 10 11:00:00 2026 GMT' });
const principal = () => ({ fingerprint, enabled:true, role:'worker', lane:'task-a', digest:'test-grant', op:'op-a', payload:'body-a' });
test('registered valid certificate resolves only its server-side scope', () => {
  assert.deepEqual(authenticate(cert(), [principal()], now), principal());
});
for (const presented of ['0','1']) for (const verified of ['NONE','SUCCESS']) for (const revoked of ['0','1']) {
  test('certificate decision table ' + [presented,verified,revoked].join('/'), () => {
    const actual = authenticate({...cert(),certPresented:presented,certVerified:verified,certRevoked:revoked},[principal()],now);
    assert.equal(actual !== null, presented === '1' && verified === 'SUCCESS' && revoked === '0');
  });
}
for (const [name, change] of Object.entries({
  missing: () => undefined,
  empty: () => ({}),
  booleanPresented: c => ({...c,certPresented:true}),
  booleanRevoked: c => ({...c,certRevoked:false}),
  booleanVerified: c => ({...c,certVerified:true}),
  untrustedCA: c => ({...c,certVerified:'FAILED:unable to get local issuer certificate'}),
  unknownFingerprint: c => ({...c,certFingerprintSHA256:'cd'.repeat(32)}),
  malformedFingerprint: c => ({...c,certFingerprintSHA256:'not-a-hash'}),
  missingFingerprint: c => ({...c,certFingerprintSHA256:undefined}),
  subjectOnly: c => ({...c,certFingerprintSHA256:'',certSubjectDN:'CN=trusted'}),
  invalidDate: c => ({...c,certNotAfter:'invalid'}),
  missingDate: c => ({...c,certNotBefore:undefined}),
  reversedDates: c => ({...c,certNotBefore:c.certNotAfter,certNotAfter:c.certNotBefore}),
})) test('deny certificate ' + name, () => assert.equal(authenticate(change(cert()),[principal()],now),null));
for (const [label,time,allowed] of [
  ['before lower',Date.parse('Oct 10 09:00:00 2026 GMT')-1,false],
  ['at lower',Date.parse('Oct 10 09:00:00 2026 GMT'),true],
  ['before upper',Date.parse('Oct 10 11:00:00 2026 GMT')-1,true],
  ['at upper',Date.parse('Oct 10 11:00:00 2026 GMT'),false],
  ['invalid clock',NaN,false],
]) test('certificate lifetime ' + label, () => assert.equal(authenticate(cert(),[principal()],time)!==null,allowed));
for (const [label,registry] of [
  ['empty',[]],['absent',undefined],['disabled',[{...principal(),enabled:false}]],
  ['controller',[{...principal(),role:'controller'}]],
  ['missing lane',[{...principal(),lane:''}]],['duplicate',[principal(),principal()]],
]) test('deny registry ' + label, () => assert.equal(authenticate(cert(),registry,now),null));
