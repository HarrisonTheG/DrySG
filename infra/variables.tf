variable "env" {
  description = "Environment name, used to prefix resource names (e.g. drysg-dev-AreaSnapshot)"
  type        = string
  default     = "dev"
}

locals {
  prefix = "drysg-${var.env}"
}
