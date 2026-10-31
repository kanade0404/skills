terraform {
  required_version = "= 1.16.5"
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "= 5.27.0"
    }
  }
}

locals {
  account_id = "00000000000000000000000000000001"
  zone_id    = "00000000000000000000000000000002"
  hostname   = "poc133-control.example.invalid"
  names = {
    authority = "poc133-example-authority"
    edge      = "poc133-example-edge"
  }
  common = {
    RUN_ID          = var.run_id
    TEST_STARTED_AT = var.trial_started_at
    TEST_UNTIL      = var.test_until
    LEASE_TTL_MS    = tostring(var.lease_ttl_ms)
  }
}

resource "cloudflare_client_certificate" "trial" {
  for_each      = var.provision ? var.csrs : {}
  zone_id       = local.zone_id
  csr           = each.value
  validity_days = 1
  lifecycle {
    prevent_destroy = true
  }
}

resource "cloudflare_worker" "trial" {
  for_each   = var.provision ? local.names : {}
  account_id = local.account_id
  name       = each.value
  subdomain = {
    enabled          = false
    previews_enabled = false
  }
  lifecycle {
    prevent_destroy = true
  }
}

resource "cloudflare_worker_version" "authority" {
  count              = var.provision ? 1 : 0
  deploy             = true
  account_id         = local.account_id
  worker_id          = cloudflare_worker.trial["authority"].id
  compatibility_date = "2026-10-01"
  main_module        = "authority.mjs"
  modules = [{
    name           = "authority.mjs"
    content_type   = "application/javascript+module"
    content_base64 = filebase64("${path.module}/../dist/authority.mjs")
  }]
  exports = {
    IsolatedCoordinator = { type = "durable-object", storage = "sqlite", state = "created" }
    Control             = { type = "worker", state = "created", cache = { enabled = false } }
    Reservations        = { type = "worker", state = "created", cache = { enabled = false } }
  }
  bindings = concat(
    [{ name = "COORD", type = "durable_object_namespace", class_name = "IsolatedCoordinator" }],
    [for name, value in merge(local.common, { CONTROL_ENABLED = tostring(var.enable_control) }) : { name = name, type = "plain_text", text = value }],
  )
  lifecycle {
    create_before_destroy = true
  }
}

resource "cloudflare_worker_version" "edge" {
  count              = var.provision ? 1 : 0
  account_id         = local.account_id
  worker_id          = cloudflare_worker.trial["edge"].id
  compatibility_date = "2026-10-01"
  main_module        = "edge.mjs"
  modules = [{
    name           = "edge.mjs"
    content_type   = "application/javascript+module"
    content_base64 = filebase64("${path.module}/../dist/edge.mjs")
  }]
  bindings = concat(
    [{ name = "RESERVATIONS", type = "service", service = cloudflare_worker.trial["authority"].name, entrypoint = "Reservations" }],
    [for name, value in merge(local.common, {
      ENABLED     = tostring(var.enable_edge)
      PHASE       = var.phase
      FINGERPRINT = lower(length(var.renewal_csrs) > 0 ? cloudflare_client_certificate.renewal["registered"].fingerprint_sha256 : cloudflare_client_certificate.trial["registered"].fingerprint_sha256)
    }) : { name = name, type = "plain_text", text = value }],
  )
  depends_on = [cloudflare_worker_version.authority]
  lifecycle {
    create_before_destroy = true
  }
}

resource "cloudflare_workers_deployment" "edge" {
  count       = var.provision ? 1 : 0
  account_id  = local.account_id
  script_name = cloudflare_worker.trial["edge"].name
  strategy    = "percentage"
  versions    = [{ version_id = cloudflare_worker_version.edge[0].id, percentage = 100 }]
  lifecycle {
    create_before_destroy = true
  }
}

# Only the authenticated edge has an optional public route. The authority never does.
resource "cloudflare_workers_custom_domain" "edge" {
  count      = var.publish ? 1 : 0
  account_id = local.account_id
  zone_id    = local.zone_id
  hostname   = local.hostname
  service    = cloudflare_worker.trial["edge"].name
  depends_on = [cloudflare_workers_deployment.edge]
}

output "candidate" {
  value = {
    provision       = var.provision
    publish         = var.publish
    enable_edge     = var.enable_edge
    enable_control  = var.enable_control
    run_id          = var.run_id
    phase           = var.phase
    namespace_owner = local.names.authority
    management      = "Control named WorkerEntrypoint through private service binding only"
  }
}
