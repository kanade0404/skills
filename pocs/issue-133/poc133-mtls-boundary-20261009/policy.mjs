export function workerRequest(principal, body) {
  if (!principal || !body || Array.isArray(body) || typeof body !== "object") return null;
  if (Object.keys(body).sort().join(",") !== "op,payload") return null;
  if (body.op !== principal.op || body.payload !== principal.payload) return null;
  return { lane: principal.lane, digest: principal.digest, request: { op: body.op, payload: body.payload } };
}
export function controllerRequest(principal, body) {
  if (!principal || !body || typeof body !== "object" || Array.isArray(body)) return null;
  if (Object.keys(body).sort().join(",") !== "input,lane,method") return null;
  if (!principal.lanes.includes(body.lane)) return null;
  if (!["inspect", "revoke"].includes(body.method)) return null;
  if (body.method === "inspect" && body.input !== null) return null;
  if (body.method === "revoke" && (!body.input || Object.keys(body.input).join(",") !== "digest" || typeof body.input.digest !== "string")) return null;
  return body;
}
