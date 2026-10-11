variable "env" {
  description = "Environment name, used to prefix resource names (e.g. drysg-dev-AreaSnapshot)"
  type        = string
  default     = "dev"
}

locals {
  prefix = "drysg-${var.env}"
}

variable "ingest_schedule_enabled" {
  description = "Run the ingest Lambda every 5 minutes. Set false to deploy and test it manually first"
  type        = bool
  default     = true
}

variable "ingest_reserved_concurrency" {
  description = "Reserved concurrency for ingest (1 prevents overlapping runs). Use -1 if the account concurrency limit is only 10"
  type        = number
  default     = 1
}
