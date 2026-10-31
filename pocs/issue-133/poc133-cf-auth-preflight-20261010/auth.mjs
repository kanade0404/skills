export function authenticate(cert, principals, now) {
  if (!cert || cert.certPresented !== '1' || cert.certVerified !== 'SUCCESS' || cert.certRevoked !== '0') return null;
  if (typeof cert.certFingerprintSHA256 !== 'string' || !/^[a-fA-F0-9]{64}$/.test(cert.certFingerprintSHA256)) return null;
  if (typeof cert.certNotBefore !== 'string' || typeof cert.certNotAfter !== 'string') return null;
  const start = Date.parse(cert.certNotBefore), end = Date.parse(cert.certNotAfter);
  if (![now,start,end].every(Number.isFinite) || now < start || now >= end) return null;
  if (!Array.isArray(principals)) return null;
  const matches = principals.filter(p => p?.fingerprint === cert.certFingerprintSHA256.toLowerCase());
  if (matches.length !== 1) return null;
  const p = matches[0];
  if (p.enabled !== true || p.role !== 'worker') return null;
  if (!['lane','digest','op','payload'].every(k => typeof p[k] === 'string' && p[k].length > 0)) return null;
  return p;
}
