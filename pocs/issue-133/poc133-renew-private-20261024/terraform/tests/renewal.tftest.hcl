mock_provider "cloudflare" {}

run "renew_without_destroying_originals" {
  command = plan
  variables {
    provision = true
    csrs = {
      registered   = "-----BEGIN CERTIFICATE REQUEST-----\nb2xk\n-----END CERTIFICATE REQUEST-----"
      unregistered = "-----BEGIN CERTIFICATE REQUEST-----\nb2xk\n-----END CERTIFICATE REQUEST-----"
    }
    renewal_csrs = {
      registered   = "-----BEGIN CERTIFICATE REQUEST-----\nbmV3\n-----END CERTIFICATE REQUEST-----"
      unregistered = "-----BEGIN CERTIFICATE REQUEST-----\nbmV3\n-----END CERTIFICATE REQUEST-----"
    }
  }
  override_resource {
    target = cloudflare_client_certificate.renewal["registered"]
    override_during = plan
    values = { fingerprint_sha256 = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB" }
  }
  assert {
    condition = length(cloudflare_client_certificate.trial) == 2 && length(cloudflare_client_certificate.renewal) == 2 && alltrue([for cert in cloudflare_client_certificate.renewal : cert.validity_days == 1])
    error_message = "Preserve both originals and add two one-day certificates."
  }
  assert {
    condition = one([for binding in cloudflare_worker_version.edge[0].bindings : binding.text if binding.name == "FINGERPRINT"]) == "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    error_message = "Use the newly issued registered certificate, not the expired one."
  }
}

run "reject_private_key_as_renewal_csr" {
  command = plan
  variables {
    renewal_csrs = {
      registered = "-----BEGIN PRIVATE KEY-----\nbmV3\n-----END PRIVATE KEY-----"
      unregistered = "-----BEGIN CERTIFICATE REQUEST-----\nbmV3\n-----END CERTIFICATE REQUEST-----"
    }
  }
  expect_failures = [var.renewal_csrs]
}
