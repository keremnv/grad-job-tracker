variable "profile" {
  type    = string
  default = "grad-jobwatch"
}
variable "region" {
  type    = string
  default = "eu-north-1"
  validation {
    condition     = var.region == "eu-north-1"
    error_message = "This project is restricted to its selected Stockholm Region."
  }
}
variable "expected_project_id" {
  description = "Supply privately; protects against planning/applying to the wrong AWS project."
  type        = string
  sensitive   = true
  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_project_id))
    error_message = "A twelve-digit project identifier is required."
  }
}
variable "ami_id" {
  description = "Explicit reviewed Canonical Ubuntu 24.04 ARM64 AMI; never follow latest automatically."
  type        = string
  validation {
    condition     = can(regex("^ami-[0-9a-f]{17}$", var.ami_id))
    error_message = "Pin an explicit AMI identifier."
  }
}
variable "availability_zone" {
  type    = string
  default = "eu-north-1a"
  validation {
    condition     = can(regex("^eu-north-1[a-c]$", var.availability_zone))
    error_message = "Use a Stockholm availability zone."
  }
}
variable "instance_type" {
  type    = string
  default = "t4g.small"
  validation {
    condition     = contains(["t4g.small", "t4g.micro"], var.instance_type)
    error_message = "Use the reviewed ARM64 burstable sizes."
  }
}
variable "data_gib" {
  type    = number
  default = 16
  validation {
    condition     = var.data_gib >= 16 && var.data_gib <= 64 && floor(var.data_gib) == var.data_gib
    error_message = "Use 16–64 GiB; growth requires a new cost review and filesystem expansion."
  }
}
variable "owner_tag" {
  type    = string
  default = "personal-tracker"
}

# Supplied only through private operator inputs. Never commit an email address.
variable "backup_notification_email" {
  type      = string
  sensitive = true
  nullable  = true
  default   = null
  validation {
    condition     = var.backup_notification_email == null ? true : can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.backup_notification_email))
    error_message = "Supply the owner-confirmed email privately, or leave subscription absent."
  }
}
