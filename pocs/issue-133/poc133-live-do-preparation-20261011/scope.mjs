const scenarios = new Set(['normal', 'stale', 'expiry', 'revoked', 'empty']);
const iso = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/;
export function readConfig(env, now = Date.now()) {
  if (!env || typeof env.RUN_ID !== 'string' || !/^poc133-[a-z0-9-]{1,48}$/.test(env.RUN_ID)) return null;
  if (![env.TEST_STARTED_AT, env.TEST_UNTIL].every(v => typeof v === 'string' && iso.test(v))) return null;
  const start = Date.parse(env.TEST_STARTED_AT), end = Date.parse(env.TEST_UNTIL);
  if (![start,end,now].every(Number.isSafeInteger) || start > now || now >= end || end - start > 3600000) return null;
  if (typeof env.LEASE_TTL_MS !== 'string' || !/^\d+$/.test(env.LEASE_TTL_MS)) return null;
  const ttl = Number(env.LEASE_TTL_MS);
  if (!Number.isSafeInteger(ttl) || ttl < 1 || ttl > 60000) return null;
  return { run:env.RUN_ID, start, end, ttl };
}
export function scopeFor(env, scenario, now = Date.now()) {
  const config = readConfig(env, now);
  if (!config || !scenarios.has(scenario)) return null;
  return {
    lane:config.run + ':' + scenario, digest:'grant:' + config.run + ':' + scenario,
    owner:{worker:'poc-worker', incarnation:config.run + '-a', epoch:1, revision:1},
    op:'poc-noop', payload:'poc133:no-external-effect', ttl:config.ttl,
  };
}
