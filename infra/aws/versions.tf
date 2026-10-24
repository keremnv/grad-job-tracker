terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
  # Local state deliberately: no remote-state bucket is bootstrapped by init.
}
provider "aws" {
  region              = var.region
  profile             = var.profile
  allowed_account_ids = [var.expected_project_id]
  default_tags {
    tags = {
      Project     = "graduate-job-watch"
      Environment = "preproduction"
      ManagedBy   = "terraform"
      Owner       = var.owner_tag
    }
  }
}
