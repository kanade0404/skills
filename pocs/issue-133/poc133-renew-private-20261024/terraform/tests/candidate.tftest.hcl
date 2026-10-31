mock_provider "cloudflare" {
  mock_resource "cloudflare_client_certificate" {
    defaults = { fingerprint_sha256 = "abababababababababababababababababababababababababababababababab" }
  }
}

run "default_is_inert" {
  command = plan
  assert {
    condition     = length(cloudflare_worker.trial) == 0 && length(cloudflare_client_certificate.trial) == 0 && length(cloudflare_workers_custom_domain.edge) == 0
    error_message = "Defaults must not create Workers, certificates or domains."
  }
}

run "isolated_private_provision" {
  command = plan
  variables {
    provision = true
    csrs = {
      registered   = "-----BEGIN CERTIFICATE REQUEST-----\nbW9jaw==\n-----END CERTIFICATE REQUEST-----"
      unregistered = "-----BEGIN CERTIFICATE REQUEST-----\nbW9jaw==\n-----END CERTIFICATE REQUEST-----"
    }
  }
  assert {
    condition     = cloudflare_worker_version.authority[0].deploy == true && cloudflare_worker_version.edge[0].deploy == null
    error_message = "The initial authority version must deploy atomically; the edge keeps its explicit deployment."
  }
  assert {
    condition     = length(cloudflare_worker.trial) == 2 && length(cloudflare_client_certificate.trial) == 2 && length(cloudflare_workers_custom_domain.edge) == 0
    error_message = "Create only the new isolated pair and two test certificates without a domain."
  }
  assert {
    condition     = alltrue([for worker in cloudflare_worker.trial : !worker.subdomain.enabled && !worker.subdomain.previews_enabled])
    error_message = "All workers.dev and preview endpoints must stay disabled."
  }
  assert {
    condition     = cloudflare_worker_version.authority[0].exports["IsolatedCoordinator"].storage == "sqlite" && cloudflare_worker_version.authority[0].exports["Control"].type == "worker"
    error_message = "Create a dedicated SQLite namespace and an internal named entrypoint."
  }
  assert {
    condition = (
      alltrue([for binding in cloudflare_worker_version.edge[0].bindings : binding.type != "durable_object_namespace"]) &&
      one([for binding in cloudflare_worker_version.edge[0].bindings : binding.service if binding.name == "RESERVATIONS"]) == "poc133-example-authority" &&
      one([for binding in cloudflare_worker_version.edge[0].bindings : binding.entrypoint if binding.name == "RESERVATIONS"]) == "Reservations"
    )
    error_message = "The edge may hold only the reservation service, never any DO namespace or management entrypoint."
  }
  assert {
    condition = (
      one([for binding in cloudflare_worker_version.edge[0].bindings : binding.text if binding.name == "ENABLED"]) == "false" &&
      one([for binding in cloudflare_worker_version.authority[0].bindings : binding.text if binding.name == "CONTROL_ENABLED"]) == "false"
    )
    error_message = "Provisioning must not arm either endpoint."
  }
}

run "reviewed_public_trial" {
  command = plan
  variables {
    provision                 = true
    publish                   = true
    mtls_association_reviewed = true
    enable_edge               = true
    enable_control            = true
    run_id                    = "poc133-mock-only"
    phase                     = "stale"
    trial_started_at          = "2026-10-11T00:00:00Z"
    test_until                = "2026-10-11T01:00:00Z"
    csrs = {
      registered   = "-----BEGIN CERTIFICATE REQUEST-----\nbW9jaw==\n-----END CERTIFICATE REQUEST-----"
      unregistered = "-----BEGIN CERTIFICATE REQUEST-----\nbW9jaw==\n-----END CERTIFICATE REQUEST-----"
    }
  }
  assert {
    condition     = length(cloudflare_workers_custom_domain.edge) == 1 && cloudflare_workers_custom_domain.edge[0].service == "poc133-example-edge" && cloudflare_workers_custom_domain.edge[0].hostname == "poc133-control.example.invalid"
    error_message = "Only the authenticated edge may use the reserved hostname."
  }
  assert {
    condition     = one([for binding in cloudflare_worker_version.edge[0].bindings : binding.text if binding.name == "PHASE"]) == "stale"
    error_message = "The operator chooses the phase, never the public caller."
  }
}

run "reject_publish_without_review" {
  command = plan
  variables { publish = true }
  expect_failures = [var.publish]
}
run "reject_armed_without_window" {
  command = plan
  variables {
    provision      = true
    enable_control = true
    csrs = {
      registered   = "-----BEGIN CERTIFICATE REQUEST-----\nbW9jaw==\n-----END CERTIFICATE REQUEST-----"
      unregistered = "-----BEGIN CERTIFICATE REQUEST-----\nbW9jaw==\n-----END CERTIFICATE REQUEST-----"
    }
  }
  expect_failures = [var.test_until]
}
run "reject_long_window" {
  command = plan
  variables {
    provision        = true
    enable_control   = true
    trial_started_at = "2026-10-11T00:00:00Z"
    test_until       = "2026-10-11T02:00:00Z"
    csrs = {
      registered   = "-----BEGIN CERTIFICATE REQUEST-----\nbW9jaw==\n-----END CERTIFICATE REQUEST-----"
      unregistered = "-----BEGIN CERTIFICATE REQUEST-----\nbW9jaw==\n-----END CERTIFICATE REQUEST-----"
    }
  }
  expect_failures = [var.test_until]
}
run "reject_control_without_provision" {
  command = plan
  variables {
    enable_control   = true
    trial_started_at = "2026-10-11T00:00:00Z"
    test_until       = "2026-10-11T01:00:00Z"
  }
  expect_failures = [var.enable_control]
}
run "reject_unknown_phase" {
  command = plan
  variables { phase = "production" }
  expect_failures = [var.phase]
}
run "reject_long_lease" {
  command = plan
  variables { lease_ttl_ms = 60001 }
  expect_failures = [var.lease_ttl_ms]
}
run "reject_private_key" {
  command = plan
  variables {
    provision = true
    csrs = {
      registered   = "-----BEGIN PRIVATE KEY-----\nbW9jaw==\n-----END PRIVATE KEY-----"
      unregistered = "-----BEGIN CERTIFICATE REQUEST-----\nbW9jaw==\n-----END CERTIFICATE REQUEST-----"
    }
  }
  expect_failures = [var.csrs]
}
