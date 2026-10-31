variable "provision" {
  type    = bool
  default = false
}
variable "publish" {
  type    = bool
  default = false
  validation {
    condition     = !var.publish || (var.provision && var.mtls_association_reviewed)
    error_message = "Publication requires provisioning and a reviewed managed-CA hostname association in the canonical root."
  }
}
variable "mtls_association_reviewed" {
  type    = bool
  default = false
}
variable "enable_edge" {
  type    = bool
  default = false
  validation {
    condition     = !var.enable_edge || var.publish
    error_message = "Enable the edge only for the reviewed published trial."
  }
}
variable "enable_control" {
  type    = bool
  default = false
  validation {
    condition     = !var.enable_control || var.provision
    error_message = "Private management requires the isolated authority."
  }
}
variable "run_id" {
  type    = string
  default = "poc133-unarmed"
  validation {
    condition     = can(regex("^poc133-[a-z0-9-]{1,48}$", var.run_id))
    error_message = "Use a dedicated PoC run ID."
  }
}
variable "phase" {
  type    = string
  default = "normal"
  validation {
    condition     = contains(["normal", "stale", "expiry", "revoked", "empty"], var.phase)
    error_message = "Only fixed trial scenarios are allowed."
  }
}
variable "trial_started_at" {
  type    = string
  default = ""
}
variable "test_until" {
  type    = string
  default = ""
  validation {
    condition = !(var.enable_edge || var.enable_control) || try(
      can(regex("^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(\\.\\d{3})?Z$", var.trial_started_at)) &&
      can(regex("^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(\\.\\d{3})?Z$", var.test_until)) &&
      timecmp(var.test_until, var.trial_started_at) > 0 &&
    timecmp(var.test_until, timeadd(var.trial_started_at, "1h")) <= 0, false)
    error_message = "An armed trial needs an explicit UTC window of at most one hour. The runtime also checks the current time."
  }
}
variable "lease_ttl_ms" {
  type    = number
  default = 60000
  validation {
    condition     = var.lease_ttl_ms >= 1 && var.lease_ttl_ms <= 60000 && floor(var.lease_ttl_ms) == var.lease_ttl_ms
    error_message = "Lease TTL must be an integer between 1 and 60000 milliseconds."
  }
}
variable "csrs" {
  type    = map(string)
  default = {}
  validation {
    condition = !var.provision || (
      toset(keys(var.csrs)) == toset(["registered", "unregistered"]) &&
      alltrue([for csr in values(var.csrs) : can(regex("^-----BEGIN CERTIFICATE REQUEST-----\\s+[A-Za-z0-9+/=\\r\\n]+\\s+-----END CERTIFICATE REQUEST-----\\s*$", csr))])
    )
    error_message = "Provide exactly two CSR PEMs, never private keys. Cryptographic CSR validity must be checked before approval."
  }
}
