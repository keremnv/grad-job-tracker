terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
  # Bootstrap this separately; validation/tests use init -backend=false.
  backend "s3" {
    key                  = "tracker/terraform.tfstate"
    region               = "eu-north-1"
    profile              = "grad-jobwatch"
    encrypt              = true
    use_lockfile         = true
    workspace_key_prefix = "no-workspaces"
  }
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
