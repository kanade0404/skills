// Local test transport only; excluded from deployable bundles and Terraform.
export default {
  async fetch(request,env) {
    return Response.json(await env.CONTROL.invoke(await request.json()));
  }
};
