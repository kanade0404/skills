variable "renewal_csrs" {
  type    = map(string)
  default = {}
  validation {
    condition = length(var.renewal_csrs) == 0 || (
      toset(keys(var.renewal_csrs)) == toset(["registered", "unregistered"]) &&
      alltrue([for csr in values(var.renewal_csrs) : can(regex("^-----BEGIN CERTIFICATE REQUEST-----\\s+[A-Za-z0-9+/=\\r\\n]+\\s+-----END CERTIFICATE REQUEST-----\\s*$", csr))])
    )
    error_message = "Renew both test roles using public CSR PEMs only."
  }
}

# Keep the expired originals managed; do not revoke or destroy them during issuance.
resource "cloudflare_client_certificate" "renewal" {
  for_each      = var.provision ? var.renewal_csrs : {}
  zone_id       = local.zone_id
  csr           = each.value
  validity_days = 1
  lifecycle {
    prevent_destroy = true
  }
}
