terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  backend "s3" {
    bucket       = "drysg-tfstate-637107289229"
    key          = "drysg/terraform.tfstate"
    region       = "ap-southeast-1"
    use_lockfile = true
  }
}