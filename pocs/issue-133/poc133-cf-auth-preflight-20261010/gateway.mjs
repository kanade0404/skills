import { authenticate } from './auth.mjs';
import { workerRequest } from '../poc133-mtls-boundary-20261009/policy.mjs';

async function boundedJson(request) {
  if (!request.body) throw { status:400 };
  const reader = request.body.getReader(), decoder = new TextDecoder('utf-8', {fatal:true});
  let timer, bytes=0, text='';
  const deadline = new Promise((_,reject) => { timer=setTimeout(()=>reject({status:408}),2000); });
  try {
    for (;;) {
      const {done,value} = await Promise.race([reader.read(),deadline]);
      if (done) break;
      bytes += value.byteLength;
      if (bytes > 8192) throw { status:413 };
      text += decoder.decode(value,{stream:true});
    }
    text += decoder.decode();
    return JSON.parse(text);
  } catch (error) {
    void reader.cancel().catch(()=>{});
    throw { status:[408,413].includes(error?.status)?error.status:400 };
  } finally { clearTimeout(timer); reader.releaseLock(); }
}

export async function handle(request, env, {now=Date.now,log=event=>console.log(JSON.stringify(event))}={}) {
  const reply = (status,body) => {
    log({event:'poc133-auth-decision',status});
    return Response.json(body,{status,headers:{'cache-control':'no-store'}});
  };
  try {
    const url = new URL(request.url);
    if (url.protocol !== 'https:' || url.host !== env.AUTH_HOST || url.pathname !== '/operation' || url.search || request.method !== 'POST')
      return reply(404,{status:'not-found'});
    const registry = JSON.parse(env.AUTH_PRINCIPALS);
    if (!authenticate(request.cf?.tlsClientAuth,registry,now())) return reply(403,{status:'denied'});
    if (request.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'application/json') return reply(415,{status:'invalid'});
    let body;
    try { body=await boundedJson(request); } catch (error) { return reply(error.status,{status:'invalid'}); }
    // Reading a body may span certificate expiry; never reuse a cached authentication decision.
    const principal = authenticate(request.cf?.tlsClientAuth,JSON.parse(env.AUTH_PRINCIPALS),now());
    const scope = workerRequest(principal,body);
    if (!scope) return reply(403,{status:'denied'});
    const result = await env.COORD.getByName(scope.lane).reserveGrant({digest:scope.digest,request:scope.request});
    if (result?.status === 'denied' || result?.status === 'rejected') return reply(403,{status:'denied'});
    if (result?.status === 'needs-reconcile') return reply(409,{status:'needs-reconcile'});
    if (result?.status === 'reserved') return reply(200,{status:'reserved'});
    if (result?.status === 'complete' && typeof result.result === 'string') return reply(200,{status:'complete',result:result.result});
    return reply(503,{status:'unavailable'});
  } catch { return reply(503,{status:'unavailable'}); }
}
