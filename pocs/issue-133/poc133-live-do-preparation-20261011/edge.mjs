import { handle } from '../poc133-cf-auth-preflight-20261010/gateway.mjs';
import { scopeFor } from './scope.mjs';
export default {
  async fetch(request,env) {
    const scope = scopeFor(env,env.PHASE);
    if (env.ENABLED !== 'true' || !scope || typeof env.FINGERPRINT !== 'string' || !/^[a-f0-9]{64}$/.test(env.FINGERPRINT)) {
      return Response.json({status:'unavailable'},{status:503,headers:{'cache-control':'no-store'}});
    }
    const principal = {fingerprint:env.FINGERPRINT,enabled:true,role:'worker',...scope};
    return handle(request,{
      AUTH_HOST:'poc133-control.example.invalid',AUTH_PRINCIPALS:JSON.stringify([principal]),
      COORD:{getByName(lane) {
        if (lane !== scope.lane) throw Error('Wrong trial scope');
        return {reserveGrant(input) {
          // The request body can finish after the trial deadline.
          if (!scopeFor(env,env.PHASE)) throw Error('Trial closed');
          return env.RESERVATIONS.reserveGrant({scenario:env.PHASE,...input});
        }};
      }},
    });
  },
};
