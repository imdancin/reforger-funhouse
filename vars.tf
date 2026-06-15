# Non-sensitive Terraform variables — values set in terraform.tfvars

variable "instance_count" {
  type        = number
  default     = 0
  description = "Set to 1 to boot the game server, set to 0 to tear it down"
}

variable "enable_custom_dns" {
  type        = bool
  default     = true
  description = "Set to true if you have an active Route 53 Hosted Zone ready. Set to false to bypass DNS creation."
}

variable "domain_name" {
  type        = string
  description = "The registered domain name managed by Route 53 (e.g. imdancin.com)"
}

variable "data_volume_size" {
  type        = number
  default     = 20
  description = "Size in GB for the persistent game data EBS volume (can be increased, never decreased)"
}

variable "legacy_volume_id" {
  type        = string
  default     = ""
  description = "Volume ID of the old root volume to temporarily attach for data recovery. Set empty to skip."
}

variable "active_scenario" {
  type        = string
  default     = "values-freedomfighters.yaml"
  description = "Helm values file in cluster-manifests/ that defines the active game scenario"
}

variable "ssh_allowed_cidr" {
  description = "IPv4 CIDR block to allow SSH access from (e.g., 203.0.113.5/32). Leave empty to disable SSH ingress."
  type        = string
  default     = ""

  validation {
    condition     = var.ssh_allowed_cidr == "" || can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}/([0-9]|[12][0-9]|3[0-2])$", var.ssh_allowed_cidr))
    error_message = "ssh_allowed_cidr must be either an empty string or a valid IPv4 CIDR block (e.g., 203.0.113.5/32)."
  }
}
